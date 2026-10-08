"""Build a case: place the weapon, scope, mesh, write the deck, have the
OpenRadioss starter check it, and estimate the solve time. Then solve it.

Ported from the web app's build_case and run_solve tools.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from .case_dir import RESOLVED, CaseError, sim, write_resolved

#: The engine runs at this x the starter's nodal timestep estimate.
TIMESTEP_SCALE = 0.9
#: Element-cycles per second per solver thread, measured on an i5-6500
#: (2026-10-08); a modern desktop core is roughly 1.5-2x this.
THROUGHPUT_PER_THREAD = 1.28e6
#: Above this estimate `solve` wants --allow-over-budget.
SOLVE_BUDGET_MIN = 45.0
#: What the last build produced, read back by results.
BUILD_RECORD = "build/build.json"
DECK = "build/run/strike_0000.rad"


def threads() -> int:
    return max(1, min(8, (os.cpu_count() or 2) // 2))


def starter_nodal_timestep(starter_out: Path) -> float | None:
    """The smallest entry of the starter's "NODAL TIME STEP (estimation)"
    table, which is what the engine runs at (x TIMESTEP_SCALE)."""
    if not starter_out.is_file():
        return None
    lines = starter_out.read_text(errors="replace").splitlines()
    for i, line in enumerate(lines):
        if "NODAL TIME STEP (estimation)" in line:
            for row in lines[i + 1:i + 8]:
                parts = row.split()
                if len(parts) == 2:
                    try:
                        return float(parts[0])
                    except ValueError:
                        continue
    return None


def estimate_minutes(elements: int, end_time: float, dt: float, nthreads: int) -> float:
    cycles = end_time / (dt * TIMESTEP_SCALE)
    return round(elements * cycles / (THROUGHPUT_PER_THREAD * nthreads) / 60.0, 1)


def build(case: Path) -> dict[str, Any]:
    write_resolved(case)
    shutil.rmtree(case / "build", ignore_errors=True)
    out = sim(case, "case_cli.py", "build", RESOLVED, "-o", "build")
    sim(case, "build_deck.py", "build/strike.msh", "-c", "build/strike.yaml", "-o", "build/run")
    try:
        sim(case, "run_sim.sh", DECK, "--check", timeout=600)
    except CaseError as exc:
        raise CaseError(f"the solver's starter rejected the deck: {exc}") from exc

    m = out["metrics"]
    dt = starter_nodal_timestep(case / "build/run/strike_0000.out") or m["dt_physical"]
    est = estimate_minutes(m["elements"], out["end_time"], dt, threads())
    (case / BUILD_RECORD).write_text(json.dumps({
        "target_id": out["target_id"], "scoped_ids": out["scoped_ids"],
        "end_time": out["end_time"], "weapon": out.get("weapon"), "up": out.get("up"),
        "estimated_solve_minutes": est, "threads": threads()}))
    return {
        "target_id": out["target_id"],
        "scoped_ids": out["scoped_ids"],
        "elements": m["elements"],
        "contact_time_us": round(out["end_time"] * 1e6, 1),
        "estimated_solve_minutes": est,
        "threads": threads(),
        **({"over_budget": f"estimated {est} min exceeds {SOLVE_BUDGET_MIN:.0f} min; offer a "
                           "larger mesh_size or shorter end_time, or solve with --allow-over-budget"}
           if est > SOLVE_BUDGET_MIN else {}),
        "weapon": out.get("weapon"),
        "impactor": out.get("impactor"),
        "up_axis": out.get("up"),
        "tooth": out["tooth"],
        "aim": out["aim"],
        "warnings": out["warnings"],
        "starter_check": "passed",
    }


def solve(case: Path, nthreads: int | None, allow_over_budget: bool) -> dict[str, Any]:
    record = case / BUILD_RECORD
    if not record.is_file():
        raise CaseError("no build to solve; run build first")
    rec = json.loads(record.read_text())
    if rec["estimated_solve_minutes"] > SOLVE_BUDGET_MIN and not allow_over_budget:
        raise CaseError(f"estimated {rec['estimated_solve_minutes']} min is over budget; "
                        "confirm with the user, then pass --allow-over-budget")
    shutil.rmtree(case / "build/results", ignore_errors=True)
    n = nthreads or threads()
    res = sim(case, "run_sim.sh", DECK, "-nt", str(n), timeout=6 * 3600)
    return {"threads": n, "log": (res["stdout"] + res["stderr"])[-1500:]}
