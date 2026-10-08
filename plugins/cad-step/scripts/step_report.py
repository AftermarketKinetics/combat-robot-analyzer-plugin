#!/usr/bin/env python3
"""Every collector's output for one STEP file, from a single parse.

The seven focused tools each load the file themselves, which is the right
trade when a person is asking one question at a time.  A program that wants
all of the answers at once pays that cost seven times -- on a 32 MB assembly
that is minutes rather than seconds -- so this runs the same collectors over
one ``stepcore`` parse and one OpenCASCADE transfer.

The other thing it adds is identity.  Both tiers otherwise name parts, and a
name is not unique: eight identical standoffs under one parent share theirs,
and the two tiers do not even agree on which name to use.  Every occurrence
here gets an opaque ``id``, every section is keyed by it, and ``joined`` says
how confidently each was reconciled (see :mod:`stepjoin`).  Records that could
not be reconciled are reported as such rather than guessed at.

There is no SKILL.md for this: it is a machine-facing endpoint, and a combined
dump is worse than the focused tools for reading by hand.

    step_report.py FILE --json [--skip drawing,clash] [--max-seconds 120]
"""

from __future__ import annotations

import os
import sys
import time

import _common as C

SECTIONS = ("info", "measure", "features", "placements", "clash", "fasteners",
            "drawing", "tessellate")


def _section(name, fn, skip, deadline):
    """Run one collector, keeping its failure to itself.

    A projection that will not converge or a clash pass that runs out of pairs
    must cost the caller one section, not the whole document.
    """
    if name in skip:
        return {"seconds": 0.0, "error": "skipped"}
    if deadline is not None and time.time() > deadline:
        return {"seconds": 0.0, "error": "skipped: time budget exhausted"}
    t0 = time.time()
    try:
        data = fn()
    except (Exception, SystemExit) as exc:
        return {"seconds": round(time.time() - t0, 2),
                "error": "%s: %s" % (type(exc).__name__, exc)}
    return {"seconds": round(time.time() - t0, 2), "data": data}


