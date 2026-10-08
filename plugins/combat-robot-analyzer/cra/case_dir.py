"""A case directory: one robot, its setup, and everything built from it.

Laid out like a web-app session so the sim scripts' relative paths work
unchanged:

    CASE/model.step  CASE/report/  CASE/setup.json  CASE/build/...

setup.json is the schema documented in sim/case_cli.py.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

PLUGIN = Path(__file__).resolve().parents[1]
SIM = PLUGIN / "sim"
LIBRARY = SIM / "materials.yaml"

DEFAULTS: dict[str, Any] = {
    "step": "model.step",
    "report": "report/report.json",
    "geometry": "report/geometry.json",
    "target_id": None,
    "materials": {},
    "default_material": None,
    # plus any override keys (sim/case/impactor.py OVERRIDE_KEYS)
    "impactor": {"weight_class": None, "energy_level": "typical", "archetype": "vertical_drum"},
    "end_time": "auto",
    "up_axis": "auto",
    "mesh_size": 1.5,
    "exclude_span": True,
    "aim": None,
    "include": None,
    "exclude": [],
    "standins": True,
}

#: The setup as case_cli reads it (default_material expanded per part).
RESOLVED = "setup.resolved.json"


class CaseError(Exception):
    """A failure to report to the user as is."""


def load(case: Path) -> dict[str, Any]:
    p = case / "setup.json"
    stored = json.loads(p.read_text()) if p.is_file() else {}
    return {**DEFAULTS, **stored, "impactor": {**DEFAULTS["impactor"], **stored.get("impactor", {})}}


def save(case: Path, setup: dict[str, Any]) -> None:
    (case / "setup.json").write_text(json.dumps(setup, indent=2) + "\n")


def report(case: Path) -> dict[str, Any] | None:
    p = case / "report" / "report.json"
    return json.loads(p.read_text()) if p.is_file() else None


def resolved(case: Path, setup: dict[str, Any]) -> dict[str, Any]:
    """Every part given a material: default_material fills the unnamed ones."""
    mats = dict(setup["materials"])
    if setup.get("default_material"):
        for p in (report(case) or {}).get("parts") or []:
            mats.setdefault(p["id"], setup["default_material"])
    return {**setup, "materials": mats}


def missing(case: Path, setup: dict[str, Any]) -> list[str]:
    """What must still be decided before a build can run."""
    out = []
    rep = report(case)
    if rep is None:
        out.append("model not analysed yet (init)")
    if not setup.get("target_id"):
        out.append("target part (target_id)")
    if not setup["impactor"].get("weight_class"):
        out.append("opponent weight class (impactor.weight_class)")
    if rep is not None:
        mats = resolved(case, setup)["materials"]
        unassigned = [p["id"] for p in rep.get("parts") or [] if p["id"] not in mats]
        if unassigned:
            out.append(f"materials for {len(unassigned)} parts (materials or default_material)")
    return out


def write_resolved(case: Path) -> None:
    setup = load(case)
    gaps = missing(case, setup)
    if gaps:
        raise CaseError("setup incomplete: " + "; ".join(gaps))
    (case / RESOLVED).write_text(json.dumps(resolved(case, setup)))


def _json(stdout: str) -> Any:
    """The script's JSON: the whole of stdout (some indent it), or else its
    last line (some print progress first)."""
    text = stdout.strip()
    for candidate in (text, text.splitlines()[-1] if text else ""):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def sim(case: Path, script: str, *args: str, timeout: int = 1800) -> Any:
    """Run a sim script in the case directory; return its JSON stdout.

    Raises CaseError with the script's own error message when it reports one.
    """
    argv = (["bash", str(SIM / script)] if script.endswith(".sh")
            else ["python3", str(SIM / script)]) + list(args)
    env = {**os.environ, "PYTHONPATH": f"{SIM}:{os.environ.get('PYTHONPATH', '')}"}
    try:
        proc = subprocess.run(argv, cwd=case, env=env, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise CaseError(f"{script} took longer than {timeout} s") from exc
    if script.endswith(".sh"):
        if proc.returncode != 0:
            raise CaseError(f"{script} failed (exit {proc.returncode}): {proc.stderr[-2000:]}")
        return {"stdout": proc.stdout, "stderr": proc.stderr}
    out = _json(proc.stdout)
    if proc.returncode < 0:
        raise CaseError(f"{script} crashed (signal {-proc.returncode})")
    if proc.returncode != 0:
        msg = (out or {}).get("error") if isinstance(out, dict) else None
        raise CaseError(msg or f"{script} failed (exit {proc.returncode}): {proc.stderr[-1500:]}")
    return out
