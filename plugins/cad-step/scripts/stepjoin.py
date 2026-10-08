#!/usr/bin/env python3
"""Join the text tier's assembly occurrences to the OCC tier's located shapes.

The two tiers of this plugin see the same file through different eyes.
:mod:`stepcore` reads the exchange structure and knows every occurrence by the
``NEXT_ASSEMBLY_USAGE_OCCURRENCE`` entities that placed it; :mod:`occshapes`
goes through OpenCASCADE's XCAF document and knows every occurrence by an
XCAF label name.  Names are not an identity: eight identical standoffs under
one parent share a name and a path, and the two tiers do not even agree on
which name to use (stepcore takes the PRODUCT name, XCAF prefers the
component's instance name).

This module produces one :class:`Entry` per occurrence, carrying an opaque
``id`` that both tiers can key on, and says how each join was reached so a
caller can refuse to trust the doubtful ones:

``exact``       matched through STEP entity ids recovered from the transfer
``geometry``    matched by world transform or bounding-box centre
``name``        matched by name path only -- weak, may be wrong for duplicates
``ambiguous``   several candidates were equally good; no assignment made
``unmatched``   one tier has this occurrence and the other does not

Only ``exact`` and ``geometry`` are safe to join user-entered data against.
"""

from __future__ import annotations

import math

TOL_XYZ = 1e-6      # world transform agreement, mm and unitless

# Bounding boxes are the last resort and cannot be held to microns: the text
# tier measures vertex positions only, so a curved part's box is an
# underestimate against OpenCASCADE's true one -- by 13 mm on the worst part
# of the 600 mm corpus model.  All three are fractions of the model diagonal.
NEAR_FRAC = 0.05    # beyond this the boxes are not the same part
CLEAR_FRAC = 0.005  # a runner-up this close means we cannot tell them apart
FIT_FRAC = 1e-4     # slack when testing one box for containment in another


class Entry(object):
    """One occurrence, as seen by either or both tiers."""

    __slots__ = ("id", "part", "node", "joined", "name", "path")

    def __init__(self, eid, part=None, node=None, joined="unmatched"):
        self.id = eid
        self.part = part
        self.node = node
        self.joined = joined
        self.name = (part.name if part is not None
                     else (node.product if node is not None else "?"))
        self.path = (node.path() if node is not None
                     else (part.path if part is not None else ""))

    @property
    def trusted(self):
        return self.joined in ("exact", "geometry")

    def __repr__(self):
        return "<Entry %s %s %s>" % (self.id, self.name, self.joined)


class PartIndex(object):
    """The result of :func:`build_index`; lookups from either tier to an id."""

    def __init__(self, entries):
        self.entries = entries
        self._by_node = {}
        self._by_solid = {}
        self._by_part = {}
        for e in entries:
            if e.node is not None:
                self._by_node[id(e.node)] = e
                for s in e.node.solids:
                    # a solid belongs to a product, and a product may be
                    # placed more than once; first occurrence wins, which is
                    # the best a solid-keyed lookup can do
                    self._by_solid.setdefault(s.id, e)
            if e.part is not None:
                self._by_part[id(e.part)] = e

    def for_node(self, node):
        return self._by_node.get(id(node))

    def for_part(self, part):
        return self._by_part.get(id(part))

    def for_solid(self, solid_id):
        return self._by_solid.get(solid_id)

    def id_for_node(self, node):
        e = self.for_node(node)
        return e.id if e else None

    def id_for_part(self, part):
        e = self.for_part(part)
        return e.id if e else None

    def id_for_solid(self, solid_id):
        e = self.for_solid(solid_id)
        return e.id if e else None

    @property
    def unjoined(self):
        """Occurrences whose join is not safe to key user data against."""
        return sum(1 for e in self.entries if not e.trusted)

    def counts(self):
        out = {}
        for e in self.entries:
            out[e.joined] = out.get(e.joined, 0) + 1
        return out


# -- product lookup ---------------------------------------------------------

