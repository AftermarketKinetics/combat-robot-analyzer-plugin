#!/usr/bin/env python3
"""Transparent access to pythonocc-core (OpenCASCADE) without a global install.

Scripts that need exact B-rep geometry call :func:`ensure_occ` first.  If
``OCC`` cannot be imported the current process re-execs itself inside

    nix-shell -p "python3.withPackages(ps: with ps; [pythonocc-core numpy])"

so the caller only ever has to run ``python3 step_measure.py part.step``.

Escape hatches:
  ``STEP_OCC_PYTHON``   absolute path to an interpreter that already has OCC
  ``STEP_OCC_NO_NIX=1`` never re-exec; fail with a clear message instead
"""

from __future__ import annotations

import os
import shutil
import sys

_GUARD = "STEP_OCC_REEXEC"
_PKGS = ("pythonocc-core", "numpy")

_HELP = """pythonocc-core is required for this operation but is not importable.

Options:
  * NixOS / nix installed:  nothing to do -- this script re-execs itself in a
    nix-shell automatically (it could not find `nix-shell` on PATH).
  * conda:                  conda install -c conda-forge pythonocc-core
  * point at an existing interpreter:  export STEP_OCC_PYTHON=/path/to/python
"""


def have_occ():
    try:
        import OCC  # noqa: F401
        return True
    except Exception:
        return False


def ensure_occ(extra_packages=()):
    """Return once ``OCC`` is importable, re-execing under nix-shell if needed."""
    if have_occ():
        return

    if os.environ.get(_GUARD):
        sys.stderr.write("error: re-exec happened but OCC still missing\n" + _HELP)
        raise SystemExit(3)

    script = os.path.abspath(sys.argv[0])
    args = sys.argv[1:]
    env = dict(os.environ)
    env[_GUARD] = "1"

    alt = os.environ.get("STEP_OCC_PYTHON")
    if alt and os.path.exists(alt):
        os.execve(alt, [alt, script] + args, env)

    if os.environ.get("STEP_OCC_NO_NIX") or not shutil.which("nix-shell"):
        sys.stderr.write(_HELP)
        raise SystemExit(3)

    pkgs = " ".join(list(_PKGS) + list(extra_packages))
    expr = "python3.withPackages(ps: with ps; [%s])" % pkgs
    inner = " ".join(_q(x) for x in ["python3", script] + args)
    sys.stderr.write("[occ] entering nix-shell (%s)...\n" % pkgs)
    os.execvpe("nix-shell", ["nix-shell", "-p", expr, "--run", inner], env)


def _q(s):
    if s and all(c.isalnum() or c in "-_./=" for c in s):
        return s
    return "'" + s.replace("'", "'\\''") + "'"


if __name__ == "__main__":
    ensure_occ()
    import OCC
    print("pythonocc", OCC.VERSION, "via", sys.executable)