def part_rows(index, validate=False, measured=None, rollups=None):
    """One row per occurrence, in part-id order.

    ``volume_mm3`` and ``closed`` together are the geometry sanity gate: an
    open shell has a volume, it just does not mean anything, and it cannot be
    meshed either.  Rows the OCC tier never saw carry nulls for the measured
    fields rather than being dropped, so a caller can tell "no such part" from
    "part we could not measure".

    *measured* is ``{part_id: measure row}`` from an already-run measure
    section; mass properties are taken from it rather than computed twice,
    which is a quarter of the non-clash budget on a 10 MB assembly.
    *rollups* is :func:`feature_rollups` keyed the same way, or ``None`` when
    the features pass did not run.
    """
    import occshapes as O

    measured = measured or {}
    seen = {}
    rows = []
    for e in index.entries:
        node = e.node
        pd = node.pd_id if node is not None else None
        key = pd if pd is not None else ("name", e.name)
        seen[key] = seen.get(key, -1) + 1
        row = {
            "id": e.id,
            "name": e.name,
            "path": e.path,
            "product_definition_id": pd,
            "nauo_chain": list(node.nauo_chain) if node is not None else None,
            "occurrence": seen[key],
            "joined": e.joined,
            "volume_mm3": None,
            "area_mm2": None,
            "com": None,
            "bbox": None,
            "solids": None,
            "solid_bboxes": None,
            "solid_volumes": None,
            "closed": None,
            "color": None,
            "min_edge_mm": None,
        }
        # "no holes" and "we never looked" are different answers, and only
        # the first one means anything to a caller checking whether a weapon
        # part has somewhere to put a shaft
        looked = rollups is not None and e.node is not None
        row.update({"has_cylindrical_bore": False if looked else None,
                    "max_bore_dia_mm": None, "min_feature_mm": None})
        if looked:
            row.update(rollups.get(e.id, {}))
        p = e.part
        if p is not None:
            done = measured.get(e.id)
            if done is not None:
                row["volume_mm3"] = done["volume_mm3"]
                row["area_mm2"] = done["area_mm2"]
                row["com"] = done["com"]
                row["solids"] = done["solids"]
            else:
                vp = O.volume_props(p.shape)
                row["volume_mm3"] = vp["volume"]
                row["area_mm2"] = O.surface_area(p.shape)
                row["com"] = list(vp["com"])
                row["solids"] = O.topology_census(p.shape)["solids"]
            # A bodiless occurrence encloses nothing, and OCC raises rather
            # than returning an empty box; the row already allows a null bbox,
            # so one such part must not take the whole report down.
            extent = O.bbox_or_none(p.shape)
            row["bbox"] = (
                {"min": list(extent[0]), "max": list(extent[1])} if extent else None
            )
            # Per-solid boxes only where there is more than one solid to tell
            # apart; a single-solid part is fully described by its own bbox.
            if (row["solids"] or 1) > 1:
                extents = O.solid_extents(p.shape)
                # `None` per unboxable body, not a shorter list: the index
                # into this array is how a consumer names a solid, and it has
                # to keep meaning the same body as `solid_volumes`.
                row["solid_bboxes"] = (
                    [
                        None if e is None else {"min": list(e[0]), "max": list(e[1])}
                        for e in extents
                    ]
                    if extents
                    else None
                )
                # Per solid, because volume_mm3 above is the union of the
                # bodies and a consumer decomposing the part cannot compare
                # against it: overlapping bodies make the whole less than the
                # sum. Indexes with solid_bboxes.
                row["solid_volumes"] = O.solid_volumes(p.shape)
            row["closed"] = O.is_closed(p.shape)
            # The smallest edge, not the smallest recognised feature. A sliver
            # from a bad export is neither hole nor fillet, so min_feature_mm
            # cannot see it, and it is exactly what drives the timestep.
            row["min_edge_mm"] = O.min_edge_length(p.shape)
            row["color"] = list(p.color) if p.color else None
            if validate:
                row["valid"] = O.is_valid(p.shape)
        rows.append(row)
    return rows


def feature_rollups(features):
    """Per-part bore and smallest-feature figures, keyed by part id.

    ``has_cylindrical_bore`` backs the "a weapon part with nowhere to put a
    shaft is probably mis-tagged" check; ``max_bore_dia_mm`` is there because
    that flag alone cannot tell a shaft bore from a tapped M2 hole, and only
    the caller knows which it wanted.

    ``min_feature_mm`` is the smallest thing a mesh would have to resolve on
    that part: a hole counts as its diameter, a fillet as its radius.  Those
    are not the same kind of number, but they are the two numbers a CAD user
    recognises, and both scale the same way against element size.
    """
    out = {}

    def slot(pid):
        return out.setdefault(pid, {"has_cylindrical_bore": False,
                                    "max_bore_dia_mm": None,
                                    "min_feature_mm": None})

    def smallest(d, v):
        if d["min_feature_mm"] is None or v < d["min_feature_mm"]:
            d["min_feature_mm"] = v

    for r in features.get("holes", ()):
        pid = r.get("part_id")
        if pid is None:
            continue
        d = slot(pid)
        smallest(d, r["diameter"])
        if r["kind"] != "hole":
            continue
        d["has_cylindrical_bore"] = True
        if d["max_bore_dia_mm"] is None or r["diameter"] > d["max_bore_dia_mm"]:
            d["max_bore_dia_mm"] = r["diameter"]
    for b in features.get("blends", ()):
        pid = b.get("part_id")
        if pid is not None:
            smallest(slot(pid), b["radius"])
    return out


def timestep_drivers(features, names, limit_mm):
    """The smallest features in the model, grouped, smallest first.

    Refusing a job because the geometry is too fine is only useful if it comes
    with the list of things to go and remove, by size and location -- "your
    model is too detailed" is not actionable.  Identical features on the same
    part collapse into one row with a count, because six 0.4 mm holes are one
    decision, not six.
    """
    # the rounding buckets features that are the same size to within a tenth
    # of a micron; the size reported back is the real one, so that it still
    # matches the part's own min_feature_mm exactly
    groups = {}

    def add(pid, kind, size, center):
        if pid:
            g = groups.setdefault((pid, kind, round(size, 4)), [[], []])
            g[0].append(size)
            g[1].append(center)

    for r in features.get("holes", ()):
        if r["kind"] == "hole":
            add(r.get("part_id"), "hole", r["diameter"], r["center"])
    for b in features.get("blends", ()):
        add(b.get("part_id"), b["kind"], b["radius"], b["center"])

    out = []
    for (pid, kind, _), (sizes, centers) in groups.items():
        if min(sizes) > limit_mm:
            continue
        out.append({"part_id": pid, "part": names.get(pid), "kind": kind,
                    "size_mm": min(sizes), "center": sorted(centers)[0],
                    "count": len(centers)})
    out.sort(key=lambda d: (d["size_mm"], d["part_id"], d["kind"]))
    return out


def _assembly_bbox(rows):
    """The union of the measured part boxes, in the same world frame they are.

    ``step_info``'s bounds are vertex-based and so understate curved parts;
    this one is exact, which is what a "does this part sit on the outside of
    the assembly" test needs on both sides of the comparison.
    """
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for r in rows:
        b = r.get("bbox")
        if not b:
            continue
        for i in range(3):
            lo[i] = min(lo[i], b["min"][i])
            hi[i] = max(hi[i], b["max"][i])
    if lo[0] == float("inf"):
        return None
    return {"min": lo, "max": hi, "size": [hi[i] - lo[i] for i in range(3)]}


