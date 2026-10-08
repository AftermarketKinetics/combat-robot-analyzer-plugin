#!/usr/bin/env python3
"""Orthographic technical drawings of a STEP model, as SVG.

Uses OpenCASCADE hidden-line removal, so the result is a real engineering
projection -- visible edges solid, optionally hidden edges dashed -- not a
shaded render.  The default sheet holds four views (top, isometric, front,
right) at one shared scale.

Pass ``id_of`` to :func:`render` and every path carries ``data-part``, which is
what turns the sheet from a picture into something a page can hit-test.  Each
view is also wrapped in a ``<g class="view">`` carrying its projection, so a
caller can put a world point back into the sheet -- the coordinates themselves
are baked and cannot be inverted.
"""

from __future__ import annotations

import math
import os
import sys
import time

import _common as C

VIEWS = {
    # name: (direction towards viewer, screen-right vector)
    "front": ((0, -1, 0), (1, 0, 0)),
    "back":  ((0, 1, 0), (-1, 0, 0)),
    "top":   ((0, 0, 1), (1, 0, 0)),
    "bottom": ((0, 0, -1), (1, 0, 0)),
    "right": ((1, 0, 0), (0, 1, 0)),
    "left":  ((-1, 0, 0), (0, -1, 0)),
    "iso":   ((1, 1, 1), (1, -1, 0)),
    "iso-rear": ((-1, 1, 1), (1, 1, 0)),
}

DEFAULT_SHEET = ["top", "iso", "front", "right"]


def _norm(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


def project_edges(parts, view, deflection, hidden=False, smooth=True):
    """Run HLR for one view; return ``{part_index: {"visible": [...], ...}}``.

    *hidden* is ``False``, ``True`` or ``"outline"``.  The middle option draws
    every hidden edge and is enormously expensive on an assembly -- measured on
    a 56-part bot, 137,410 hidden polylines against 8,313 visible.  ``"outline"``
    keeps only each hidden part's silhouette: 4,803 polylines for the same
    model, at no measurable extra time.

    The difference that matters is not ink, it is *addressability*.  With no
    hidden lines only the parts you can see emit a path at all, so on that same
    bot 13 of 56 parts appear in the sheet; with outlines, 47.  A caller using
    ``data-part`` to let someone pick a part cannot pick one that was never
    drawn.
    """
    from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
    from OCC.Core.HLRAlgo import HLRAlgo_Projector
    from OCC.Core.HLRBRep import HLRBRep_PolyAlgo, HLRBRep_PolyHLRToShape
    from OCC.Core.gp import gp_Ax2, gp_Dir, gp_Pnt

    direction, right = VIEWS[view]
    ax = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*_norm(direction)), gp_Dir(*_norm(right)))
    projector = HLRAlgo_Projector(ax)

    algo = HLRBRep_PolyAlgo()
    for p in parts:
        BRepMesh_IncrementalMesh(p.shape, deflection, False, 0.35, True)
        algo.Load(p.shape)
    algo.Projector(projector)
    algo.Update()

    hlr = HLRBRep_PolyHLRToShape()
    hlr.Update(algo)

    out = {}
    for idx, p in enumerate(parts):
        entry = {"visible": [], "hidden": []}
        groups = [("visible", hlr.VCompound(p.shape)),
                  ("visible", hlr.OutLineVCompound(p.shape))]
        if smooth:
            groups.append(("visible", hlr.Rg1LineVCompound(p.shape)))
        if hidden == "outline":
            groups.append(("hidden", hlr.OutLineHCompound(p.shape)))
        elif hidden:
            groups.append(("hidden", hlr.HCompound(p.shape)))
            groups.append(("hidden", hlr.OutLineHCompound(p.shape)))
        for key, comp in groups:
            if comp is None or comp.IsNull():
                continue
            entry[key].extend(_polylines(comp, deflection))
        out[idx] = entry
    return out


def _polylines(compound, deflection):
    """Discretise every edge of an HLR compound into 2-D polylines."""
    from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
    from OCC.Core.GCPnts import GCPnts_QuasiUniformDeflection
    from OCC.Core.TopAbs import TopAbs_EDGE
    from OCC.Core.TopExp import TopExp_Explorer

    lines = []
    exp = TopExp_Explorer(compound, TopAbs_EDGE)
    while exp.More():
        edge = exp.Current()
        exp.Next()
        try:
            curve = BRepAdaptor_Curve(edge)
            disc = GCPnts_QuasiUniformDeflection(curve, max(deflection, 1e-4))
            if not disc.IsDone() or disc.NbPoints() < 2:
                continue
            pts = []
            for i in range(1, disc.NbPoints() + 1):
                p = disc.Value(i)
                pts.append((p.X(), p.Y()))
            lines.append(pts)
        except Exception:
            continue
    return lines


def _bounds(view_entry):
    """2-D extents of one view's polylines."""
    lo = [float("inf")] * 2
    hi = [float("-inf")] * 2
    for entry in view_entry.values():
        for key in ("visible", "hidden"):
            for line in entry[key]:
                for x, y in line:
                    lo[0] = min(lo[0], x)
                    lo[1] = min(lo[1], y)
                    hi[0] = max(hi[0], x)
                    hi[1] = max(hi[1], y)
    if lo[0] == float("inf"):
        return (0.0, 0.0), (1.0, 1.0)
    return tuple(lo), tuple(hi)


def _svg_path(line, ox, oy, scale, flip_y):
    d = []
    for i, (x, y) in enumerate(line):
        sx = ox + x * scale
        sy = oy + (-y * scale if flip_y else y * scale)
        d.append("%s%.2f %.2f" % ("M" if i == 0 else "L", sx, sy))
    return "".join(d)


