"""Post-process a finished solve into numbers and files: energy plot, history
CSV, per-part damage, the target's wear map and an impact playback.

Ported from the web app's get_results tool. Unlike the web app it keeps the
raw solver frames (build/run/vtk), so post-processing can be redone.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .build import BUILD_RECORD
from .case_dir import CaseError, sim

RESULTS = "build/results"


def deck_parts(case: Path) -> dict[str, int]:
    """Deck part number by name (``p035``, ``fill_p026``, ``tooth_head``)."""
    parts: dict[str, int] = {}
    lines = (case / "build/run/strike_0000.rad").read_text(errors="replace").splitlines()
    for i, line in enumerate(lines[:-1]):
        m = re.match(r"^/PART/(\d+)\s*$", line)
        if m:
            parts[lines[i + 1].strip()] = int(m.group(1))
    return parts


def hit_progress(csv_path: Path) -> dict[str, Any]:
    """Was the target still absorbing energy when the run stopped? If internal
    energy grew more than 5% over the last tenth, the damage is a lower bound."""
    if not csv_path.is_file():
        return {"known": False}
    rows = []
    with csv_path.open() as fh:
        header = fh.readline().strip().split(",")
        for line in fh:
            vals = line.strip().split(",")
            if len(vals) == len(header):
                rows.append(dict(zip(header, (float(v) for v in vals))))
    if len(rows) < 5:
        return {"known": False}
    t_end = rows[-1]["time"]
    late = min(rows, key=lambda r: abs(r["time"] - 0.9 * t_end))
    ie_end, ie_late = rows[-1]["i_energy"], late["i_energy"]
    ke0 = rows[0]["k_energy_t"] + rows[0]["k_energy_r"]
    growth = (ie_end - ie_late) / ie_end if ie_end > 0 else 0.0
    return {
        "known": True,
        "still_absorbing": growth > 0.05,
        "internal_energy_growth_last_10pct": round(growth, 3),
        "absorbed_fraction_of_initial_energy": round(ie_end / ke0, 4) if ke0 > 0 else None,
    }


def quality(warnings: list[str], damage: dict[str, Any]) -> list[str]:
    """With erosion, deleted elements take their energy with them, so the
    energy 'drift' warning is expected rather than a defect."""
    if not damage.get("eroded_elements"):
        return warnings
    kept = [w for w in warnings if "energy drift" not in w]
    if len(kept) < len(warnings):
        kept.append("energy balance excludes eroded elements: material was deleted, so the "
                    "energy it held is gone from the totals (expected with erosion)")
    return kept


def results(case: Path) -> dict[str, Any]:
    if not (case / "build/run/strike_0001.out").is_file():
        raise CaseError("no finished solve; run solve first")
    record = case / BUILD_RECORD
    if not record.is_file():
        raise CaseError("no build record; run build and solve again")
    out_dir = case / RESULTS
    out_dir.mkdir(parents=True, exist_ok=True)
    post = sim(case, "postprocess.py", "build/run/strike_0001.out", "--vtk",
               "--plot", f"{RESULTS}/energy.png", "--csv", f"{RESULTS}/history.csv")
    damage = sim(case, "extract_damage.py", "build/run/vtk", "-o", f"{RESULTS}/damage.npz", "-q")
    target = json.loads(record.read_text())["target_id"]
    numbers = deck_parts(case)

    per_part = {}
    for name, number in numbers.items():
        st = sim(case, "wear_figure.py", f"{RESULTS}/damage.npz", "-p", str(number),
                 "-c", "build/strike.yaml", "--stats-only", "-q")
        per_part[name] = {k: st.get(k) for k in (
            "elements", "eroded_elements", "eroded_fraction", "peak_strain_alive",
            "mean_strain_alive", "peak_von_mises_alive", "mass_g", "mass_eroded_g")}
        per_part[name]["damage_clusters"] = len(st.get("clusters") or [])

    wear = None
    if target in numbers:
        wear = sim(case, "wear_figure.py", f"{RESULTS}/damage.npz", "-p", str(numbers[target]),
                   "-c", "build/strike.yaml", "-o", f"{RESULTS}/wear.svg", "-q",
                   "--title", f"Wear map, {target}")
        wear.pop("damage_npz", None)
        wear.pop("svg", None)
    robot = [str(n) for name, n in numbers.items() if not name.startswith("tooth_")]
    playback_note = None
    try:
        sim(case, "playback.py", "build/run/vtk", "-o", RESULTS, "-q",
            *(["--scale-parts", ",".join(robot)] if robot else []))
    except CaseError as exc:
        playback_note = str(exc)[:300]

    summary = {
        # The deck is mm-Mg-s: energies print as mJ (1.1e6 is 1,100 J).
        "units": {"length": "mm", "time": "s", "mass": "tonne (Mg)", "stress": "MPa",
                  "energy": "mJ", "force": "N", "velocity": "mm/s"},
        "termination": {k: post.get(k) for k in ("normal_termination", "cycles", "end_time")},
        "energy": post.get("energy"),
        "mass": post.get("mass"),
        "quality_warnings": quality(post.get("warnings", []), damage),
        "hit": hit_progress(out_dir / "history.csv"),
        "overall": {k: damage.get(k) for k in ("elements", "eroded_elements", "eroded_fraction",
                                                "peak_plastic_strain_alive", "peak_von_mises_alive")},
        "per_part": per_part,
        "target_wear": wear,
        "files": {
            "energy_plot": f"{RESULTS}/energy.png",
            "history_csv": f"{RESULTS}/history.csv",
            "wear_map": f"{RESULTS}/wear.svg" if wear else None,
            "playback": None if playback_note else f"{RESULTS}/playback.json",
        },
        **({"playback_unavailable": playback_note} if playback_note else {}),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary
