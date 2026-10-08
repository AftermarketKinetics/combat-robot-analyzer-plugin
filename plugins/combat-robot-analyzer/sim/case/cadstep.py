"""Bridge to the cad-step scripts copied into ``sim/cadstep/``.

They are CLI scripts, not an installed package, so they have to be put on
``sys.path`` before anything can import them. Every module that needs them
goes through here rather than manipulating ``sys.path`` itself, so there is
one place that knows where they live.

``load_step`` is the text tier (``stepcore``, pure stdlib); ``run_report`` is
the one-pass analysis, run as a subprocess.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

#: The cad-step scripts copied into v2 sit beside this package.
SCRIPTS = Path(__file__).resolve().parents[1] / "cadstep"

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    pass

__all__ = [
    "CadStepUnavailable",
    "ReportFailed",
    "ensure_on_path",
    "load_step",
    "run_report",
]


class CadStepUnavailable(RuntimeError):
    """The cad-step scripts are missing from ``sim/cadstep/``."""


def ensure_on_path() -> Path:
    """Put the cad-step script directory on ``sys.path`` and return it."""
    scripts = SCRIPTS
    if not (scripts / "stepcore.py").is_file():
        raise CadStepUnavailable(f"cad-step scripts not found at {scripts}")
    entry = str(scripts)
    if entry not in sys.path:
        sys.path.insert(0, entry)
    return scripts


def load_step(path: str | Path) -> Any:
    """Parse a STEP file and return the plugin's ``stepcore.StepFile``.

    Returned untyped: ``stepcore`` ships no annotations, and wrapping it in a
    Protocol would duplicate a 1200-line API for no benefit at this layer.
    """
    ensure_on_path()
    import stepcore

    return stepcore.load(str(path))


class ReportFailed(RuntimeError):
    """``step_report.py`` did not produce a document."""


def run_report(
    step_path: str | Path,
    *,
    skip: Sequence[str] = (),
    min_dia: float = 0.0,
    timeout: int = 600,
    tessellate_dir: str | Path | None = None,
    max_tessellated: int = 0,
    drawing_hidden: str = "",
) -> dict[str, Any]:
    """Run the plugin's one-pass analysis and return the parsed document.

    A subprocess rather than an import: the OpenCASCADE sections need
    pythonocc, and OCC sections are slow enough that a hang must be killable.
    In v2 pythonocc is in the sim Python (dev shell and sandbox image), so
    ``occenv.ensure_occ()`` is a no-op.
    """
    scripts = ensure_on_path()
    script = scripts / "step_report.py"
    if not script.is_file():
        raise CadStepUnavailable(
            f"step_report.py not found at {script}")

    cmd = [sys.executable, str(script), str(step_path), "--json"]
    if skip:
        cmd += ["--skip", ",".join(skip)]
    if min_dia:
        cmd += ["--min-dia", str(min_dia)]
    # `"outline"` or `"full"`, the plugin's two hidden-line modes.
    if drawing_hidden:
        cmd += ["--hidden", drawing_hidden]
    # Without a directory the tessellate section skips itself: the geometry is
    # megabytes and does not come back through this pipe. Callers that want it
    # say where it goes.
    if tessellate_dir:
        cmd += ["--tessellate-dir", str(tessellate_dir)]
        if max_tessellated:
            cmd += ["--max-tessellated", str(max_tessellated)]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReportFailed(f"geometry analysis exceeded {exc.timeout:.0f}s") from exc

    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        raise ReportFailed(
            f"step_report.py exited {proc.returncode}: {' / '.join(tail) or 'no output'}"
        )
    try:
        data: dict[str, Any] = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ReportFailed(f"step_report.py returned unparseable JSON: {exc}") from exc
    return data
