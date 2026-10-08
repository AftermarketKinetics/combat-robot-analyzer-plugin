"""robot-impact: drive one case directory from STEP to results.

    robot-impact init STEP CASE          copy the model in and analyse it
    robot-impact show CASE               setup, what is missing, the weapon
    robot-impact set CASE JSON           change the setup (only the given keys)
    robot-impact materials [QUERY]       the material library
    robot-impact scope CASE              which neighbour parts a build carries
    robot-impact build CASE              mesh, deck, starter check, time estimate
    robot-impact solve CASE [-nt N] [--allow-over-budget]
    robot-impact results CASE            energies, damage, wear map, playback

Each prints one JSON document and exits 0, or prints {"error": ...} and
exits 1.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from .build import build, solve
from .case_dir import DEFAULTS, RESOLVED, CaseError, load, missing, save, sim, write_resolved
from .results import results
from .setup_edit import materials_table, update, weapon_card


def _init(a: argparse.Namespace) -> Any:
    case = Path(a.case)
    case.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(a.step, case / "model.step")
    rep = sim(case, "case_cli.py", "report", "model.step", "-o", "report", timeout=1200)
    if not (case / "setup.json").is_file():
        save(case, dict(DEFAULTS))
    return rep


def _show(a: argparse.Namespace) -> Any:
    case = Path(a.case)
    setup = load(case)
    return {"setup": setup, "missing": missing(case, setup),
            "weapon": weapon_card(case, setup["impactor"])}


def _set(a: argparse.Namespace) -> Any:
    try:
        patch = json.loads(a.json)
    except json.JSONDecodeError as exc:
        raise CaseError(f"not valid JSON: {exc}") from exc
    return update(Path(a.case), patch)


def _materials(a: argparse.Namespace) -> Any:
    q = (a.query or "").lower()
    return {"materials": [m for m in materials_table() if q in m["key"] or q in str(m["name"]).lower()]}


def _scope(a: argparse.Namespace) -> Any:
    case = Path(a.case)
    write_resolved(case)
    return sim(case, "case_cli.py", "scope", RESOLVED, timeout=300)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="robot-impact", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init"); s.add_argument("step"); s.add_argument("case"); s.set_defaults(fn=_init)
    s = sub.add_parser("show"); s.add_argument("case"); s.set_defaults(fn=_show)
    s = sub.add_parser("set"); s.add_argument("case"); s.add_argument("json"); s.set_defaults(fn=_set)
    s = sub.add_parser("materials"); s.add_argument("query", nargs="?"); s.set_defaults(fn=_materials)
    s = sub.add_parser("scope"); s.add_argument("case"); s.set_defaults(fn=_scope)
    s = sub.add_parser("build"); s.add_argument("case")
    s.set_defaults(fn=lambda a: build(Path(a.case)))
    s = sub.add_parser("solve"); s.add_argument("case"); s.add_argument("-nt", type=int)
    s.add_argument("--allow-over-budget", action="store_true")
    s.set_defaults(fn=lambda a: solve(Path(a.case), a.nt, a.allow_over_budget))
    s = sub.add_parser("results"); s.add_argument("case")
    s.set_defaults(fn=lambda a: results(Path(a.case)))
    args = p.parse_args(argv)
    try:
        out, code = args.fn(args), 0
    except CaseError as exc:
        out, code = {"error": str(exc)}, 1
    print(json.dumps(out, indent=2, default=str))
    return code


if __name__ == "__main__":
    sys.exit(main())
