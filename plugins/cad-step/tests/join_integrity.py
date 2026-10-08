#!/usr/bin/env python3
"""Check that every occurrence is identified exactly once.

A combat robot is mostly duplicates -- eight standoffs, four wheels, two
motors -- and a name join mis-assigns them silently.  The failure mode is not
a crash, it is a report that looks plausible with the wrong mass against the
wrong part, so these are assertions rather than warnings.

    python3 tests/join_integrity.py FILE [max_unjoined]
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "scripts"))

import occenv  # noqa: E402

occenv.ensure_occ()

import occshapes as O   # noqa: E402
import stepcore         # noqa: E402
import stepjoin         # noqa: E402

path = sys.argv[1]
allowed_unjoined = int(sys.argv[2]) if len(sys.argv) > 2 else 0

sf = stepcore.load(path)
parts = O.load_parts(path)
index = stepjoin.build_index(sf, parts, occ_bbox=O.bbox)

fails = []


def want(cond, msg):
    if not cond:
        fails.append(msg)


# every OCC part is accounted for, and nothing was invented
want(len(index.entries) >= len(parts),
     "%d parts went in but only %d entries came out" % (len(parts), len(index.entries)))
want(len([e for e in index.entries if e.part is not None]) == len(parts),
     "some parts lost their entry")

ids = [e.id for e in index.entries]
want(len(set(ids)) == len(ids), "part ids are not unique")

# no node may back two entries -- that is the mis-assignment we are guarding
nodes = [id(e.node) for e in index.entries if e.node is not None]
want(len(set(nodes)) == len(nodes), "a node was claimed by two entries")

want(index.unjoined <= allowed_unjoined,
     "%d unjoined parts (allowed %d): %s"
     % (index.unjoined, allowed_unjoined,
        ", ".join("%s %s=%s" % (e.id, e.name, e.joined)
                  for e in index.entries if not e.trusted)))

# a join is only worth anything if the two tiers really are describing the
# same lump of metal: their world placements must agree
for e in index.entries:
    if e.part is None or e.node is None:
        continue
    if not stepjoin._same_transform(e.part.world, e.node.world, tol=1e-6):
        fails.append("%s %s: OCC and stepcore disagree on where it sits"
                     % (e.id, e.name))

# duplicated products must come out as separate, distinguishable occurrences
by_pd = {}
for e in index.entries:
    if e.node is not None:
        by_pd.setdefault(e.node.pd_id, []).append(e)
dups = {pd: es for pd, es in by_pd.items() if len(es) > 1}
for pd, es in dups.items():
    chains = [tuple(e.node.nauo_chain) for e in es]
    want(len(set(chains)) == len(chains),
         "product %s is placed %d times but the occurrences share a NAUO chain"
         % (pd, len(es)))

print("%s: %d parts, %d entries, %d products placed more than once, %s"
      % (os.path.basename(path), len(parts), len(index.entries), len(dups),
         index.counts()))
for f in fails:
    print("  FAIL %s" % f)
raise SystemExit(1 if fails else 0)
