"""Change a case's setup the way the web app's update_setup tool does: only
the given keys change, values are checked against the model's parts and the
material library, and nothing is saved when any check fails.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .case_dir import LIBRARY, CaseError, load, missing, report, save, sim

END_TIME_RANGE = (1.0e-5, 5.0e-4)
MESH_SIZE_RANGE = (0.5, 5.0)
UP_AXES = ("auto", "x", "y", "z", "-x", "-y", "-z")
KEYS = ("target_id", "materials", "default_material", "impactor", "end_time", "up_axis",
        "mesh_size", "include", "exclude", "standins", "exclude_span", "aim", "clear_aim")
#: The impactor block case_cli resolves, written beside setup.json.
WEAPON_IN = "weapon.in.json"


def library() -> dict[str, Any]:
    return yaml.safe_load(LIBRARY.read_text())


def materials_table() -> list[dict[str, Any]]:
    out = []
    for key, m in library().items():
        if not isinstance(m, dict) or "density" not in m:
            continue
        out.append({"key": key, "name": m.get("name", key), "law": m.get("law"),
                    "density_g_cm3": round(float(m["density"]) * 1e9, 2),
                    "yield_mpa": m.get("yield"), "eps_max": m.get("eps_max")})
    return out


def weapon_card(case: Path, impactor: dict[str, Any]) -> dict[str, Any] | None:
    """Every resolved weapon number (case_cli.py weapon), or None before a
    weight class is chosen. Raises CaseError on a bad override."""
    if not impactor.get("weight_class"):
        return None
    (case / WEAPON_IN).write_text(json.dumps({"impactor": impactor}))
    return sim(case, "case_cli.py", "weapon", WEAPON_IN, timeout=120)


def update(case: Path, patch: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(patch) - set(KEYS))
    if unknown:
        raise CaseError(f"unknown setup key {', '.join(unknown)}; expected {', '.join(KEYS)}")
    setup = load(case)
    rep = report(case)
    part_ids = {p["id"] for p in (rep or {}).get("parts") or []}
    keys = {m["key"] for m in materials_table()}

    def need_part(pid: str) -> None:
        if rep is None:
            raise CaseError("analyse the model first (init)")
        if pid not in part_ids:
            raise CaseError(f"no part {pid!r} in this model")

    def need_material(key: str) -> None:
        if key not in keys:
            raise CaseError(f"unknown material {key!r}; see `robot-impact materials`")

    if "target_id" in patch:
        need_part(patch["target_id"])
        if patch["target_id"] != setup.get("target_id"):
            setup["aim"] = None  # an aim belongs to the part it was placed on
        setup["target_id"] = patch["target_id"]
    for pid, key in (patch.get("materials") or {}).items():
        need_part(pid)
        if key is None:
            setup["materials"].pop(pid, None)
        else:
            need_material(key)
            setup["materials"][pid] = key
    if "default_material" in patch:
        if patch["default_material"] is not None:
            need_material(patch["default_material"])
        setup["default_material"] = patch["default_material"]
    if "impactor" in patch:
        merged = {**setup["impactor"], **patch["impactor"]}
        # null clears an override back to its preset
        setup["impactor"] = {k: v for k, v in merged.items() if v is not None or k == "weight_class"}
    if "end_time" in patch:
        lo, hi = END_TIME_RANGE
        v = patch["end_time"]
        if v != "auto" and not (isinstance(v, (int, float)) and lo <= v <= hi):
            raise CaseError(f'end_time must be "auto" or within {lo:g}..{hi:g} s')
        setup["end_time"] = v
    if "up_axis" in patch:
        if patch["up_axis"] not in UP_AXES:
            raise CaseError(f"up_axis must be one of {', '.join(UP_AXES)}")
        setup["up_axis"] = patch["up_axis"]
    if "mesh_size" in patch:
        lo, hi = MESH_SIZE_RANGE
        if not lo <= patch["mesh_size"] <= hi:
            raise CaseError(f"mesh_size must be within {lo}..{hi} mm")
        setup["mesh_size"] = patch["mesh_size"]
    for key in ("include", "exclude"):
        if key in patch:
            for pid in patch[key] or []:
                need_part(pid)
            setup[key] = patch[key] if key == "include" else (patch[key] or [])
    for key in ("exclude_span", "standins"):
        if key in patch:
            setup[key] = bool(patch[key])
    if "aim" in patch:
        aim = patch["aim"]
        if aim is not None and not (isinstance(aim, dict) and len(aim.get("point") or []) == 3
                                    and len(aim.get("direction") or []) == 3):
            raise CaseError("aim must be {point: [x,y,z], direction: [x,y,z]} (mm, outward normal)")
        setup["aim"] = None if aim is None else {**aim, "human_placed": True}
    if patch.get("clear_aim"):
        setup["aim"] = None

    # Last, after every other check: it is the slow one.
    weapon = weapon_card(case, setup["impactor"]) if "impactor" in patch else None
    save(case, setup)
    out = {"setup": setup, "missing": missing(case, setup)}
    if weapon is not None:
        out["weapon"] = weapon
    return out
