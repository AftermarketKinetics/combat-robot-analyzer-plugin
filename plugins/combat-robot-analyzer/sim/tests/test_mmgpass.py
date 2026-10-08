"""Unit tests for the guarded mmg3d post-pass.

No mmg binary: the codecs and the acceptance rule are pure, and
``guarded_pass`` takes the binary name, so a stub executable stands in for
mmg. What these pin is the guard — every failure mode keeps the baseline —
and the round-trip that part identity and shared nodes survive.
"""

from __future__ import annotations

import math
import stat
from typing import ClassVar

import pytest

from case.mesh_metrics import MeshMetrics, PartMetrics
from case.mmgpass import (
    ACCEPT_MARGIN,
    evaluate_pass,
    guarded_pass,
    read_medit,
    write_medit,
    write_msh22,
)

#: Two parts, two tets each, sharing nodes within and *across* parts —
#: the conformal interface the pass must not sever.
GROUPS = {
    "p001": [
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)],
        [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 1.0, 1.0)],
    ],
    "fill_p002": [
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, -1.0)],
    ],
}


def _metrics(element_cycles: float) -> MeshMetrics:
    part = PartMetrics(
        part_id="p001",
        elements=3,
        min_edge_mm=1.0,
        min_char_mm=0.4,
        wave_speed=5.0e6,
        mass=1.0,
        added_mass=0.0,
    )
    return MeshMetrics(
        elements=3,
        dt_physical=8.0e-8,
        dt_effective=8.0e-8,
        cycles=500,
        element_cycles=element_cycles,
        mass_total=1.0,
        mass_added=0.0,
        per_part=(part,),
    )


class TestCodecs:
    def test_medit_round_trip_keeps_parts_and_shared_nodes(self, tmp_path):
        path = tmp_path / "m.mesh"
        names_by_ref = write_medit(GROUPS, path)
        assert sorted(names_by_ref.values()) == ["fill_p002", "p001"]

        # 3 tets x 4 corners = 12 references over exactly 6 distinct nodes.
        text = path.read_text().split()
        vertex_count = int(text[text.index("Vertices") + 1])
        assert vertex_count == 6

        back = read_medit(path, names_by_ref)
        assert {k: len(v) for k, v in back.items()} == {"p001": 2, "fill_p002": 1}
        assert sorted(map(sorted, back["p001"])) == sorted(map(sorted, GROUPS["p001"]))

    def test_medit_with_an_unknown_reference_is_refused(self, tmp_path):
        path = tmp_path / "m.mesh"
        names_by_ref = write_medit(GROUPS, path)
        with pytest.raises(ValueError):
            read_medit(path, {max(names_by_ref) + 1: "stranger"})

    def test_msh22_carries_the_physical_names(self, tmp_path):
        path = tmp_path / "m.msh"
        write_msh22(GROUPS, path)
        text = path.read_text()
        assert text.startswith("$MeshFormat\n2.2 0 8\n")
        assert '"p001"' in text and '"fill_p002"' in text
        # One line per tet, type 4, two tags.
        elements = text.split("$Elements\n")[1].splitlines()
        assert elements[0] == "3"
        assert all(line.split()[1:3] == ["4", "2"] for line in elements[1:4])


class TestAcceptanceRule:
    VOLS: ClassVar[dict[str, float]] = {"p001": 10.0, "fill_p002": 5.0}

    def test_cheaper_within_margin_is_accepted(self):
        verdict = evaluate_pass(_metrics(100.0), _metrics(90.0), self.VOLS, dict(self.VOLS))
        assert verdict == "accepted"

    def test_not_cheaper_enough_is_rejected(self):
        after = _metrics(100.0 * ACCEPT_MARGIN + 0.5)
        assert "not cheaper" in evaluate_pass(_metrics(100.0), after, self.VOLS, dict(self.VOLS))

    def test_a_lost_part_is_rejected_before_any_price_comparison(self):
        verdict = evaluate_pass(_metrics(100.0), _metrics(1.0), self.VOLS, {"p001": 10.0})
        assert "part set" in verdict

    def test_volume_drift_is_rejected_even_when_cheaper(self):
        drifted = {"p001": 10.2, "fill_p002": 5.0}
        verdict = evaluate_pass(_metrics(100.0), _metrics(1.0), self.VOLS, drifted)
        assert "drifted" in verdict

    def test_an_unpriceable_proposal_is_rejected(self):
        assert "nothing measurable" in evaluate_pass(
            _metrics(100.0), _metrics(math.inf), self.VOLS, dict(self.VOLS)
        )


def _stub(tmp_path, body: str) -> str:
    """An executable standing in for mmg3d_O3."""
    script = tmp_path / "fake-mmg"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


CARDS = {
    "p001": {"density": 7.8e-9, "young": 2.0e5, "poisson": 0.3},
    "fill_p002": {"density": 7.8e-9, "young": 2.0e5, "poisson": 0.3},
}


def _run(tmp_path, body: str | None = None, *, mmg_bin: str | None = None):
    if mmg_bin is None:
        assert body is not None
        mmg_bin = _stub(tmp_path, body)
    from case.mesh_metrics import analyse

    baseline = analyse(GROUPS, CARDS, end_time=4e-5, timestep_min=0.0, timestep_scale=0.9)
    return guarded_pass(
        GROUPS,
        CARDS,
        baseline=baseline,
        end_time=4e-5,
        timestep_scale=0.9,
        mesh_floor_mm=0.3,
        workdir=tmp_path,
        case="lc1",
        mmg_bin=mmg_bin,
        timeout_s=10.0,
    )


class TestGuardedPass:
    def test_an_identical_proposal_is_rejected_as_not_cheaper(self, tmp_path):
        # The stub "improves" the mesh by copying it: same price, so the
        # guard must keep the baseline.
        result = _run(tmp_path, 'cp "$2" "$4"\n')
        assert not result.accepted
        assert "not cheaper" in result.reason
        assert result.mesh_path is None and result.metrics is None
        assert not (tmp_path / "lc1-mmg.msh").exists()

    def test_a_crash_keeps_the_baseline(self, tmp_path):
        result = _run(tmp_path, "echo boom >&2\nexit 3\n")
        assert not result.accepted
        assert "exited 3" in result.reason and "boom" in result.reason

    def test_a_missing_binary_keeps_the_baseline(self, tmp_path):
        result = _run(tmp_path, mmg_bin=str(tmp_path / "does-not-exist"))
        assert not result.accepted
        assert "not on PATH" in result.reason

    def test_the_argument_order_the_stubs_rely_on(self, tmp_path):
        # The stubs read $2 (input) and $4 (output). If the mmg invocation
        # is ever reordered, this failing names the real cause.
        seen = tmp_path / "args"
        _run(tmp_path, f'echo "$1 $3 $5" > "{seen}"\nexit 1\n')
        assert seen.read_text().split() == ["-in", "-out", "-optim"]