def collect(path, skip=(), min_dia=0.0, max_seconds=None, validate=False,
            views=None, hidden=False, clearance=None, max_pairs=20000,
            driver_max=1.0, tessellate_dir=None, max_tessellated=0):
    import occenv
    occenv.ensure_occ()

    import occshapes as O
    import stepcore
    import stepjoin
    import step_clash
    import step_drawing
    import step_fasteners
    import step_features
    import step_info
    import step_measure
    import step_placements
    import step_tessellate

    t0 = time.time()
    deadline = t0 + max_seconds if max_seconds else None
    skip = set(skip)
    unknown = skip - set(SECTIONS)
    if unknown:
        raise SystemExit("unknown section(s) %s; choose from %s"
                         % (", ".join(sorted(unknown)), ", ".join(SECTIONS)))

    sf = stepcore.load(path)
    parts = O.load_parts(path)
    index = stepjoin.build_index(sf, parts, occ_bbox=O.bbox)
    by_part, by_node = index.id_for_part, index.id_for_node
    units = sf.units()

    sections = {}
    sections["info"] = _section(
        "info", lambda: step_info.collect_from(sf), skip, deadline)
    sections["measure"] = _section(
        "measure", lambda: step_measure.collect_from(
            parts, path=path, id_of=by_part), skip, deadline)
    sections["features"] = _section(
        "features", lambda: step_features.collect_from(
            sf, world=True, min_dia=min_dia, planes=True, blends=True,
            id_of=by_node, deadline=deadline), skip, deadline)
    sections["placements"] = _section(
        "placements", lambda: step_placements.collect_from(
            sf, id_of=by_node), skip, deadline)
    sections["fasteners"] = _section(
        "fasteners", lambda: step_fasteners.collect_from(
            parts, path=path, id_of=by_part), skip, deadline)
    # clash and drawing run last whatever order they are reported in: they
    # cost more than everything else together, and under a budget they must
    # not be the reason a section worth a fifth of a second never ran
    sections["clash"] = _section(
        "clash", lambda: step_clash.collect_from(
            parts, clearance=clearance, max_pairs=max_pairs, path=path,
            id_of=by_part, deadline=deadline), skip, deadline)

    def draw():
        svg, info = step_drawing.render(
            parts, views=views, hidden=hidden, color=True,
            title=os.path.basename(path), color_source=sf, id_of=by_part,
            deadline=deadline)
        # the SVG travels inline: a caller embedding this in a report should
        # not have to find somewhere writable to put it first
        info["svg"] = svg
        return info

    sections["drawing"] = _section("drawing", draw, skip, deadline)

    # Folded in here rather than run as its own invocation because the shapes
    # are already loaded: measured across the corpus, a separate pass re-reads
    # the STEP and spends about two thirds of its total doing it again. From
    # here only the triangulation is new, a median 54% on top of the load.
    #
    # Skipped with no output directory rather than returning 6 MB inline. The
    # caller has to have decided where the geometry goes; there is no sensible
    # default and inventing one writes a large file somewhere nobody looked.
    def tess():
        if not tessellate_dir:
            raise SystemExit("no output directory; pass --tessellate-dir")
        return step_tessellate.collect_from(
            parts, tessellate_dir,
            name=os.path.splitext(os.path.basename(path))[0],
            source=os.path.basename(path), id_of=by_part,
            max_parts=max_tessellated, deadline=deadline)

    sections["tessellate"] = _section("tessellate", tess, skip, deadline)

    # after the sections, so the mass properties measure has already worked
    # out are not worked out a second time
    t_parts = time.time()
    done = sections["measure"].get("data")
    measured = dict((r["part_id"], r) for r in done["parts"]) if done else None
    feats = sections["features"].get("data")
    rows = part_rows(index, validate, measured,
                     feature_rollups(feats) if feats is not None else None)
    feats = feats or {}
    names = dict((r["id"], r["name"]) for r in rows)
    drivers = timestep_drivers(feats, names, driver_max)
    t_parts = round(time.time() - t_parts, 2)

    return {
        "path": os.path.abspath(path),
        "size_bytes": os.path.getsize(path),
        "seconds": round(time.time() - t0, 2),
        "schema": sf.schema,
        "units": {"length": [units["length"][0], units["length"][1]],
                  "angle": units["angle"], "assumed": units["assumed"]},
        "parts": rows,
        "parts_seconds": t_parts,
        "bbox": _assembly_bbox(rows),
        "timestep_drivers": drivers,
        "timestep_drivers_max_mm": driver_max,
        # an empty list means "nothing that small"; this says whether it
        # instead means the features pass never got far enough to tell.
        # --min-dia hides small holes from both.
        "timestep_drivers_error": sections["features"].get("error") or (
            "features incomplete: %d of %d solids"
            % (feats.get("solids_reached", 0), feats.get("solids_total", 0))
            if feats.get("incomplete") else None),
        "unjoined_parts": index.unjoined,
        "join_methods": index.counts(),
        "sections": {k: sections[k] for k in SECTIONS},
    }


def _status(section):
    """ok / an error / how far a section got before the budget ran out."""
    if section.get("error"):
        return section["error"]
    data = section.get("data")
    if not isinstance(data, dict) or not data.get("incomplete"):
        return "ok"
    for done, total in (("pairs_tested", "pairs_candidate"),
                        ("solids_reached", "solids_total"),
                        ("parts_tessellated", "parts_total")):
        if total in data:
            return "partial (%s of %s)" % (data[done], data[total])
    return "partial"


