#!/usr/bin/env python3
"""The sandbox entry point for load-case work: one subcommand per agent tool.

    case_cli.py report STEP -o DIR [--clash]   -> DIR/report.json, DIR/geometry.json
    case_cli.py scope  SETUP.json              -> the suggested part set
    case_cli.py weapon SETUP.json              -> the resolved weapon (impactor.weapon_card)
    case_cli.py build  SETUP.json -o DIR       -> DIR/<case>.msh, DIR/<case>.yaml

Each prints one JSON document on stdout and exits 0, or prints
``{"error": ...}`` and exits 1. A crash (gmsh segfaults on some CAD) ends the
process with a signal instead; the caller sees that as the exit status, which
is how the agent learns to retry ``build`` with ``"standins": false``.

SETUP.json is the session's analysis setup, owned by the service:

    {"step": "model.step", "report": "report/report.json",
     "geometry": "report/geometry.json", "target_id": "p012",
     "materials": {"p012": "aluminium_6061_t6", ...},
     "impactor": {"weight_class": "3lb", "energy_level": "typical",
                  "archetype": "vertical_drum",
                  ...any of impactor.OVERRIDE_KEYS, e.g. "rpm": 9000},
     "end_time": "auto" | 4e-05, "mesh_size": 1.5, "exclude_span": true,
     "up_axis": "auto" | "z" | "-y" | ...,
     "aim": {"point": [...], "direction": [...], "human_placed": true} | null,
     "include": ["p003", ...] | null, "exclude": [...], "standins": true}

Relative paths resolve against SETUP.json's directory.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from case.aim import grade_aim, load_surface, travel_mm
from case.cadstep import run_report
from case.impactor import spec_from_setup, weapon_card
from case.loadcase import build_case, resolve_end_time, suggest_scope
from case.models import Aim

LIBRARY = Path(__file__).resolve().parent / "materials.yaml"

# clash is ~90% of the report's time on a real bot (129 s of 142 s on
# inertial-v6) and nothing in a load case reads it; drawing is never used.
SKIP_ON_UPLOAD = ("clash", "drawing")


def _report(args: argparse.Namespace) -> dict[str, Any]:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    skip = ("drawing",) if args.clash else SKIP_ON_UPLOAD
    # step_tessellate names its file after the model, so it gets a directory
    # of its own: a model called report.step must not overwrite report.json.
    tess = out / "tessellate"
    tess.mkdir(exist_ok=True)
    doc = run_report(args.step, skip=skip, tessellate_dir=tess, timeout=args.timeout)
    (out / "report.json").write_text(json.dumps(doc))
    written = sorted(tess.glob("*.json"))
    if written:
        written[0].replace(out / "geometry.json")
    return {
        "parts": [
            {
                "id": p["id"],
                "name": str(p.get("name") or "")[:80],
                "volume_mm3": p.get("volume_mm3"),
                "bbox": p.get("bbox"),
                "solids": p.get("solids"),
            }
            for p in doc.get("parts") or []
        ],
        "bbox": doc.get("bbox"),
        "units": doc.get("units"),
        "seconds": doc.get("seconds"),
    }


def _load_setup(path: str) -> tuple[dict[str, Any], Path]:
    setup_path = Path(path).resolve()
    return json.loads(setup_path.read_text()), setup_path.parent


def _common(setup: dict[str, Any], base: Path) -> dict[str, Any]:
    report = json.loads((base / setup["report"]).read_text())
    geometry_path = base / setup.get("geometry", "report/geometry.json")
    return {
        "parts": report["parts"],
        "materials": setup["materials"],
        "library": yaml.safe_load(LIBRARY.read_text()),
        "target_id": setup["target_id"],
        "impactor": spec_from_setup(setup["impactor"]),
        # "auto": build_case estimates how long the hit lasts
        "end_time": None if setup.get("end_time") in (None, "auto") else float(setup["end_time"]),
        "exclude_span": bool(setup.get("exclude_span", True)),
        "aim": Aim.from_dict(setup["aim"]) if setup.get("aim") else None,
        "geometry": json.loads(geometry_path.read_text()) if geometry_path.is_file() else None,
    }


def _scope(args: argparse.Namespace) -> dict[str, Any]:
    setup, base = _load_setup(args.setup)
    common = _common(setup, base)
    # the same duration build will use, so the preview's radius matches it
    common["end_time"] = resolve_end_time(
        common["end_time"], common["impactor"], common["library"][common["materials"][common["target_id"]]])
    return suggest_scope(
        include=setup.get("include"),
        exclude=setup.get("exclude") or (),
        **common,
    )


class Refused(ValueError):
    """The setup cannot be built as it stands; the message says what to change."""


def _grade(common: dict[str, Any]) -> dict[str, Any] | None:
    """Check a placed aim lands on the target, unobstructed, within travel.

    v1 ran this as a submission gate: across its corpus an unchecked strike
    missed the material 46 times in 58. A blocking verdict refuses the build;
    a marginal one rides along as a warning.
    """
    aim, geometry, target = common["aim"], common["geometry"], common["target_id"]
    if aim is None:
        return None
    if geometry is None:
        raise Refused("no geometry.json for this model; run report first")
    try:
        surface = load_surface(geometry, target)
    except (KeyError, ValueError) as exc:
        raise Refused(f"{target} has no tessellated surface, so the aim cannot be checked") from exc
    verdict = grade_aim(
        aim.point,
        aim.direction,
        surface,
        travel=travel_mm(common["impactor"].v_tip_ms, resolve_end_time(
            common["end_time"], common["impactor"],
            common["library"][common["materials"][common["target_id"]]])),
        target_id=target,
        doc=geometry,
    )
    if verdict.blocks or verdict.status == "no_geometry":
        raise Refused(f"aim rejected ({verdict.status}): {verdict.message}")
    return {"status": verdict.status, "gap_mm": verdict.gap_mm, "message": verdict.message}


def _weapon(args: argparse.Namespace) -> dict[str, Any]:
    setup, _ = _load_setup(args.setup)
    return weapon_card(spec_from_setup(setup["impactor"]))


def _build(args: argparse.Namespace) -> dict[str, Any]:
    setup, base = _load_setup(args.setup)
    common = _common(setup, base)
    aim_grade = _grade(common)
    built = build_case(
        base / setup["step"],
        case=args.case,
        workdir=args.out,
        mesh_size=float(setup["mesh_size"]),
        with_standins=bool(setup.get("standins", True)),
        include=setup.get("include"),
        exclude=setup.get("exclude") or (),
        up_axis=setup.get("up_axis") or "auto",
        **common,
    )
    if built.metrics.unpriced_parts:
        # no usable density or wave speed: the solve would be meaningless
        raise Refused(
            "parts with no usable material (density or wave speed): "
            + ", ".join(built.metrics.unpriced_parts)
        )
    warnings = list(common["impactor"].warnings) + list(built.warnings)
    if aim_grade and aim_grade["status"] == "marginal":
        warnings.append(f"aim: {aim_grade['message']}")
    return {
        "case": built.case,
        "target_id": built.target_id,
        "radius_mm": built.radius,
        "scoped_ids": list(built.scoped_ids),
        "mesh": str(built.mesh_path),
        "spec": str(built.spec_path),
        "metrics": asdict(built.metrics),
        # v2's tooth is a rigid head on a spinning weapon (`weapon`); the
        # backing and impedance figures ToothGeometry also computes were v1's.
        "tooth": {
            "summary": (f"rigid S7 tooth head {built.tooth.head_width:.1f} x "
                        f"{built.tooth.head_thickness:.1f} mm, {built.tooth.head_length:.1f} mm "
                        f"long, with a {built.tooth.land_width:.1f} mm striking land"),
        },
        "aim": aim_grade,
        "end_time": built.end_time,
        "weapon": built.swing.to_dict() if built.swing else None,
        "impactor": weapon_card(common["impactor"]),
        "up": list(built.up) if built.up else None,
        "stage_seconds": built.stage_seconds,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="case_cli.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("report", help="analyse and tessellate an uploaded STEP")
    r.add_argument("step")
    r.add_argument("-o", "--out", required=True)
    r.add_argument("--clash", action="store_true", help="include the slow clash section")
    r.add_argument("--timeout", type=int, default=600)
    r.set_defaults(fn=_report)

    s = sub.add_parser("scope", help="the part set build would carry by default")
    s.add_argument("setup")
    s.set_defaults(fn=_scope)

    w = sub.add_parser("weapon", help="resolve the impactor block into every weapon number")
    w.add_argument("setup")
    w.set_defaults(fn=_weapon)

    b = sub.add_parser("build", help="place the tooth, scope, mesh and write the spec")
    b.add_argument("setup")
    b.add_argument("-o", "--out", required=True)
    b.add_argument("--case", default="strike")
    b.set_defaults(fn=_build)

    args = p.parse_args(argv)
    # Stdout carries exactly one JSON document. gmsh, OCC and HXT write to
    # the process's stdout from C/C++ — some of it only flushed at exit
    # (HXT's "duplicated facets" landed after the JSON). So fd 1 is pointed
    # at stderr for the rest of the process and never restored, and the
    # answer is written to a saved copy of the real stdout.
    sys.stdout.flush()
    real_stdout = os.dup(1)
    os.dup2(2, 1)
    try:
        result, code = args.fn(args), 0
    except Exception as exc:  # every failure reaches the agent as data, not a traceback
        result, code = {"error": f"{type(exc).__name__}: {exc}"}, 1
    with os.fdopen(real_stdout, "w") as out:
        out.write(json.dumps(result, default=str) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
