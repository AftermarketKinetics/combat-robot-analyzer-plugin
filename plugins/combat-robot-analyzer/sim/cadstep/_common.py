#!/usr/bin/env python3
"""Shared CLI plumbing for the STEP scripts."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def base_parser(description, multi=False):
    ap = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    if multi:
        ap.add_argument("files", nargs="+", help="STEP/STP files")
    else:
        ap.add_argument("file", help="STEP/STP file")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    return ap


def expired(deadline):
    """Has a wall-clock budget run out?

    A budget only means anything if the loop that spends the time is the one
    checking it.  Testing it between whole collectors lets a single pass start
    one second inside the budget and return three minutes outside it.
    """
    return deadline is not None and time.time() > deadline


def emit(data, as_json, text_fn):
    if as_json:
        json.dump(data, sys.stdout, indent=2, default=_default)
        sys.stdout.write("\n")
    else:
        text_fn(data)


def _default(o):
    if isinstance(o, tuple):
        return list(o)
    return str(o)


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0


def table(rows, headers, indent=2, aligns=None):
    """Render a fixed-width text table."""
    if not rows:
        return
    cols = len(headers)
    widths = [len(str(h)) for h in headers]
    srows = []
    for r in rows:
        cells = [("" if c is None else str(c)) for c in r]
        cells += [""] * (cols - len(cells))
        srows.append(cells)
        for i, c in enumerate(cells):
            widths[i] = max(widths[i], len(c))
    aligns = aligns or ["<"] * cols
    pad = " " * indent
    fmt = "  ".join("{:%s%d}" % (aligns[i], widths[i]) for i in range(cols))
    print(pad + fmt.format(*[str(h) for h in headers]))
    print(pad + "  ".join("-" * w for w in widths))
    for cells in srows:
        print(pad + fmt.format(*cells).rstrip())


def heading(text):
    print("")
    print(text)
    print("-" * len(text))


def fmt_num(v, nd=4):
    if v is None:
        return "-"
    s = "%.*f" % (nd, v)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def fmt_xyz(v, nd=4):
    return "(%s, %s, %s)" % (fmt_num(v[0], nd), fmt_num(v[1], nd), fmt_num(v[2], nd))


def match(name, patterns, default=True):
    """Case-insensitive substring / fnmatch filter."""
    if not patterns:
        return default
    import fnmatch
    low = (name or "").lower()
    for p in patterns:
        pl = p.lower()
        if pl in low or fnmatch.fnmatch(low, pl):
            return True
    return False
