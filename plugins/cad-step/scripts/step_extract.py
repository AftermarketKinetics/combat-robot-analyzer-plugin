#!/usr/bin/env python3
"""Write selected parts of an assembly out as a standalone STEP file.

Useful for isolating a subassembly before analysing or sending it somewhere.
By default the parts keep their assembly position; ``--local`` moves the first
match back to its own origin.
"""

from __future__ import annotations

import os
import sys

import _common as C


def run(path, out_path, parts, local=False, list_only=False):
    import occenv
    occenv.ensure_occ()
    import occshapes as O

    items = O.load_parts(path)
    picked = [p for p in items
              if C.match(p.name, parts, default=False)
              or C.match(p.path, parts, default=False)]
    if list_only or not parts:
        return {"available": [{"name": p.name, "path": p.path} for p in items],
                "selected": len(picked)}
    if not picked:
        raise SystemExit("no parts matched %s (run with --list to see names)"
                         % ", ".join(parts))

    from OCC.Core.BRep import BRep_Builder
    from OCC.Core.STEPControl import STEPControl_AsIs, STEPControl_Writer
    from OCC.Core.TopoDS import TopoDS_Compound

    shapes = [p.shape for p in picked]
    if local:
        inv = picked[0].shape.Location().Inverted()
        shapes = [s.Moved(inv) for s in shapes]

    builder = BRep_Builder()
    comp = TopoDS_Compound()
    builder.MakeCompound(comp)
    for s in shapes:
        builder.Add(comp, s)

    writer = STEPControl_Writer()
    writer.Transfer(comp, STEPControl_AsIs)
    status = writer.Write(out_path)
    ok = os.path.exists(out_path) and os.path.getsize(out_path) > 0
    lo, hi = O.bbox(comp)
    return {
        "output": os.path.abspath(out_path),
        "ok": bool(ok),
        "status": int(status),
        "parts": [p.path for p in picked],
        "frame": "part-local" if local else "assembly",
        "bounds": {"min": list(lo), "max": list(hi),
                   "size": [hi[i] - lo[i] for i in range(3)]},
        "size_bytes": os.path.getsize(out_path) if ok else 0,
    }


def show(d):
    if "available" in d:
        C.table([[p["name"], p["path"]] for p in d["available"]], ["name", "path"])
        print("")
        print("%d parts" % len(d["available"]))
        return
    print("wrote %s (%s)" % (d["output"], C.human_size(d["size_bytes"])))
    print("  %d part(s), %s frame" % (len(d["parts"]), d["frame"]))
    print("  bounds %s .. %s" % (C.fmt_xyz(d["bounds"]["min"], 3),
                                 C.fmt_xyz(d["bounds"]["max"], 3)))
    for p in d["parts"][:20]:
        print("    - %s" % p)


def main(argv=None):
    ap = C.base_parser(__doc__)
    ap.add_argument("--part", action="append", default=[],
                    help="part name or path to extract (repeatable)")
    ap.add_argument("-o", "--output", default=None)
    ap.add_argument("--local", action="store_true",
                    help="reset to the first matched part's own frame")
    ap.add_argument("--list", action="store_true", help="just list part names")
    a = ap.parse_args(argv)
    out = a.output or (os.path.splitext(os.path.basename(a.file))[0] + "-extract.step")
    C.emit(run(a.file, out, a.part, a.local, a.list), a.json, show)


if __name__ == "__main__":
    sys.exit(main())
