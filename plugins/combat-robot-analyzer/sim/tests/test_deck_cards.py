"""The deck cards v2 added to build_deck: rigid bodies, spin about an axis,
free-standing nodes, and erosion derived from eps_max."""

import numpy as np
import pytest

from build_deck import derived_failure, inivel_axis_cards, rbody_cards


def _floats(line):
    return [float(line[i:i + 20]) for i in range(0, len(line), 20) if line[i:i + 20].strip()]


def test_the_spin_axis_is_the_frames_y_line_and_dir_y():
    """/FRAME/FIX's vector lines are local Y and Z; spinning about DIR = X
    with the axis on the first line turned the body about a perpendicular
    axis (caught on the fixture spike)."""
    lines = inivel_axis_cards(1, {"name": "spin", "axis": {
        "origin": [55, 15, 10], "direction": [0, 2, 0], "omega": -666.7}}, grnod_id=7)
    y_axis = _floats(lines[5])
    assert y_axis == pytest.approx([0.0, 1.0, 0.0])
    assert lines[11].split() == ["Y", "1", "7"]
    assert _floats(lines[13])[3] == pytest.approx(-666.7)


def test_translation_is_converted_into_the_frame():
    lines = inivel_axis_cards(1, {"axis": {"origin": [0, 0, 0], "direction": [0, 0, 1], "omega": 1.0},
                                  "vector": [0, 0, 5.0]}, grnod_id=1)
    vxt, vyt, vzt, _ = _floats(lines[13])
    assert (vxt, vyt, vzt) == pytest.approx((0.0, 5.0, 0.0))  # along the spin axis = local Y


def test_rbody_adds_mass_and_inertia_at_the_main_node():
    lines = rbody_cards(2, {"name": "weapon", "mass": 5.4e-3, "inertia": [1, 2, 3, 0, 0, 0]},
                        main_node=99, grnod_id=4)
    assert lines[0] == "/RBODY/2"
    head = lines[3]
    # node(10) sens(10) skew(10) ispher(10) mass(20) grnd(10) ikrem(10) icog(10)
    assert int(head[0:10]) == 99 and int(head[60:70]) == 4 and int(head[80:90]) == 1  # ICOG = 1
    assert float(head[40:60]) == pytest.approx(5.4e-3)
    assert _floats(lines[5]) == pytest.approx([1.0, 2.0, 3.0])


def test_erosion_derives_johnson_cook_d1_from_eps_max():
    assert derived_failure({"law": "johnson_cook", "eps_max": 0.28}) == {"model": "johnson_cook", "d1": 0.28}
    assert derived_failure({"law": "elastic", "eps_max": 0.28}) is None
    assert derived_failure({"law": "johnson_cook"}) is None


def test_elements_dead_at_the_first_frame_are_rigid_not_eroded(tmp_path, monkeypatch):
    """OpenRadioss reports /RBODY elements as status 0 from t = 0."""
    import extract_damage as ed

    frames = [{"time": 0.0, "status": np.array([1, 1, 0])},
              {"time": 1e-5, "status": np.array([1, 0, 0])}]

    def fake_read(path, want=None, geometry=False):
        fr = frames[int(path)]
        out = {"time": fr["time"], "fields": {"strain": "S", "stress": None, "damage": None, "alive": "A"},
               "PART_ID": np.array([1.0, 1.0, 2.0]), "ELEMENT_ID": np.array([1.0, 2.0, 3.0]),
               "S": np.zeros(3), "A": fr["status"].astype(float)}
        if geometry:
            out.update(points=np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1.0]]),
                       cells=np.array([[0, 1, 2, 3]] * 3), cell_types=np.array([10, 10, 10]))
        return out

    monkeypatch.setattr(ed, "read_frame", fake_read)
    data = ed.track(["0", "1"], quiet=True)
    assert data["rigid"].tolist() == [False, False, True]
    assert (data["erode_frame"] >= 0).tolist() == [False, True, False]