def show(d):
    print("File        %s  (%s)" % (os.path.basename(d["path"]),
                                    C.human_size(d["size_bytes"])))
    print("Schema      %s, %s" % (d["schema"] or "?", d["units"]["length"][0]))
    print("Elapsed     %.2f s" % d["seconds"])

    C.heading("Parts")
    C.table([[r["id"], r["name"][:34], r["joined"],
              "-" if r["volume_mm3"] is None else "%.1f" % r["volume_mm3"],
              {True: "yes", False: "NO", None: "-"}[r["closed"]],
              "-" if r["solids"] is None else r["solids"]]
             for r in d["parts"]],
            ["id", "name", "joined", "volume mm3", "closed", "solids"],
            aligns=["<", "<", "<", ">", ">", ">"])
    print("")
    print("%d parts; %d not reconciled between the two tiers (%s)"
          % (len(d["parts"]), d["unjoined_parts"],
             ", ".join("%s %d" % kv for kv in sorted(d["join_methods"].items()))))
    open_shells = [r for r in d["parts"] if r["closed"] is False]
    if open_shells:
        print("WARNING: %d part(s) are not closed solids; their volumes are "
              "meaningless and they cannot be meshed:" % len(open_shells))
        for r in open_shells[:10]:
            print("    %s %s" % (r["id"], r["name"]))

    if d["timestep_drivers"]:
        C.heading("Smallest features (drive the explicit timestep, <= %g mm)"
                  % d["timestep_drivers_max_mm"])
        C.table([[t["part_id"], (t["part"] or "")[:30], t["kind"],
                  "%.3f" % t["size_mm"], t["count"], C.fmt_xyz(t["center"], 2)]
                 for t in d["timestep_drivers"][:20]],
                ["id", "part", "kind", "size mm", "count", "first at"],
                aligns=["<", "<", "<", ">", ">", "<"])
        extra = len(d["timestep_drivers"]) - 20
        if extra > 0:
            print("  ... and %d more group(s)" % extra)

    C.heading("Sections")
    C.table([[k, "%.2f" % v["seconds"], _status(v)]
             for k, v in d["sections"].items()],
            ["section", "seconds", "status"], aligns=["<", ">", "<"])
    if any(_status(v).startswith("partial") for v in d["sections"].values()):
        print("")
        print("A partial section ran out of the --max-seconds budget part way "
              "through.  What it did\nfinish is real; what is missing is "
              "missing, not absent.")


def main(argv=None):
    ap = C.base_parser(__doc__)
    ap.add_argument("--skip", default="",
                    help="comma-separated sections to omit (%s); drawing is "
                         "the most expensive" % ", ".join(SECTIONS))
    ap.add_argument("--min-dia", type=float, default=0.0,
                    help="ignore holes smaller than this (mm); note this "
                         "hides them from timestep_drivers too")
    ap.add_argument("--driver-max", type=float, default=1.0,
                    help="largest feature to list as a timestep driver (mm)")
    ap.add_argument("--max-seconds", type=float, default=None,
                    help="stop starting new sections once this much wall "
                         "clock has gone; those left are marked skipped")
    ap.add_argument("--validate", action="store_true",
                    help="also run the full BRepCheck validity test per part "
                         "(slow)")
    ap.add_argument("--view", action="append", default=[],
                    help="drawing view name; repeatable")
    ap.add_argument("--hidden", nargs="?", const="full", default=None,
                    choices=["full", "outline"],
                    help="drawing: hidden lines dashed -- 'full' (every hidden "
                         "edge) or 'outline' (each hidden part's silhouette). "
                         "Outline is what makes a part that is buried behind "
                         "another one appear in the sheet at all.")
    ap.add_argument("--clearance", type=float, default=None,
                    help="also report non-touching pairs closer than this (mm)")
    ap.add_argument("--max-pairs", type=int, default=20000)
    ap.add_argument("--tessellate-dir", default=None,
                    help="write per-part viewer geometry here; without it the "
                         "tessellate section is skipped, because the document "
                         "is megabytes and does not travel inline")
    ap.add_argument("--max-tessellated", type=int, default=0,
                    help="tessellate only the N largest parts by volume "
                         "(0 = all; a part absent cannot be tagged or struck)")
    a = ap.parse_args(argv)
    skip = [s.strip() for s in a.skip.split(",") if s.strip()]
    data = collect(a.file, skip, a.min_dia, a.max_seconds, a.validate,
                   a.view or None, a.hidden, a.clearance, a.max_pairs,
                   a.driver_max, a.tessellate_dir, a.max_tessellated)
    C.emit(data, a.json, show)


if __name__ == "__main__":
    sys.exit(main())
