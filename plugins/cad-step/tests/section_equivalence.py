#!/usr/bin/env python3
"""``step_report`` must return exactly what the standalone scripts return.

This is what keeps the shared-parse refactor honest: the bulk endpoint is
allowed to add ``part_id`` keys and nothing else.  Any other divergence means
one of the ``collect_from`` splits changed behaviour.

    python3 tests/section_equivalence.py FILE
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "scripts"))

import occenv  # noqa: E402

occenv.ensure_occ()

import step_clash        # noqa: E402
import step_drawing      # noqa: E402
import step_fasteners    # noqa: E402
import step_features     # noqa: E402
import step_info         # noqa: E402
import step_measure      # noqa: E402
import step_placements   # noqa: E402
import step_report       # noqa: E402

ADDED = ("part_id", "a_id", "b_id", "part_ids")


def strip(o):
    """Drop the keys the bulk endpoint is allowed to add, and round floats.

    Sharing one set of shapes between sections is the whole point, but a
    boolean intersection nudges its operands at the 1e-13 level, so a section
    that runs after the clash pass is equivalent rather than bit-identical.
    """
    if isinstance(o, dict):
        return dict((k, strip(v)) for k, v in o.items() if k not in ADDED)
    if isinstance(o, list):
        return [strip(v) for v in o]
    if isinstance(o, float):
        return float("%.9g" % o)
    return o


path = sys.argv[1]
report = step_report.collect(path)

# the standalone call has to be given the same arguments step_report used
standalone = {
    "info": lambda: step_info.collect(path),
    "measure": lambda: step_measure.collect(path),
    "features": lambda: step_features.collect(path, world=True, planes=True,
                                              blends=True),
    "placements": lambda: step_placements.collect(path),
    "clash": lambda: step_clash.collect(path),
    "fasteners": lambda: step_fasteners.collect(path),
}

fails = []
for name, fn in standalone.items():
    section = report["sections"][name]
    if "error" in section:
        fails.append("%s: section errored: %s" % (name, section["error"]))
        continue
    a = json.dumps(strip(section["data"]), sort_keys=True, default=str)
    b = json.dumps(strip(fn()), sort_keys=True, default=str)
    if a != b:
        n = next((i for i in range(min(len(a), len(b))) if a[i] != b[i]),
                 min(len(a), len(b)))
        fails.append("%s: differs at char %d\n      report: %s\n      alone : %s"
                     % (name, n, a[max(0, n - 60):n + 60], b[max(0, n - 60):n + 60]))

# the drawing is compared on its statistics; the SVG itself is the same
# projection run twice and is checked for being well formed elsewhere
draw = report["sections"]["drawing"]
if "error" in draw:
    fails.append("drawing: section errored: %s" % draw["error"])
else:
    import occshapes as O
    _, info = step_drawing.render(O.load_parts(path), color=True,
                                  color_source=path)
    got = strip(dict((k, v) for k, v in draw["data"].items() if k != "svg"))
    if json.dumps(got, sort_keys=True) != json.dumps(strip(info), sort_keys=True):
        fails.append("drawing: %s != %s" % (got, strip(info)))
    if not draw["data"].get("svg", "").lstrip().startswith("<"):
        fails.append("drawing: svg is not inline in the section")

print("%s: %d sections compared" % (os.path.basename(path), len(standalone) + 1))
for f in fails:
    print("  FAIL %s" % f)
raise SystemExit(1 if fails else 0)