def build_svg(model_name, view_data, view_names, part_names, part_colors,
              size_mm, page=1600, margin=48, hidden=False, dims=None,
              scale_note=None, part_ids=None):
    """The sheet as SVG.

    *part_ids*, when given, is one identifier per entry in ``part_names`` and
    lands on every path as ``data-part``.  Without it the drawing is a picture;
    with it a caller can hit-test, highlight or cross-reference a part, and
    ``info["part_ids"]`` alone cannot do that -- a part with no visible edge in
    a view emits no path at all, so nothing downstream can re-attach ids by
    counting.
    """
    spans = {}
    for v in view_names:
        lo, hi = _bounds(view_data[v])
        spans[v] = (lo, hi, max(hi[0] - lo[0], 1e-6), max(hi[1] - lo[1], 1e-6))

    cols = 2 if len(view_names) > 1 else 1
    rows = int(math.ceil(len(view_names) / float(cols)))
    cell_w = (page - margin * (cols + 1)) / float(cols)

    # One scale for the whole sheet -- an engineering drawing has to be
    # comparable across views.  Size the cells to the content instead of
    # padding every view out to a fixed box.
    pad = 0.90
    scale = min(cell_w * pad / s[2] for s in spans.values())
    needed_h = max(s[3] * scale for s in spans.values()) / pad
    max_h = page * 0.62 / max(1, rows)
    if needed_h > max_h:
        scale *= max_h / needed_h
        needed_h = max_h
    cell_h = max(needed_h, 140.0)

    height = int(margin * (rows + 1) + cell_h * rows + 96)
    width = int(page)

    out = []
    out.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
               'viewBox="0 0 %d %d" font-family="ui-monospace, Menlo, monospace">'
               % (width, height, width, height))
    out.append('<style>'
               '.v{fill:none;stroke-width:1.05;stroke-linecap:round;stroke-linejoin:round}'
               '.h{fill:none;stroke:#8a8a8a;stroke-width:0.7;stroke-dasharray:5 3}'
               '.lbl{font-size:15px;fill:#111}'
               '.meta{font-size:12px;fill:#555}'
               '.frame{fill:none;stroke:#d0d0d0;stroke-width:1}'
               '.axis{stroke-width:1.6;fill:none}'
               '</style>')
    # "100%", not "100%%": nothing formats this string, so the doubled percent
    # that would be right in a `%`-operator argument reaches the SVG literally
    # and the browser rejects the attribute. The sheet has been drawing without
    # its background ever since -- invisible on white paper, and invisible in a
    # page that paints its own, which is where it was finally noticed.
    out.append('<rect width="100%" height="100%" fill="#ffffff"/>')

    for k, view in enumerate(view_names):
        r, c = divmod(k, cols)
        x0 = margin + c * (cell_w + margin)
        y0 = margin + r * (cell_h + margin)
        out.append('<rect class="frame" x="%.1f" y="%.1f" width="%.1f" height="%.1f"/>'
                   % (x0, y0, cell_w, cell_h))
        cx = x0 + cell_w / 2.0
        cy = y0 + cell_h / 2.0
        lo, hi, _, _ = spans[view]
        ox = cx - ((lo[0] + hi[0]) / 2.0) * scale
        oy = cy + ((lo[1] + hi[1]) / 2.0) * scale

        data = view_data[view]

        # Everything a caller needs to put a world point into this view, which
        # is otherwise unrecoverable: `_svg_path` bakes the projection into the
        # coordinates and nothing downstream can invert it. With these, a page
        # can draw an approach arrow, a dimension or a marker into the sheet at
        # a place that means something.
        #
        # screen_x = ox + dot(p, right) * scale
        # screen_y = oy - dot(p, up) * scale,  up = dir x right
        direction, right = VIEWS[view]
        d, r = _norm(direction), _norm(right)
        up = (d[1] * r[2] - d[2] * r[1],
              d[2] * r[0] - d[0] * r[2],
              d[0] * r[1] - d[1] * r[0])
        out.append('<g class="view" data-view="%s" data-ox="%.4f" data-oy="%.4f" '
                   'data-scale="%.6f" data-right="%.6f,%.6f,%.6f" '
                   'data-up="%.6f,%.6f,%.6f" data-dir="%.6f,%.6f,%.6f" '
                   'data-frame="%.1f,%.1f,%.1f,%.1f">'
                   % (_esc(view), ox, oy, scale, r[0], r[1], r[2],
                      up[0], up[1], up[2], d[0], d[1], d[2],
                      x0, y0, cell_w, cell_h))

        def tag(idx):
            if not part_ids or idx >= len(part_ids) or part_ids[idx] is None:
                return ""
            return ' data-part="%s"' % _esc(part_ids[idx])

        if hidden:
            for idx, entry in data.items():
                lines = [_svg_path(line, ox, oy, scale, True)
                         for line in entry["hidden"]]
                if lines:
                    out.append('<path class="h"%s d="%s"/>'
                               % (tag(idx), " ".join(lines)))
        for idx, entry in data.items():
            color = part_colors.get(idx) or "#111111"
            paths = [_svg_path(line, ox, oy, scale, True) for line in entry["visible"]]
            if paths:
                out.append('<path class="v"%s stroke="%s" d="%s"/>'
                           % (tag(idx), color, " ".join(paths)))
        out.append("</g>")
        out.append('<text class="lbl" x="%.1f" y="%.1f">%s</text>'
                   % (x0 + 8, y0 + 20, view.upper()))

    # title block
    ty = height - 62
    out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#d0d0d0"/>'
               % (margin, ty, width - margin, ty))
    out.append('<text class="lbl" x="%d" y="%.1f">%s</text>'
               % (margin, ty + 24, _esc(model_name)))
    meta = "size %.2f x %.2f x %.2f mm" % size_mm if size_mm else ""
    if scale_note:
        meta += "    " + scale_note
    meta += "    %d parts    %s" % (len(part_names),
                                    "hidden lines shown" if hidden else "visible edges only")
    out.append('<text class="meta" x="%d" y="%.1f">%s</text>'
               % (margin, ty + 44, _esc(meta)))

    # scale bar: a round number of millimetres
    bar_mm = _nice(max(s[2] for s in spans.values()) / 6.0)
    bar_px = bar_mm * scale
    bx = width - margin - bar_px
    by = ty + 36
    out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#111" '
               'stroke-width="2"/>' % (bx, by, bx + bar_px, by))
    for tick in (bx, bx + bar_px):
        out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#111" '
                   'stroke-width="2"/>' % (tick, by - 5, tick, by + 5))
    out.append('<text class="meta" x="%.1f" y="%.1f" text-anchor="middle">%g mm</text>'
               % (bx + bar_px / 2.0, by + 20, bar_mm))

    if dims:
        out.append('<text class="meta" x="%d" y="%.1f">%s</text>'
                   % (margin, ty + 60, _esc(dims)))
    out.append('</svg>')
    return "\n".join(out)