def _nodes_by_entity(sf, nodes):
    """``{step entity id: [AssemblyNode]}`` for solids and shape representations.

    A leaf's transfer result is either the ``MANIFOLD_SOLID_BREP`` itself or
    the shape representation that holds it, so both routes have to be here.
    The value is a list because a product placed *n* times is *n* occurrences,
    and because exporters do sometimes hang one solid off two products.
    """
    by_pd = {}
    for n in nodes:
        by_pd.setdefault(n.pd_id, []).append(n)
    out = {}
    for n in nodes:
        for s in n.solids:
            out.setdefault(s.id, []).append(n)
        # a component whose placement happens to be the identity resolves to
        # the NAUO rather than to the product's own solid, and that names the
        # occurrence outright
        if n.nauo_id is not None:
            out.setdefault(n.nauo_id, []).append(n)
    for pd, reps in sf.shape_reps().items():
        for r in reps:
            bucket = out.setdefault(int(r), [])
            seen = set(id(x) for x in bucket)
            bucket.extend(n for n in by_pd.get(pd, []) if id(n) not in seen)
    return out


def _chain_fits(occ_chain, node):
    """Is the OCC tier's partial NAUO chain consistent with *node*?

    XCAF cannot name the NAUO behind every reference -- a reference to a
    subassembly resolves to a compound that was never a transfer result, and
    comes back as ``None``.  The known ids must therefore appear in order
    within the node's chain rather than line up position for position, and
    the deepest known id must be the one that placed the node itself.
    """
    known = [x for x in occ_chain if x is not None]
    if not known:
        return True
    if known[-1] != node.nauo_id:
        return False
    it = iter(node.nauo_chain)
    return all(any(c == k for c in it) for k in known)


# -- geometry ---------------------------------------------------------------

def _node_world_box(sf, node):
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for s in node.solids:
        b = sf.solid_bounds(s)
        if b is None:
            continue
        (x0, y0, z0), (x1, y1, z1) = b
        for cx in (x0, x1):
            for cy in (y0, y1):
                for cz in (z0, z1):
                    p = node.world.apply((cx, cy, cz))
                    for i in range(3):
                        lo[i] = min(lo[i], p[i])
                        hi[i] = max(hi[i], p[i])
    if lo[0] == float("inf"):
        return None
    return lo, hi


def _deviation(outer, inner):
    """How far *inner*'s corners fall short of *outer*'s -- the match score.

    Centre distance looks like the obvious score and is a trap: an assembly is
    full of concentric parts, and a small one sitting inside a big curved one
    can have the closer centre while being obviously the wrong size.  Corner
    agreement gets that pair the right way round.
    """
    return max(max(abs(inner[0][i] - outer[0][i]), abs(inner[1][i] - outer[1][i]))
               for i in range(3))


