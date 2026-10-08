#!/usr/bin/env python3
"""Assemble a comprehensive simulation report context.

Collects the simulation config, pipeline results, and VTK-derived analysis
into one JSON structure designed for Claude to interpret and summarize.

    report.py BUILD_DIR                   # print analysis context as JSON
    report.py BUILD_DIR --text            # print a data-driven text report
    report.py BUILD_DIR --config spec.yaml  # include the simulation config

The output includes:
  - simulation setup (materials, mesh, constraints, contact)
  - quality checks (energy balance, mass scaling, termination)
  - per-frame evolution of strain, stress, velocity, displacement
  - spatial distribution and hotspot locations at the final frame
  - per-part element counts and damage breakdown
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import math
import os
import sys

import yaml

import extract_results as er


def locate_artefacts(build_dir, prefix=None):
    """Find the pipeline artefacts in a build directory."""
    art = {}

    # VTK frames
    vtk_dir = os.path.join(build_dir, "vtk")
    if os.path.isdir(vtk_dir):
        art["vtk_dir"] = vtk_dir
        art["vtk_files"] = sorted(glob.glob(os.path.join(vtk_dir, "*.vtk")))
    else:
        # maybe they pointed directly at the vtk dir
        files = sorted(glob.glob(os.path.join(build_dir, "*.vtk")))
        if files:
            art["vtk_dir"] = build_dir
            art["vtk_files"] = files
    if prefix and "vtk_files" in art:
        art["vtk_files"] = [f for f in art["vtk_files"]
                            if os.path.basename(f).startswith(prefix)]

    # When a prefix is given, filter all artefacts to that prefix.
    def _first(pattern):
        matches = sorted(glob.glob(os.path.join(build_dir, pattern)))
        if prefix:
            matches = [m for m in matches
                       if os.path.basename(m).startswith(prefix)]
        return matches[0] if matches else None

    # Pipeline result JSON (written by pipeline.py --json)
    art["pipeline_json"] = _first("*_pipeline.json")

    # History CSV
    art["history_csv"] = _first("*_history.csv")

    # Energy plot
    art["energy_plot"] = _first("*_history.png")

    # Starter deck
    art["starter"] = _first("*_0000.rad")

    # Config YAML — look for the spec that drove this run
    art["config"] = _first("*.yaml")

    # Clean None values
    art = {k: v for k, v in art.items() if v is not None}

    return art


def read_pipeline_json(path):
    """Read the combined pipeline report if available."""
    if not path or not os.path.isfile(path):
        return None
    with open(path) as fh:
        return json.load(fh)


def read_config(path):
    """Read the simulation spec YAML."""
    if not path or not os.path.isfile(path):
        return None
    with open(path) as fh:
        return yaml.safe_load(fh)


def assemble_context(build_dir, config_path=None, thresholds=None,
                     hotspot_count=6, band_size=10.0, axis="r",
                     prefix=None):
    """Collect everything into one analysis context dict."""
    if thresholds is None:
        thresholds = [0.02, 0.06, 0.12]

    artefacts = locate_artefacts(build_dir, prefix=prefix)
    vtk_files = artefacts.get("vtk_files", [])

    ctx = {"build_dir": os.path.abspath(build_dir)}

    # --- simulation config ---
    cfg_path = config_path or artefacts.get("config")
    cfg = read_config(cfg_path)
    if cfg:
        ctx["config"] = {
            "name": cfg.get("name"),
            "units": cfg.get("units", "mm_Mg_s"),
            "mesh": cfg.get("mesh"),
            "materials": cfg.get("materials"),
            "contact": cfg.get("contact"),
            "end_time": (cfg.get("control") or {}).get("end_time"),
            "parts_spec": cfg.get("parts"),
            "boundary_conditions": cfg.get("boundary_conditions"),
            "initial_velocity": cfg.get("initial_velocity"),
        }
        # Clean out None values
        ctx["config"] = {k: v for k, v in ctx["config"].items() if v is not None}

    # --- pipeline quality checks ---
    pipeline = read_pipeline_json(artefacts.get("pipeline_json"))
    if pipeline:
        results = pipeline.get("results", {})
        ctx["quality"] = {
            "normal_termination": results.get("normal_termination"),
            "energy": results.get("energy"),
            "mass": results.get("mass"),
            "timestep": results.get("timestep"),
            "warnings": results.get("warnings"),
        }
        ctx["quality"] = {k: v for k, v in ctx["quality"].items() if v is not None}

        deck = pipeline.get("deck", {})
        if deck:
            ctx["deck_summary"] = {
                k: deck[k] for k in ("elements", "nodes", "parts", "materials",
                                      "contact", "failure", "warnings")
                if k in deck
            }

        mesh = pipeline.get("mesh", {})
        if mesh:
            ctx["mesh_summary"] = {}
            for k in ("element_total", "element", "nodes", "elements",
                      "order", "target_size", "parts"):
                if k in mesh:
                    ctx["mesh_summary"][k] = mesh[k]

    # --- VTK frame-by-frame analysis ---
    if vtk_files:
        ctx["frame_count"] = len(vtk_files)
        ctx["parts"] = er.part_summary(vtk_files[0])

        # Per-frame summaries
        summaries = []
        for fp in vtk_files:
            s = er.frame_summary(fp, part_filter=None, thresholds=thresholds)
            if s:
                summaries.append(s)
        ctx["frames"] = summaries

        # Last-frame detailed analysis
        last = vtk_files[-1]
        ctx["hotspots"] = er.hotspot_analysis(last, part_filter=None,
                                              top_n=hotspot_count)
        ctx["spatial"] = er.spatial_distribution(
            last, part_filter=None,
            strain_threshold=thresholds[0] if thresholds else 0.02,
            band_size=band_size, axis=axis)

        # Per-part breakdown at last frame
        pts, cells, ct, pd, cd, fd = er.read_vtk(last)
        pid_list = cd.get("PART_ID")
        _, strain = er.resolve_strain_field(cd)
        _, stress = er.resolve_stress_field(cd)
        _, damage = er.resolve_damage_field(cd)
        _, status = er.resolve_element_status(cd)
        if pid_list and strain:
            part_ids = sorted(set(int(p) for p in pid_list))
            part_breakdown = {}
            for pid in part_ids:
                indices = [i for i, p in enumerate(pid_list) if int(p) == pid]
                eps = [strain[i] for i in indices]
                entry = {
                    "elements": len(indices),
                    "max_plastic_strain": max(eps),
                    "mean_plastic_strain": sum(eps) / len(eps),
                }
                if thresholds:
                    entry["above_threshold"] = {
                        str(t): sum(1 for e in eps if e > t) for t in thresholds
                    }
                if stress:
                    vm = [stress[i] for i in indices]
                    entry["max_von_mises"] = max(vm)
                    entry["mean_von_mises"] = sum(vm) / len(vm)
                if damage:
                    dam = [damage[i] for i in indices]
                    max_d = max(dam)
                    if max_d > 0:
                        entry["max_damage"] = max_d
                        entry["damaged_elements"] = sum(1 for d in dam if d > 0)
                if status:
                    eroded = sum(1 for i in indices if float(status[i]) < 0.5)
                    if eroded > 0:
                        entry["eroded_elements"] = eroded
                part_breakdown[pid] = entry
            ctx["per_part"] = part_breakdown

    return ctx


def text_report(ctx):
    """Generate a data-driven plain-text report from the analysis context."""
    lines = []

    def heading(s):
        lines.append("")
        lines.append(s)
        lines.append("=" * len(s))

    def subheading(s):
        lines.append("")
        lines.append(s)
        lines.append("-" * len(s))

    name = (ctx.get("config") or {}).get("name", "Simulation")
    lines.append(f"SIMULATION RESULTS — {name}")
    lines.append("=" * (24 + len(name)))

    # --- Setup ---
    cfg = ctx.get("config")
    if cfg:
        heading("1. Simulation Setup")
        lines.append(f"  Units: {cfg.get('units', 'mm_Mg_s')} (mm, tonne, second → MPa, N)")
        mesh = cfg.get("mesh", {})
        if mesh:
            lines.append(f"  Element type: {mesh.get('element', '?')}")
            lines.append(f"  Target edge size: {mesh.get('size', '?')} mm")
        end_time = cfg.get("end_time")
        if end_time:
            lines.append(f"  Simulation end time: {end_time:.2e} s")
        mats = cfg.get("materials", {})
        for mname, mdef in mats.items():
            law = mdef.get("law", "?")
            rho = mdef.get("density", "?")
            E = mdef.get("young", "?")
            lines.append(f"  Material '{mname}': {law}, ρ={rho}, E={E} MPa")
            if "yield" in mdef:
                lines.append(f"    yield={mdef['yield']} MPa (J-C A), "
                             f"B={mdef.get('hardening_b', '?')}, "
                             f"n={mdef.get('hardening_n', '?')}, "
                             f"ε_max={mdef.get('eps_max', '?')}")
        ct = cfg.get("contact")
        if ct:
            if isinstance(ct, dict):
                lines.append(f"  Contact: TYPE7, friction={ct.get('friction', 0)}")
            else:
                lines.append(f"  Contact: {ct}")
        iv = cfg.get("initial_velocity")
        if iv:
            for v in (iv if isinstance(iv, list) else [iv]):
                vec = v.get("vector", [])
                vmag = math.sqrt(sum(c ** 2 for c in vec)) if vec else 0
                lines.append(f"  Initial velocity on '{v.get('set', '?')}': "
                             f"{vec} mm/s ({vmag:.0f} mm/s = {vmag / 1000:.1f} m/s)")

    # --- Failure/Erosion models ---
    ds = ctx.get("deck_summary", {})
    fail = ds.get("failure")
    if fail:
        subheading("Failure / Element Erosion")
        lines.append(f"  Active: {fail.get('count', 0)} failure criteria")
        for e in fail.get("entries", []):
            lines.append(f"    Part '{e.get('part', '?')}' "
                         f"(material '{e.get('material', '?')}'): "
                         f"/FAIL/{e.get('model', '?').upper()}")
        # Also report failure params from config YAML
        mats = cfg.get("materials", {}) if cfg else {}
        for mname, mdata in mats.items():
            fblock = mdata.get("failure") if isinstance(mdata, dict) else None
            if fblock:
                model = fblock.get("model", "?")
                params = {k: v for k, v in fblock.items() if k != "model"}
                if params:
                    lines.append(f"    {mname} failure params: "
                                 + ", ".join(f"{k}={v}" for k, v in params.items()))

    # --- Mesh ---
    ms = ctx.get("mesh_summary")
    if ms:
        subheading("Mesh")
        elems = ms.get("elements", {})
        elem_desc = ", ".join(f"{v} {k}" for k, v in elems.items()) if elems else "?"
        lines.append(f"  Elements: {ms.get('element_total', '?')} ({elem_desc})")
        lines.append(f"  Nodes: {ms.get('nodes', '?')}")
        lines.append(f"  Element type: {ms.get('element', '?')}, order {ms.get('order', '?')}")
        lines.append(f"  Target size: {ms.get('target_size', '?')} mm")

    # --- Quality checks ---
    q = ctx.get("quality")
    if q:
        heading("2. Quality Checks")
        term = q.get("normal_termination")
        if term is not None:
            status = "NORMAL TERMINATION ✓" if term else "ABNORMAL TERMINATION ✗"
            lines.append(f"  Termination: {status}")
        energy = q.get("energy", {})
        if energy:
            drift = float(energy.get("drift_fraction", 0))
            pct = abs(drift * 100)
            verdict = "ACCEPTABLE ✓" if pct < 5 else ("MARGINAL ⚠" if pct < 10 else "HIGH ✗")
            lines.append(f"  Energy drift: {drift:+.4f} ({pct:.1f}%) — {verdict}")
            ie = energy.get("internal")
            ke = energy.get("kinetic")
            ce = energy.get("contact")
            if ie is not None:
                lines.append(f"    Internal: {float(ie):.4g}")
            if ke is not None:
                lines.append(f"    Kinetic:  {float(ke):.4g}")
            if ce is not None:
                lines.append(f"    Contact:  {float(ce):.4g}")
        mass = q.get("mass", {})
        if mass:
            added = float(mass.get("added_fraction", 0))
            verdict = "OK ✓" if added < 0.02 else "HIGH ✗"
            lines.append(f"  Added mass: {added * 100:.2f}% — {verdict}")
        ts = q.get("timestep", {})
        if ts:
            tsmin = ts.get("minimum")
            if tsmin is not None:
                lines.append(f"  Min timestep: {float(tsmin):.3e} s")
        warns = q.get("warnings", [])
        for w in warns:
            lines.append(f"  ⚠ {w}")

    # --- Frame evolution ---
    frames = ctx.get("frames", [])
    if frames:
        heading("3. Results Evolution")
        lines.append(f"  {len(frames)} animation frames")
        first = frames[0]
        last = frames[-1]
        lines.append(f"  Time range: {first.get('time', 0):.3e} → {last.get('time', 0):.3e} s")
        lines.append(f"  Final max plastic strain: {last['max_plastic_strain']:.5f}")
        lines.append(f"  Final mean plastic strain: {last['mean_plastic_strain']:.6f}")
        if "max_von_mises" in last:
            lines.append(f"  Final max von Mises stress: {last['max_von_mises']:.1f} MPa")
        if "max_displacement" in last:
            lines.append(f"  Final max displacement: {last['max_displacement']:.3f} mm")
        if "max_velocity" in last:
            lines.append(f"  Final max velocity: {last['max_velocity']:.0f} mm/s")

        # Threshold counts at the last frame
        above = last.get("above_threshold", {})
        total = last.get("elements", 0)
        if above and total:
            subheading("Strain distribution (last frame)")
            for t, count in sorted(above.items(), key=lambda x: float(x[0])):
                pct = count / total * 100
                lines.append(f"  εp > {float(t):.0%}: {count} elements ({pct:.1f}%)")

    # --- Per-part breakdown ---
    pp = ctx.get("per_part")
    if pp:
        heading("4. Per-Part Breakdown (last frame)")
        for pid, info in sorted(pp.items()):
            lines.append(f"  Part {pid}: {info['elements']} elements")
            lines.append(f"    max εp = {info['max_plastic_strain']:.5f}  "
                         f"mean εp = {info['mean_plastic_strain']:.6f}")
            if "max_von_mises" in info:
                lines.append(f"    max σ_vM = {info['max_von_mises']:.1f} MPa  "
                             f"mean σ_vM = {info['mean_von_mises']:.1f} MPa")
            if "max_damage" in info:
                lines.append(f"    max damage = {info['max_damage']:.4f}  "
                             f"damaged elements = {info.get('damaged_elements', 0)}")
            if "eroded_elements" in info:
                lines.append(f"    ERODED: {info['eroded_elements']} elements deleted")

    # --- Hotspots ---
    spots = ctx.get("hotspots", [])
    if spots:
        heading("5. Hotspot Elements (last frame)")
        lines.append(f"  Top {len(spots)} elements by plastic strain:")
        for s in spots:
            c = s["centroid"]
            line = (f"    εp={s['plastic_strain']:.5f}"
                    f"  σ_vM={s.get('von_mises', 0):7.1f} MPa"
                    f"  at ({c[0]:.1f}, {c[1]:.1f}, {c[2]:.2f}) mm")
            if "part_id" in s:
                line += f"  [part {s['part_id']}]"
            lines.append(line)

    # --- Spatial distribution ---
    spatial = ctx.get("spatial", {})
    if spatial and spatial.get("total_above", 0) > 0:
        heading("6. Spatial Distribution of Yielded Elements")
        axis = spatial.get("axis", "r")
        axis_label = {"r": "radius", "x": "x", "y": "y", "z": "z"}.get(axis, axis)
        thresh = spatial.get("threshold", 0.02)
        bs = int(spatial.get("band_size", 10))
        lines.append(f"  Elements with εp > {thresh} grouped by {axis_label}:")
        lines.append(f"  Total yielded: {spatial['total_above']}")
        for b, count in sorted(spatial.get("bands", {}).items()):
            lines.append(f"    {axis_label} = {b:3d}–{b + bs:3d} mm: {count} elements")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(
        prog="report.py",
        description="Assemble a comprehensive simulation analysis report",
    )
    p.add_argument("build_dir", help="build directory (or vtk/ subdirectory)")
    p.add_argument("--config", "-c", help="simulation spec YAML (auto-detected if not given)")
    p.add_argument("--prefix", help="only read VTK files whose basename starts with this "
                   "(e.g. 'bracket_impact' when vtk/ has multiple runs)")
    p.add_argument("--text", action="store_true",
                   help="print a text report (default: JSON context for Claude)")
    p.add_argument("--hotspots", "-n", type=int, default=6,
                   help="number of hotspot elements to report (default: 6)")
    p.add_argument("--band-size", type=float, default=10.0,
                   help="spatial distribution bin width (default: 10 mm)")
    p.add_argument("--axis", choices=["r", "x", "y", "z"], default="r",
                   help="spatial axis for distribution (default: r)")
    p.add_argument("--threshold", "-t", action="append", type=float,
                   help="strain threshold (repeatable; default: 0.02, 0.06, 0.12)")
    args = p.parse_args(argv)

    thresholds = args.threshold or [0.02, 0.06, 0.12]

    ctx = assemble_context(
        args.build_dir,
        config_path=args.config,
        thresholds=thresholds,
        hotspot_count=args.hotspots,
        band_size=args.band_size,
        axis=args.axis,
        prefix=args.prefix,
    )

    if args.text:
        print(text_report(ctx))
    else:
        print(json.dumps(ctx, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
