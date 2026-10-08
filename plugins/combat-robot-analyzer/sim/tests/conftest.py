"""Shared fixtures and builders for the load-case tests copied from v1.

Only what the copied tests use: the material library path, a synthetic
cad-step report (``make_tier0`` — v1's name for it, kept so the tests read
the same as their v1 originals) and exact box surfaces."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

SIM_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def materials_yaml() -> Path:
    """The material library the deck builder reads."""
    return SIM_ROOT / "materials.yaml"


def make_tier0(**overrides: object) -> dict[str, Any]:
    """A Tier 0 document as B3 writes it: opaque ids, no names."""
    doc: dict[str, Any] = {
        "assembly": {"schema": "AUTOMOTIVE_DESIGN", "units": {"length": ["millimetre", 1.0]}},
        "parts": [
            # A 12 lb bot: 2 kg weapon bar, 2 kg armour, 1.4 kg chassis.
            _part("p000", 712_000.0, bbox=([0, 0, 0], [40, 300, 12]), bore=True),
            _part("p001", 712_000.0, bbox=([0, 0, 100], [300, 300, 106]), bore=False),
            _part("p002", 498_000.0, bbox=([50, 50, 40], [250, 250, 60]), bore=False),
        ],
        "bbox": {"min": [0, 0, 0], "max": [300, 300, 106], "size": [300, 300, 106]},
        "holes": [],
        "bolt_circles": [],
        "placements": [],
        "fasteners": {"groups": [], "total_count": 0, "total_mass_g": None},
        "totals": {"volume_mm3": 1_922_000.0, "com": None, "unmeasured": 0},
        "timestep_drivers": [],
        "timestep_drivers_max_mm": 1.0,
        "timestep_drivers_error": None,
        "join": {"unjoined_parts": 0, "methods": {"exact": 3}},
    }
    doc.update(overrides)
    return doc


def _part(pid, volume, bbox, bore):
    lo, hi = bbox
    return {
        "id": pid,
        "occurrence": 0,
        "joined": "exact",
        "volume_mm3": volume,
        "area_mm2": 1000.0,
        "com": [0.0, 0.0, 0.0],
        "bbox": {"min": list(map(float, lo)), "max": list(map(float, hi))},
        "solids": 1,
        "closed": True,
        "color": None,
        "min_feature_mm": 2.0,
        "has_cylindrical_bore": bore,
        "max_bore_dia_mm": 8.0 if bore else None,
    }


def box_triangles(lo, hi):
    """The twelve triangles of an axis-aligned box.

    The fixture's parts *are* boxes — a Tier 0 part carries a bounding box and
    nothing else — so the synthetic surface is not an approximation of the
    fixture geometry, it is exactly the fixture geometry.
    """
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
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (0, 4, 7, 3), (1, 2, 6, 5)]
    out = []
    for a, b, c, d in quads:
        out.append((v[a], v[b], v[c]))
        out.append((v[a], v[c], v[d]))
    return out