def _dist(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def _contains(outer, inner, tol):
    """Does *outer* enclose *inner*?

    The text tier's box comes from vertex positions alone, so a curved face
    bulges outside it but never the other way round -- ``tests/cross_tier.py``
    holds the whole corpus to that.  Which makes containment a real filter on
    "could these two boxes be the same part", where comparing extents directly
    would just be comparing an underestimate to the truth.
    """
    return all(inner[0][i] >= outer[0][i] - tol
               and inner[1][i] <= outer[1][i] + tol for i in range(3))


def _model_span(sf):
    """Diagonal of the whole model, the yardstick for "close enough"."""
    b = sf.world_bounds()
    if b is None:
        return 1.0
    return max(_dist(b[0], b[1]), 1e-6)


def _same_transform(part_world, node_world, tol=TOL_XYZ):
    if part_world is None:
        return False
    for i in range(3):
        for j in range(3):
            if abs(part_world[i][j] - node_world.r[i][j]) > tol:
                return False
        if abs(part_world[i][3] - node_world.t[i]) > tol:
            return False
    return True


# -- the join ---------------------------------------------------------------

def build_index(sf, parts, occ_bbox=None):
    """Pair every OCC :class:`occshapes.Part` with its :class:`stepcore.AssemblyNode`.

    *occ_bbox* is ``occshapes.bbox``; pass it to enable the bounding-box
    fallback, omit it to stay text-only and cheap.  Returns a :class:`PartIndex`
    whose entries cover the union of both tiers -- an occurrence only one tier
    can see still gets an id, marked ``unmatched``.
    """
    nodes = [n for r in sf.assembly() for n in r.walk() if n.solids]
    by_entity = _nodes_by_entity(sf, nodes)

    taken = set()
    assigned = []      # (part, node|None, method)

    def free(pool):
        return [n for n in pool if id(n) not in taken]

    def candidates(part):
        """Nodes this part could be, narrowed by STEP entity id where possible.

        An entity we cannot place -- XCAF wraps stray geometry in compounds
        that were never a transfer result -- means "could be anything", not
        "could be nothing", so it widens back to every node.
        """
        if part.entity is None:
            return nodes, False
        pool = by_entity.get(part.entity)
        if not pool:
            return nodes, False
        return pool, True

    # pass 1 -- STEP entity ids, the only join that cannot be wrong
    pending = []
    for p in parts:
        pool, known = candidates(p)
        if not known:
            pending.append(p)
            continue
        cands = free(pool)
        if len(cands) > 1:
            narrowed = [n for n in cands if _chain_fits(p.nauo_chain, n)]
            if narrowed:
                cands = narrowed
        if len(cands) == 1:
            taken.add(id(cands[0]))
            assigned.append((p, cands[0], "exact"))
        else:
            pending.append(p)

    # pass 2 -- world transform; both tiers compute it from the same
    # placements, so agreement here is arithmetic rather than a heuristic
    still = []
    for p in pending:
        pool, _ = candidates(p)
        cands = [n for n in free(pool) if _same_transform(p.world, n.world)]
        if len(cands) > 1:
            narrowed = [n for n in cands if _chain_fits(p.nauo_chain, n)]
            if narrowed:
                cands = narrowed
        if len(cands) == 1:
            taken.add(id(cands[0]))
            assigned.append((p, cands[0], "geometry"))
        elif cands:
            assigned.append((p, None, "ambiguous"))
        else:
            still.append(p)

    # pass 3 -- bounding boxes, best agreement wins, ties refused
    if occ_bbox is not None and still:
        boxes = {}
        for n in free(nodes):
            b = _node_world_box(sf, n)
            if b is not None:
                boxes[id(n)] = (n, b)
        span = _model_span(sf)
        near, clear, fit = NEAR_FRAC * span, CLEAR_FRAC * span, FIT_FRAC * span
        for p in still[:]:
            if not boxes:
                break
            box = occ_bbox(p.shape)
            # the vertex box can only be smaller than the true one, never
            # larger, so containment is a real filter rather than a guess
            ranked = sorted(((_deviation(box, b), n, b)
                             for n, b in boxes.values() if _contains(box, b, fit)),
                            key=lambda t: t[0])
            if not ranked or ranked[0][0] > near:
                continue                # nothing here is plausibly that part
            if len(ranked) > 1 and ranked[1][0] - ranked[0][0] < clear:
                assigned.append((p, None, "ambiguous"))
                still.remove(p)
                continue
            best = ranked[0][1]
            taken.add(id(best))
            del boxes[id(best)]
            assigned.append((p, best, "geometry"))
            still.remove(p)

    # pass 4 -- names, last resort, and only when it is unambiguous
    for p in still:
        cands = [n for n in free(nodes) if n.product == p.name]
        if len(cands) == 1:
            taken.add(id(cands[0]))
            assigned.append((p, cands[0], "name"))
        else:
            assigned.append((p, None, "ambiguous" if cands else "unmatched"))

    # ids follow the OCC part order, then the text-only leftovers, so they are
    # stable for a given file without depending on any name
    order = {id(p): i for i, p in enumerate(parts)}
    assigned.sort(key=lambda t: order[id(t[0])])
    entries = [Entry("p%03d" % i, p, n, m)
               for i, (p, n, m) in enumerate(assigned)]
    for n in nodes:
        if id(n) not in taken:
            entries.append(Entry("p%03d" % len(entries), None, n, "unmatched"))
    return PartIndex(entries)

