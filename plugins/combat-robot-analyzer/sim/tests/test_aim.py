"""Is the strike on the target? The geometry behind B3.5's aim gate.

Everything here runs on an axis-aligned box, because a box is the one solid
whose exact answer can be written down by hand: a face-centre aim is *exactly*
on the surface, so a gap of anything but zero is the code being wrong rather
than the mesh being coarse.
"""

from __future__ import annotations

import base64
import struct

import pytest

from case.aim import (
    AIM_TOLERANCE_MM,
    first_hit,
    grade_aim,
    is_inside,
    load_surface,
    travel_mm,
)

#: A 12 lb tooth: 4.68 mm of travel in the 40 us simulated.
TRAVEL = 4.68


def box(lo=(0.0, 0.0, 0.0), hi=(40.0, 300.0, 12.0)):
    """The twelve triangles of an axis-aligned box."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = [
        (x0, y0, z0),
        (x1, y0, z0),
        (x1, y1, z0),
        (x0, y1, z0),
        (x0, y0, z1),
        (x1, y0, z1),
        (x1, y1, z1),
        (x0, y1, z1),
    ]
    quads = [
        (0, 3, 2, 1),  # -Z
        (4, 5, 6, 7),  # +Z
        (0, 1, 5, 4),  # -Y
        (2, 3, 7, 6),  # +Y
        (0, 4, 7, 3),  # -X
        (1, 2, 6, 5),  # +X
    ]
    tris = []
    for a, b, c, d in quads:
        tris.append((v[a], v[b], v[c]))
        tris.append((v[a], v[c], v[d]))
    return tris


FACES = {
    # name: (aim point, outward direction)
    "+Z": ((20.0, 150.0, 12.0), (0.0, 0.0, 1.0)),
    "-Z": ((20.0, 150.0, 0.0), (0.0, 0.0, -1.0)),
    "+X": ((40.0, 150.0, 6.0), (1.0, 0.0, 0.0)),
    "-X": ((0.0, 150.0, 6.0), (-1.0, 0.0, 0.0)),
    "+Y": ((20.0, 300.0, 6.0), (0.0, 1.0, 0.0)),
    "-Y": ((20.0, 0.0, 6.0), (0.0, -1.0, 0.0)),
}


@pytest.mark.parametrize("face", sorted(FACES))
def test_a_strike_on_any_face_is_on_the_surface(face):
    """Every face, not just the convenient one.

    The parity test that decides inside from outside is ill-defined for a
    point lying exactly on the surface, and its three fixed directions all
    have a positive Z component — so an aim on the -Z face used to be graded
    'inside the part'. Which is every aim on the underside of a bot.
    """
    point, direction = FACES[face]
    v = grade_aim(point, direction, box(), travel=TRAVEL, target_id="p000")
    assert v.status == "on_surface", f"{face}: {v.message}"
    assert v.gap_mm == pytest.approx(0.0, abs=1e-9)
    assert not v.blocks and not v.warns


def test_a_strike_just_off_the_surface_warns_but_does_not_block():
    point = (20.0, 150.0, 12.0 + 1.0)
    v = grade_aim(point, (0.0, 0.0, 1.0), box(), travel=TRAVEL, target_id="p000")
    assert v.status == "marginal"
    assert v.gap_mm == pytest.approx(1.0)
    assert v.warns and not v.blocks
    assert "1.00 mm off" in v.message


def test_a_gap_inside_the_measured_tolerance_is_silent():
    """Two tessellations of one CAD face disagree by up to 0.08 mm measured.

    Refusing that would be refusing a disagreement the pipeline created.
    """
    point = (20.0, 150.0, 12.0 + AIM_TOLERANCE_MM * 0.8)
    v = grade_aim(point, (0.0, 0.0, 1.0), box(), travel=TRAVEL, target_id="p000")
    assert v.status == "on_surface"


def test_a_strike_further_out_than_the_tooth_travels_blocks():
    point = (20.0, 150.0, 12.0 + 55.0)
    v = grade_aim(point, (0.0, 0.0, 1.0), box(), travel=TRAVEL, target_id="p000")
    assert v.status == "beyond_travel"
    assert v.blocks
    assert "55.0 mm clear" in v.message and "4.7 mm" in v.message


def test_a_strike_inside_the_material_blocks():
    point = (20.0, 150.0, 6.0)
    v = grade_aim(point, (0.0, 0.0, 1.0), box(), travel=TRAVEL, target_id="p000")
    assert v.status == "inside"
    assert v.blocks
    assert "inside the part" in v.message


def test_an_approach_that_misses_the_part_blocks():
    """The line of travel never meets the target at all — five corpus cases."""
    point = (200.0, 150.0, 6.0)
    v = grade_aim(point, (0.0, 0.0, 1.0), box(), travel=TRAVEL, target_id="p000")
    assert v.status == "no_intersection"
    assert v.gap_mm is None
    assert v.blocks


def test_an_oblique_strike_on_a_face_is_still_on_the_surface():
    """Direction need not be the face normal; the gap is measured along it."""
    point = (20.0, 150.0, 12.0)
    d = (0.0, 0.6, 0.8)
    v = grade_aim(point, d, box(), travel=TRAVEL, target_id="p000")
    assert v.status == "on_surface"


def test_no_geometry_is_not_silently_on_the_surface():
    v = grade_aim((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), [], travel=TRAVEL, target_id="p000")
    assert v.status == "no_geometry"
    assert not v.blocks  # B3.5 raises it separately; it is our fault, not the user's


def test_inside_and_outside_agree_with_the_box():
    assert is_inside((20.0, 150.0, 6.0), box())
    assert not is_inside((20.0, 150.0, 100.0), box())
    assert not is_inside((-5.0, 150.0, 6.0), box())


def test_first_hit_measures_from_the_origin():
    assert first_hit((20.0, 150.0, 50.0), (0.0, 0.0, -1.0), box()) == pytest.approx(38.0)
    assert first_hit((20.0, 150.0, 50.0), (0.0, 0.0, 1.0), box()) is None


def test_travel_is_speed_times_end_time():
    """3.9 to 4.8 mm across the classes, at the 40 us the deck simulates."""
    assert travel_mm(117.0, 4e-5) == pytest.approx(4.68)
    assert travel_mm(97.5, 4e-5) == pytest.approx(3.9)


# ── the wire format ───────────────────────────────────────────────────


def _doc(tris, lo=(0.0, 0.0, 0.0), span=(400.0, 400.0, 400.0)):
    """One part's triangles, packed the way the tessellator packs them."""
    verts: list[tuple[float, float, float]] = []
    index: dict[tuple[float, float, float], int] = {}
    idx: list[int] = []
    for tri in tris:
        for p in tri:
            if p not in index:
                index[p] = len(verts)
                verts.append(p)
            idx.append(index[p])
    nv = len(verts)
    packed = b"".join(
        struct.pack("<H", round((v[k] - lo[k]) * 65535.0 / span[k]))
        for k in range(3)
        for v in verts
    )
    return {
        "lo": list(lo),
        "span": list(span),
        "parts": [
            {
                "id": "p000",
                "nv": nv,
                "verts": base64.b64encode(packed).decode(),
                "tris": base64.b64encode(b"".join(struct.pack("<I", i) for i in idx)).decode(),
            }
        ],
    }


