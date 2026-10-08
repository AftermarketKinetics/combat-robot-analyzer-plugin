import math

import pytest

from case.weapon import (END_TIME_CAP_S, END_TIME_FLOOR_S, estimate_end_time, infer_up,
                         parse_up, swing, weapon_inertia)


def _close(a, b, tol=1e-6):
    return all(abs(x - y) < tol for x, y in zip(a, b))


def test_up_is_the_thinnest_dimension():
    assert infer_up([0, 0, 0], [400, 420, 73]) == (0.0, 0.0, 1.0)
    assert infer_up([0, 0, 0], [400, 60, 300]) == (0.0, 1.0, 0.0)
    assert parse_up("-y", [0, 0, 0], [1, 1, 1]) == (0.0, -1.0, 0.0)


def test_horizontal_spinner_hits_a_vertical_wall_head_on_and_the_arc_passes_through_it():
    # wall facing -x at x=0; robot centre at +x; up = z
    s = swing((0, 0, 10), (-1, 0, 0), (0, 0, 1), "horizontal", 150.0, 100.0, (200, 0, 10))
    assert _close(s.tip_velocity, (1, 0, 0))
    assert _close([abs(c) for c in s.axis], (0, 0, 1))
    # the strike point is on the arc, and the hub is outside the robot
    assert math.dist(s.hub, (0, 0, 10)) == pytest.approx(150.0)
    assert s.hub[0] < 0 or abs(s.hub[1]) > 0
    assert math.dist(s.hub, (200, 0, 10)) > math.dist((0, 0, 10), (200, 0, 10))
    # rotation about the axis at omega gives the tooth +x velocity of v_tip
    r = [p - h for p, h in zip((0, 0, 10), s.hub)]
    v = [s.omega * c for c in (s.axis[1] * r[2] - s.axis[2] * r[1],
                               s.axis[2] * r[0] - s.axis[0] * r[2],
                               s.axis[0] * r[1] - s.axis[1] * r[0])]
    assert _close(v, (100_000.0, 0, 0), tol=1e-3)


def test_a_sloped_wedge_is_struck_obliquely():
    n = (-math.sqrt(0.5), 0, math.sqrt(0.5))  # 45° wedge facing -x, up
    s = swing((0, 0, 0), n, (0, 0, 1), "horizontal", 100.0, 100.0, (200, 0, 0))
    # in the horizontal plane the tooth still moves +x, so it meets the slope at 45°
    assert _close(s.tip_velocity, (1, 0, 0))
    assert sum(a * b for a, b in zip(s.tip_velocity, n)) == pytest.approx(-math.sqrt(0.5))


def test_a_drum_spins_about_a_horizontal_axis_across_the_attack():
    s = swing((0, 0, 0), (-1, 0, 0), (0, 0, 1), "vertical_drum", 60.0, 100.0, (200, 0, 0))
    assert abs(s.axis[2]) < 1e-9 and abs(abs(s.axis[1]) - 1) < 1e-9


def test_a_face_in_the_plane_of_rotation_cannot_be_struck():
    with pytest.raises(ValueError, match="parallel"):
        swing((0, 0, 0), (0, 0, 1), (0, 0, 1), "horizontal", 100.0, 100.0, (0, 0, -50))


def test_inertia_has_the_spin_value_about_the_axis():
    J = weapon_inertia((0, 0, 1), 10.0)
    assert J[2] == pytest.approx(10.0) and J[0] == pytest.approx(5.0) and J[3] == pytest.approx(0.0)


def test_end_time_is_clamped_and_grows_with_energy():
    hard = estimate_end_time(10.0, 100.0, 1000.0, 100.0)
    assert hard == END_TIME_FLOOR_S
    assert estimate_end_time(2500.0, 98.3, 435.0, 118.0) == END_TIME_CAP_S
    mid = estimate_end_time(60.0, 120.0, 435.0, 60.0)
    assert END_TIME_FLOOR_S < mid < END_TIME_CAP_S
