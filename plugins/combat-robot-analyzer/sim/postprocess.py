#!/usr/bin/env python3
"""Extract results from a finished OpenRadioss run.

Reads the engine .out file (the one output that is always present and always
text), writes the global history as CSV, optionally plots it, and converts the
animation files to VTK for ParaView.

Also reports the checks worth looking at before believing a result: energy
drift, added mass from timestep control, and whether the run reached the
requested end time.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

# Columns after the "ERROR" percentage in the engine's cycle table.
TRAILING = ["i_energy", "k_energy_t", "k_energy_r", "ext_work",
            "mass_err", "total_mass", "mass_added"]

NUM = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([EeDd][-+]?\d+)?$")


def _float(tok):
    tok = tok.replace("D", "E").replace("d", "e")
    try:
        return float(tok)
    except ValueError:
        return None


def parse_out(path):
    """Pull the cycle/energy table out of an engine .out file."""
    rows = []
    with open(path, errors="replace") as fh:
        lines = fh.readlines()

    for line in lines:
        toks = line.split()
        if len(toks) < 9 or not toks[0].isdigit():
            continue
        # Every data row carries the error as a percentage; it separates the
        # element identification from the numeric columns.
        pct = next((i for i, t in enumerate(toks) if t.endswith("%")), None)
        if pct is None or pct + len(TRAILING) >= len(toks) + 1:
            continue
        values = [_float(t) for t in toks[pct + 1: pct + 1 + len(TRAILING)]]
        if len(values) != len(TRAILING) or any(v is None for v in values):
            continue
        cycle, time, dt = toks[0], _float(toks[1]), _float(toks[2])
        if time is None or dt is None:
            continue
        row = {
            "cycle": int(cycle),
            "time": time,
            "timestep": dt,
            "element": " ".join(toks[3:pct - 1]) or None,
            "error_pct": _float(toks[pct].rstrip("%")),
        }
        row.update(dict(zip(TRAILING, values)))
        rows.append(row)
    return rows, lines


def summarise(rows, lines, out_path):
    text = "".join(lines)
    normal = "NORMAL TERMINATION" in text
    cycles = re.search(r"TOTAL NUMBER OF CYCLES\s*:\s*(\d+)", text)
    elapsed = re.search(r"ELAPSED TIME\s*=\s*([\d.]+)", text)

    summary = {
        "out_file": os.path.abspath(out_path),
        "normal_termination": normal,
        "cycles": int(cycles.group(1)) if cycles else None,
        "elapsed_seconds": float(elapsed.group(1)) if elapsed else None,
        "samples": len(rows),
    }

    if rows:
        first, last = rows[0], rows[-1]
        total0 = (first["i_energy"] or 0) + (first["k_energy_t"] or 0) + (first["k_energy_r"] or 0)
        total1 = (last["i_energy"] or 0) + (last["k_energy_t"] or 0) + (last["k_energy_r"] or 0)
        # The solver's energy balance: internal + kinetic should track the work
        # put in. Drift means hourglassing, contact noise or a timestep problem.
        reference = max(abs(total0), abs(last["ext_work"] or 0), 1e-30)
        drift = (total1 - total0 - (last["ext_work"] or 0)) / reference
        added = last["mass_added"] or 0.0
        total_mass = last["total_mass"] or 0.0

        summary["end_time"] = last["time"]
        summary["timestep"] = {"initial": first["timestep"], "final": last["timestep"],
                               "minimum": min(r["timestep"] for r in rows)}
        summary["energy"] = {
            "internal_final": last["i_energy"],
            "kinetic_final": last["k_energy_t"],
            "external_work_final": last["ext_work"],
            "drift_fraction": drift,
        }
        summary["mass"] = {
            "total": total_mass,
            "added": added,
            "added_fraction": (added / total_mass) if total_mass else None,
        }

        checks = []
        if not normal:
            checks.append("run did not reach NORMAL TERMINATION")
        if abs(drift) > 0.05:
            checks.append(f"energy drift {drift:+.1%} exceeds 5% — suspect "
                          "hourglassing, contact instability or too coarse a timestep")
        if total_mass and added / total_mass > 0.02:
            checks.append(f"added mass is {added / total_mass:.1%} of the model — "
                          "timestep control is distorting the dynamics")
        summary["warnings"] = checks

    return summary


def write_csv(rows, path):
    if not rows:
        return None
    cols = ["cycle", "time", "timestep", "error_pct"] + TRAILING
    with open(path, "w") as fh:
        fh.write(",".join(cols) + "\n")
        for r in rows:
            fh.write(",".join("" if r.get(c) is None else str(r.get(c)) for c in cols) + "\n")
    return os.path.abspath(path)


def write_plot(rows, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # plotting is a convenience, not a requirement
        return None, f"matplotlib unavailable: {exc}"

    t = [r["time"] for r in rows]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 7), sharex=True)
    ax1.plot(t, [r["i_energy"] for r in rows], label="internal")
    ax1.plot(t, [r["k_energy_t"] for r in rows], label="kinetic")
    ax1.plot(t, [r["ext_work"] for r in rows], label="external work")
    ax1.set_ylabel("energy")
    ax1.legend()
    ax1.grid(alpha=0.3)
    ax1.set_title(os.path.basename(path))

    ax2.plot(t, [r["timestep"] for r in rows], color="tab:red")
    ax2.set_ylabel("timestep")
    ax2.set_xlabel("time")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return os.path.abspath(path), None


def convert_animations(run_dir, run_name, vtk_dir):
    """Turn Annn animation files into .vtk using the bundled converter."""
    exe = shutil.which("anim_to_vtk_linux64_gf")
    if not exe and os.environ.get("OPENRADIOSS_PATH"):
        cand = os.path.join(os.environ["OPENRADIOSS_PATH"], "exec", "anim_to_vtk_linux64_gf")
        exe = cand if os.path.isfile(cand) else None
    if not exe:
        return [], "anim_to_vtk not on PATH; skipped animation conversion"

    frames = sorted(glob.glob(os.path.join(run_dir, f"{run_name}A[0-9][0-9][0-9]")))
    if not frames:
        return [], "no animation files found"

    os.makedirs(vtk_dir, exist_ok=True)
    written = []
    for frame in frames:
        target = os.path.join(vtk_dir, os.path.basename(frame) + ".vtk")
        with open(target, "w") as fh:
            proc = subprocess.run([exe, frame], stdout=fh,
                                  stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0 or os.path.getsize(target) == 0:
            os.unlink(target)
            return written, f"anim_to_vtk failed on {os.path.basename(frame)}: {proc.stderr.strip()}"
        written.append(os.path.abspath(target))
    return written, None


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="postprocess.py",
        description="Summarise an OpenRadioss run and export its results",
    )
    p.add_argument("out_file", help="engine output file (<name>_0001.out)")
    p.add_argument("--csv", help="write the cycle history here (default: alongside the run)")
    p.add_argument("--plot", nargs="?", const="", help="write an energy/timestep plot (PNG)")
    p.add_argument("--vtk", nargs="?", const="", help="convert animation frames to VTK in this directory")
    p.add_argument("--json", dest="json_out", help="also write the summary here")
    args = p.parse_args(argv)

    if not os.path.isfile(args.out_file):
        sys.exit(f"postprocess: no such file: {args.out_file}")

    run_dir = os.path.dirname(os.path.abspath(args.out_file)) or "."
    run_name = re.sub(r"_0001\.out$", "", os.path.basename(args.out_file))

    rows, lines = parse_out(args.out_file)
    summary = summarise(rows, lines, args.out_file)
    notes = []

    if rows:
        csv_path = args.csv or os.path.join(run_dir, f"{run_name}_history.csv")
        summary["csv"] = write_csv(rows, csv_path)
    else:
        notes.append("no cycle table found in the .out file; lower /PRINT in the "
                     "engine deck (control.print_every) to record more rows")

    if args.plot is not None and rows:
        png = args.plot or os.path.join(run_dir, f"{run_name}_history.png")
        path, err = write_plot(rows, png)
        if path:
            summary["plot"] = path
        if err:
            notes.append(err)

    if args.vtk is not None:
        vtk_dir = args.vtk or os.path.join(run_dir, "vtk")
        files, err = convert_animations(run_dir, run_name, vtk_dir)
        if files:
            summary["vtk"] = {"directory": os.path.abspath(vtk_dir), "frames": len(files)}
        if err:
            notes.append(err)

    if notes:
        summary.setdefault("notes", []).extend(notes)

    text = json.dumps(summary, indent=2)
    if args.json_out:
        with open(args.json_out, "w") as fh:
            fh.write(text + "\n")
    print(text)
    return 0 if summary.get("normal_termination") else 1


if __name__ == "__main__":
    sys.exit(main())