def test_load_surface_round_trips_through_the_quantisation():
    tris = box()
    got = load_surface(_doc(tris), "p000")
    assert len(got) == len(tris)
    # uint16 over a 400 mm frame is a 6.1 um step; the geometry survives it.
    flat_in = sorted(tuple(round(c, 2) for c in p) for t in tris for p in t)
    flat_out = sorted(tuple(round(c, 2) for c in p) for t in got for p in t)
    assert flat_in == flat_out


def test_load_surface_refuses_a_part_it_does_not_have():
    with pytest.raises(KeyError):
        load_surface(_doc(box()), "p999")


def test_a_quantised_face_aim_is_still_on_the_surface():
    """The user clicks in the browser, which holds quantised vertices.

    So the point B3.5 grades and the surface it grades against have both been
    through the same 16-bit grid, and the answer still has to be zero.
    """
    surface = load_surface(_doc(box()), "p000")
    v = grade_aim((20.0, 150.0, 12.0), (0.0, 0.0, 1.0), surface, travel=TRAVEL, target_id="p000")
    assert v.status == "on_surface"
    assert v.gap_mm is not None and v.gap_mm < AIM_TOLERANCE_MM


# ── occlusion ─────────────────────────────────────────────────────────


def _two_part_doc():
    """A target at z 0-12, and a shield sitting above it at z 30-40."""
    target = box((0.0, 0.0, 0.0), (40.0, 300.0, 12.0))
    shield = box((0.0, 0.0, 30.0), (40.0, 300.0, 40.0))
    doc = _doc(target)
    doc["parts"].append({**_doc(shield)["parts"][0], "id": "p001"})
    for rec, tris in (("p000", target), ("p001", shield)):
        part = next(p for p in doc["parts"] if p["id"] == rec)
        pts = [c for t in tris for c in t]
        part["bbox"] = [min(p[k] for p in pts) for k in range(3)] + [
            max(p[k] for p in pts) for k in range(3)
        ]
    return doc


