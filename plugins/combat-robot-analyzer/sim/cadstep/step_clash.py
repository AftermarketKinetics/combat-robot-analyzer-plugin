#!/usr/bin/env python3
"""Interference and clearance checking between the parts of an assembly.

Bounding boxes reject the obviously-disjoint pairs first, then a real boolean
intersection measures how much material actually overlaps.  ``--clearance``
additionally reports pairs that do not touch but come closer than a limit.
"""

from __future__ import annotations

import sys
import time

import _common as C


def collect(path, parts=None, min_volume=1e-3, clearance=None, max_pairs=20000,
            gap=0.0):
    import occenv
    occenv.ensure_occ()
    import occshapes as O
    return collect_from(O.load_parts(path), parts, min_volume, clearance,
                        max_pairs, gap, path=path)


def collect_from(loaded, parts=None, min_volume=1e-3, clearance=None,
                 max_pairs=20000, gap=0.0, path="", id_of=None, deadline=None):
    """As :func:`collect`, but reusing already-loaded :class:`occshapes.Part` objects.

    *id_of* maps a :class:`occshapes.Part` to a stable part id; pass it to get
    ``a_id`` / ``b_id`` on every pair.

    *deadline* is an absolute ``time.time()`` after which no further pair is
    started.  Each boolean costs about a second, so a caller on a budget needs
    to be able to stop in the middle and keep what it has; the result then
    carries ``incomplete`` and the number of pairs that did get tested.
    """
    import occshapes as O
    from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape

    items = [p for p in loaded
             if C.match(p.name, parts) or C.match(p.path, parts)]
    # A bodiless occurrence bounds nothing and can clash with nothing, so it
    # is dropped rather than allowed to raise on the whole comparison.
    paired = [(p, O.bbox_or_none(p.shape)) for p in items]
    items = [p for p, b in paired if b is not None]
    boxes = [b for _, b in paired if b is not None]

    def overlap(i, j, slack):
        a, b = boxes[i], boxes[j]
        for k in range(3):
            if a[0][k] - slack > b[1][k] or b[0][k] - slack > a[1][k]:
                return False
        return True

    n = len(items)
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    if len(pairs) > max_pairs:
        raise SystemExit("%d part pairs exceeds --max-pairs %d; narrow with --part"
                         % (len(pairs), max_pairs))

    def shared_volume(i, j):
        a, b = boxes[i], boxes[j]
        v = 1.0
        for k in range(3):
            lo, hi = max(a[0][k], b[0][k]), min(a[1][k], b[1][k])
            if hi <= lo:
                return 0.0
            v *= hi - lo
        return v

    slack = max(clearance or 0.0, gap)
    candidates = [(i, j) for i, j in pairs if overlap(i, j, slack)]
    # Most sound assemblies clash nowhere and 90% of these booleans find
    # nothing, so the order only shows when a budget cuts the pass short.
    # A solid can overlap no more than its bounding box does, which makes the
    # shared box volume the best cheap guess at where the real interference
    # is -- worth having first if there is only time for some of them.
    candidates.sort(key=lambda ij: -shared_volume(*ij))

    def ids(a, b):
        return {} if id_of is None else {"a_id": id_of(a), "b_id": id_of(b)}

    clashes, clearances = [], []
    done = 0
    worst = 0.0
    for i, j in candidates:
        # A boolean cannot be interrupted once it has begun, so checking the
        # clock alone overruns by however long the next one takes -- and they
        # range from milliseconds to ten seconds on the same model.  Budget
        # against the worst seen so far rather than the average: overshooting
        # a hard timeout loses the whole run, stopping early loses one pair.
        pair_started = time.time()
        if deadline is not None and pair_started + worst > deadline:
            break
        done += 1
        a, b = items[i], items[j]
        vol = 0.0
        shp = None
        try:
            common = BRepAlgoAPI_Common(a.shape, b.shape)
            common.Build()
            if common.IsDone():
                shp = common.Shape()
                if shp is not None and not shp.IsNull():
                    vol = O.volume_props(shp)["volume"]
        except Exception as exc:            # keep going on degenerate solids
            rec = {"a": a.path, "b": b.path, "error": str(exc)}
            rec.update(ids(a, b))
            clashes.append(rec)
            worst = max(worst, time.time() - pair_started)
            continue
        if vol > min_volume:
            lo, hi = O.bbox(shp)
            rec = {
                "a": a.path, "b": b.path, "a_name": a.name, "b_name": b.name,
                "volume_mm3": vol,
                "region_min": list(lo), "region_max": list(hi),
                "region_center": [(lo[k] + hi[k]) / 2.0 for k in range(3)],
            }
            rec.update(ids(a, b))
            clashes.append(rec)
        elif clearance is not None:
            try:
                d = BRepExtrema_DistShapeShape(a.shape, b.shape)
                d.Perform()
                if d.IsDone():
                    dist = d.Value()
                    if dist <= clearance:
                        p1 = d.PointOnShape1(1)
                        rec = {
                            "a": a.path, "b": b.path,
                            "a_name": a.name, "b_name": b.name,
                            "distance": dist,
                            "at": [p1.X(), p1.Y(), p1.Z()],
                        }
                        rec.update(ids(a, b))
                        clearances.append(rec)
            except Exception:
                pass
        worst = max(worst, time.time() - pair_started)

    clashes.sort(key=lambda c: -(c.get("volume_mm3") or 0.0))
    clearances.sort(key=lambda c: c["distance"])
    out = {"path": path, "parts": n, "pairs_tested": done,
           "pairs_total": len(pairs), "clashes": clashes,
           "clearances": clearances, "clearance_limit": clearance}
    if deadline is not None:
        # only when a budget was asked for, so the tool's own output is
        # unchanged -- and there `done` is always every candidate anyway
        out["pairs_candidate"] = len(candidates)
        out["incomplete"] = done < len(candidates)
    return out


def show(d):
    print("%d parts, %d of %d pairs shared a bounding box"
          % (d["parts"], d["pairs_tested"], d["pairs_total"]))
    if d["clashes"]:
        C.heading("Interferences")
        C.table([[c.get("a_name") or c["a"], c.get("b_name") or c["b"],
                  c.get("error") or "%.4f" % c["volume_mm3"],
                  "" if "region_center" not in c else C.fmt_xyz(c["region_center"], 2)]
                 for c in d["clashes"]],
                ["part A", "part B", "overlap mm3", "centred at"],
                aligns=["<", "<", ">", "<"])
    else:
        print("\nNo interferences found.")
    if d["clearance_limit"] is not None:
        C.heading("Clearances under %.3f mm" % d["clearance_limit"])
        if d["clearances"]:
            C.table([[c["a_name"], c["b_name"], "%.4f" % c["distance"],
                      C.fmt_xyz(c["at"], 2)] for c in d["clearances"]],
                    ["part A", "part B", "gap mm", "closest point"],
                    aligns=["<", "<", ">", "<"])
        else:
            print("  none")


def main(argv=None):
    ap = C.base_parser(__doc__)
    ap.add_argument("--part", action="append", default=[])
    ap.add_argument("--clearance", type=float, default=None,
                    help="also report non-touching pairs closer than this (mm)")
    ap.add_argument("--min-volume", type=float, default=1e-3,
                    help="ignore overlaps smaller than this (mm3)")
    ap.add_argument("--max-pairs", type=int, default=20000)
    a = ap.parse_args(argv)
    C.emit(collect(a.file, a.part, a.min_volume, a.clearance, a.max_pairs),
           a.json, show)


if __name__ == "__main__":
    sys.exit(main())