def _ink(rgb, target=0.42):
    """Darken a CAD colour until a 1 px stroke of it reads on white paper.

    STEP palettes are full of near-white plastics and satin steels; drawn
    literally they vanish.  Hue is preserved, luminance is capped.
    """
    lum = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    k = 1.0 if lum <= target else target / max(lum, 1e-6)
    return "#%02X%02X%02X" % tuple(
        max(0, min(255, int(round(c * k * 255)))) for c in rgb)


def _nice(v):
    if v <= 0:
        return 1.0
    exp = math.floor(math.log10(v))
    base = v / (10 ** exp)
    for step in (1, 2, 5, 10):
        if base <= step:
            return step * (10 ** exp)
    return 10 ** (exp + 1)


def _esc(s):
    # Quotes too: this escapes attribute values as well as text nodes now that
    # part ids land in data-part, and &quot; in a text node is harmless.
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def run(path, out_path, views=None, parts=None, hidden=False, color=False,
        page=1600, deflection=None, dims=False, smooth=True):
    """Render *path* and write the SVG to *out_path*."""
    import occenv
    occenv.ensure_occ()
    import occshapes as O

    svg, info = render(O.load_parts(path), views, parts, hidden, color, page,
                       deflection, dims, smooth,
                       title=os.path.basename(path), color_source=path)
    with open(out_path, "w") as fh:
        fh.write(svg)
    return dict([("output", os.path.abspath(out_path))] + list(info.items()))


def render(loaded, views=None, parts=None, hidden=False, color=False,
           page=1600, deflection=None, dims=False, smooth=True, title="",
           color_source=None, id_of=None, deadline=None):
    """As :func:`run`, but on already-loaded parts and returning the SVG.

    Returns ``(svg_string, info)``; nothing is written to disk, which is what
    a caller embedding the drawing in a report or running somewhere with no
    writable directory needs.

    *deadline* is an absolute ``time.time()`` checked between views.  Hidden-
    line removal is one call per view and cannot be interrupted inside, so a
    sheet of the views that fit is the finest the budget can be honoured at;
    ``info["views"]`` then lists only those, and ``incomplete`` says so.
    """
    import occshapes as O

    items = [p for p in loaded
             if C.match(p.name, parts) or C.match(p.path, parts)]
    if not items:
        raise SystemExit("no parts matched %r" % (parts,))

    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for p in items:
        extent = O.bbox_or_none(p.shape)
        if extent is None:
            continue          # bodiless occurrence: contributes no extent
        a, b = extent
        for i in range(3):
            lo[i] = min(lo[i], a[i])
            hi[i] = max(hi[i], b[i])
    size = tuple(hi[i] - lo[i] for i in range(3))
    if deflection is None:
        deflection = max(max(size) / 800.0, 1e-3)

    names = views or DEFAULT_SHEET
    for v in names:
        if v not in VIEWS:
            raise SystemExit("unknown view %r (choose from %s)"
                             % (v, ", ".join(sorted(VIEWS))))

    view_data = {}
    started = time.time()
    for v in names:
        # hidden-line removal is one uninterruptible call per view, so the
        # same look-ahead as step_clash applies; always draw the first one,
        # because a sheet with fewer views beats no sheet at all
        if deadline is not None and view_data:
            now = time.time()
            if now + (now - started) / len(view_data) > deadline:
                break
        view_data[v] = project_edges(items, v, deflection, hidden=hidden,
                                     smooth=smooth)
    drawn = [v for v in names if v in view_data]

    colors = {}
    if color:
        rgbs = _colors_by_name(color_source) if color_source else {}
        for idx, p in enumerate(items):
            rgb = p.color or rgbs.get(p.name)
            if rgb:
                colors[idx] = _ink(rgb)

    dim_note = None
    if dims:
        dim_note = ("overall X %.3f .. %.3f | Y %.3f .. %.3f | Z %.3f .. %.3f"
                    % (lo[0], hi[0], lo[1], hi[1], lo[2], hi[2]))

    part_ids = [id_of(p) for p in items] if id_of is not None else None
    svg = build_svg(title, view_data, drawn,
                    [p.name for p in items], colors, size, page=page,
                    hidden=hidden, dims=dim_note, part_ids=part_ids)
    edges = sum(len(e["visible"]) for d in view_data.values() for e in d.values())
    hedges = sum(len(e["hidden"]) for d in view_data.values() for e in d.values())
    info = {"views": drawn, "parts": len(items),
            "visible_polylines": edges, "hidden_polylines": hedges,
            "size_mm": list(size), "deflection": deflection}
    if part_ids is not None:
        info["part_ids"] = part_ids
    if deadline is not None:
        info["incomplete"] = len(drawn) < len(names)
    return svg, info