def test_a_strike_shadowed_by_another_part_blocks():
    """On the right surface, and still impossible.

    The scoped model would replace the shield with a stand-in box and report
    on the tooth hitting the box, which is a confident answer about the wrong
    part.
    """
    doc = _two_part_doc()
    surface = load_surface(doc, "p000")
    v = grade_aim(
        (20.0, 150.0, 12.0),
        (0.0, 0.0, 1.0),
        surface,
        travel=TRAVEL,
        target_id="p000",
        doc=doc,
    )
    assert v.status == "occluded"
    assert v.blocker == "p001"
    assert v.blocks
    assert "p001 is in the way" in v.message


def test_the_same_strike_from_an_unshadowed_side_is_fine():
    doc = _two_part_doc()
    surface = load_surface(doc, "p000")
    v = grade_aim(
        (20.0, 150.0, 0.0),
        (0.0, 0.0, -1.0),
        surface,
        travel=TRAVEL,
        target_id="p000",
        doc=doc,
    )
    assert v.status == "on_surface"


def test_occlusion_is_not_checked_without_the_model():
    """Grading the target alone is a weaker question, and says so by omission."""
    doc = _two_part_doc()
    surface = load_surface(doc, "p000")
    v = grade_aim((20.0, 150.0, 12.0), (0.0, 0.0, 1.0), surface, travel=TRAVEL, target_id="p000")
    assert v.status == "on_surface"


def test_a_flush_neighbour_is_not_an_obstruction():
    """Two surfaces that meet are not one blocking the other."""
    target = box((0.0, 0.0, 0.0), (40.0, 300.0, 12.0))
    flush = box((0.0, 0.0, 12.0), (40.0, 300.0, 24.0))
    doc = _doc(target)
    doc["parts"].append({**_doc(flush)["parts"][0], "id": "p001"})
    for rec, tris in (("p000", target), ("p001", flush)):
        part = next(p for p in doc["parts"] if p["id"] == rec)
        pts = [c for t in tris for c in t]
        part["bbox"] = [min(p[k] for p in pts) for k in range(3)] + [
            max(p[k] for p in pts) for k in range(3)
        ]
    # Struck from below, the flush neighbour is behind the target entirely.
    v = grade_aim(
        (20.0, 150.0, 0.0),
        (0.0, 0.0, -1.0),
        load_surface(doc, "p000"),
        travel=TRAVEL,
        target_id="p000",
        doc=doc,
    )
    assert v.status == "on_surface"


def test_a_part_behind_the_target_is_not_an_obstruction():
    doc = _two_part_doc()
    # Aim at the shield from above; the target below it is behind, not in front.
    v = grade_aim(
        (20.0, 150.0, 40.0),
        (0.0, 0.0, 1.0),
        load_surface(doc, "p001"),
        travel=TRAVEL,
        target_id="p001",
        doc=doc,
    )
    assert v.status == "on_surface"
