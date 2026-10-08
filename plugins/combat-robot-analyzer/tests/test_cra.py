"""The cra package: case setup, edits, estimates and result helpers.

The sim scripts have their own tests in sim/tests; the full STEP-to-results
flow is checked by hand on fixtures/projectile_plate.step (see README)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cra import build, case_dir, results, setup_edit
from cra.case_dir import CaseError

REPORT = {"parts": [{"id": "p000", "name": "armour"}, {"id": "p001", "name": "chassis"}]}


@pytest.fixture
def case(tmp_path: Path) -> Path:
    (tmp_path / "report").mkdir()
    (tmp_path / "report" / "report.json").write_text(json.dumps(REPORT))
    return tmp_path


def test_missing_lists_what_a_build_needs(case):
    gaps = case_dir.missing(case, case_dir.load(case))
    assert any("target" in g for g in gaps) and any("weight class" in g for g in gaps)
    assert any("materials for 2 parts" in g for g in gaps)


def test_default_material_fills_unnamed_parts(case):
    setup = case_dir.load(case)
    setup["materials"] = {"p000": "tpu_shore95a"}
    setup["default_material"] = "aluminium_6061_t6"
    assert case_dir.resolved(case, setup)["materials"] == {
        "p000": "tpu_shore95a", "p001": "aluminium_6061_t6"}


def test_an_edit_is_checked_and_saved(case):
    out = setup_edit.update(case, {"target_id": "p000", "default_material": "steel_4130",
                                   "mesh_size": 2.0, "up_axis": "z"})
    assert out["setup"]["target_id"] == "p000"
    assert case_dir.load(case)["mesh_size"] == 2.0


@pytest.mark.parametrize(("patch", "match"), [
    ({"target_id": "p999"}, "no part"),
    ({"materials": {"p000": "unobtainium"}}, "unknown material"),
    ({"end_time": 1.0}, "end_time"),
    ({"mesh_size": 50}, "mesh_size"),
    ({"up_axis": "w"}, "up_axis"),
    ({"aim": {"point": [0, 0]}}, "aim"),
    ({"colour": "red"}, "unknown setup key"),
])
def test_a_bad_edit_is_refused_and_nothing_is_saved(case, patch, match):
    with pytest.raises(CaseError, match=match):
        setup_edit.update(case, {"mesh_size": 2.0, **patch})
    assert not (case / "setup.json").exists()


def test_changing_the_target_forgets_the_aim(case):
    setup_edit.update(case, {"target_id": "p000", "aim": {"point": [0, 0, 0], "direction": [0, 0, 1]}})
    assert case_dir.load(case)["aim"]["human_placed"] is True
    setup_edit.update(case, {"target_id": "p001"})
    assert case_dir.load(case)["aim"] is None


def test_the_starter_timestep_is_read_from_its_table(tmp_path):
    out = tmp_path / "s.out"
    out.write_text("junk\n NODAL TIME STEP (estimation)\n ---\n  0.1234E-07    1155\n")
    assert build.starter_nodal_timestep(out) == pytest.approx(1.234e-8)
    assert build.starter_nodal_timestep(tmp_path / "none.out") is None


def test_the_estimate_scales_with_elements_and_threads():
    one = build.estimate_minutes(100_000, 5e-4, 3e-8, 1)
    assert build.estimate_minutes(200_000, 5e-4, 3e-8, 2) == pytest.approx(one)
    assert one == pytest.approx(100_000 * (5e-4 / (3e-8 * 0.9)) / 1.28e6 / 60, rel=0.01)


def test_deck_parts_reads_titles_after_part_cards(tmp_path):
    run = tmp_path / "build" / "run"
    run.mkdir(parents=True)
    (run / "strike_0000.rad").write_text("/PART/1\nfill_p026\n#x\n/PART/2\np035\n/PART/3\ntooth_head\n")
    assert results.deck_parts(tmp_path) == {"fill_p026": 1, "p035": 2, "tooth_head": 3}


def test_hit_progress_flags_a_hit_that_was_still_going(tmp_path):
    csv = tmp_path / "h.csv"
    rows = ["time,i_energy,k_energy_t,k_energy_r"]
    rows += [f"{i * 1e-5},{i * i * 10.0},0,1000" for i in range(11)]
    csv.write_text("\n".join(rows) + "\n")
    hit = results.hit_progress(csv)
    assert hit["known"] and hit["still_absorbing"]
    assert results.hit_progress(tmp_path / "none.csv") == {"known": False}


def test_erosion_explains_the_energy_drift():
    kept = results.quality(["energy drift 12%", "added mass 0.1%"], {"eroded_elements": 4})
    assert "added mass 0.1%" in kept and not any("energy drift" in w for w in kept)
    assert results.quality(["energy drift 12%"], {}) == ["energy drift 12%"]


def test_script_json_is_read_whole_or_from_the_last_line():
    assert case_dir._json('{\n  "a": 1\n}\n') == {"a": 1}
    assert case_dir._json('progress\n{"a": 2}\n') == {"a": 2}
    assert case_dir._json("no json") is None