def _colors_by_name(source):
    """Per-product RGB from the text tier (OCC often loses STEP styling).

    *source* is a path, or a :class:`stepcore.StepFile` that has already been
    parsed -- a caller running several collectors over one file should not pay
    for a second parse just to colour the strokes.
    """
    try:
        import stepcore
        sf = source if isinstance(source, stepcore.StepFile) else stepcore.load(source)
        colors = sf.colors()
        out = {}
        for root in sf.assembly():
            for n in root.walk():
                for s in n.solids:
                    c = colors.get(s.id)
                    if c and n.product not in out:
                        out[n.product] = (c[0], c[1], c[2])
        return out
    except Exception:
        return {}


def main(argv=None):
    ap = C.base_parser(__doc__)
    ap.add_argument("-o", "--output", default=None, help="SVG path to write")
    ap.add_argument("--view", action="append", default=[],
                    help="view name (%s); repeatable" % ", ".join(sorted(VIEWS)))
    ap.add_argument("--part", action="append", default=[])
    ap.add_argument("--hidden", nargs="?", const="full", default=None,
                    choices=["full", "outline"],
                    help="draw hidden lines dashed: 'full' (every hidden edge, "
                         "very heavy on assemblies) or 'outline' (each hidden "
                         "part's silhouette only)")
    ap.add_argument("--color", action="store_true", help="stroke each part in its STEP colour")
    ap.add_argument("--no-smooth", action="store_true",
                    help="omit smooth (tangent) edges - cleaner on curvy parts")
    ap.add_argument("--page", type=int, default=1600, help="SVG width in px")
    ap.add_argument("--deflection", type=float, default=None,
                    help="curve discretisation, mm (default: size/800)")
    ap.add_argument("--dims", action="store_true", help="annotate overall extents")
    a = ap.parse_args(argv)
    out = a.output or (os.path.splitext(os.path.basename(a.file))[0] + ".svg")
    res = run(a.file, out, a.view or None, a.part, a.hidden, a.color, a.page,
              a.deflection, a.dims, not a.no_smooth)
    C.emit(res, a.json, lambda d: print(
        "wrote %s\n  views: %s\n  parts: %d, %d visible polylines, %d hidden\n"
        "  model size: %s mm" % (d["output"], ", ".join(d["views"]), d["parts"],
                                 d["visible_polylines"], d["hidden_polylines"],
                                 C.fmt_xyz(d["size_mm"], 2))))


if __name__ == "__main__":
    sys.exit(main())
