#!/usr/bin/env python3
"""Exact mass properties: volume, surface area, centre of mass, inertia.

Uses OpenCASCADE, re-execing into a nix-shell automatically when pythonocc is
not already importable.  Assign densities to get real masses -- either one
density for everything (``--density 2.70``) or per part
(``--material rail=aluminium --material shell=abs``).
"""

from __future__ import annotations

import sys

import _common as C

# g/cm^3
MATERIALS = {
    "aluminium": 2.70, "aluminum": 2.70, "al7075": 2.81, "al6061": 2.70,
    "steel": 7.85, "mild-steel": 7.85, "4140": 7.85, "s7": 7.83,
    "stainless": 8.00, "ss304": 8.00, "ss316": 8.00,
    "titanium": 4.43, "ti64": 4.43,
    "brass": 8.50, "bronze": 8.80, "copper": 8.96, "lead": 11.34,
    "tungsten": 19.30, "magnesium": 1.74, "zinc": 7.14,
    "abs": 1.04, "pla": 1.24, "petg": 1.27, "nylon": 1.15, "pa12": 1.01,
    "tpu": 1.20, "delrin": 1.41, "pom": 1.41, "ptfe": 2.20, "pc": 1.20,
    "uhmw": 0.94, "hdpe": 0.95, "acrylic": 1.18,
    "carbon-fiber": 1.60, "g10": 1.85, "fr4": 1.85, "rubber": 1.20,
    "lipo": 2.10, "wood": 0.70,
}


def resolve_density(name):
    if name is None:
        return None
    try:
        return float(name)
    except ValueError:
        pass
    key = str(name).strip().lower().replace(" ", "-")
    if key not in MATERIALS:
        raise SystemExit("unknown material %r (known: %s)"
                         % (name, ", ".join(sorted(MATERIALS))))
    return MATERIALS[key]


def collect(path, parts=None, density=None, per_part=(), inertia=False):
    import occenv
    occenv.ensure_occ()
    import occshapes as O
    return collect_from(O.load_parts(path), parts, density, per_part, inertia,
                        path=path)


def collect_from(loaded, parts=None, density=None, per_part=(), inertia=False,
                 path="", id_of=None):
    """As :func:`collect`, but reusing already-loaded :class:`occshapes.Part` objects.

    *id_of* maps a :class:`occshapes.Part` to a stable part id; pass it to get
    a ``part_id`` on every row, which is the only safe join key when the
    assembly contains duplicate instances.
    """
    import occshapes as O

    rules = []
    for spec in per_part:
        if "=" not in spec:
            raise SystemExit("--material needs PATTERN=MATERIAL, got %r" % spec)
        pat, mat = spec.split("=", 1)
        rules.append((pat.strip(), resolve_density(mat)))

    rows = []
    for p in loaded:
        if not C.match(p.name, parts) and not C.match(p.path, parts):
            continue
        vp = O.volume_props(p.shape)
        vol_mm3 = vp["volume"]
        # A rule matching the part's own name beats one that only matches its
        # assembly path -- otherwise a subassembly called "spring-loaded" would
        # quietly claim every fastener inside it.
        rho = density
        for pat, d in rules:
            if C.match(p.name, [pat], default=False):
                rho = d
                break
        else:
            for pat, d in rules:
                if C.match(p.path, [pat], default=False):
                    rho = d
                    break
        rec = {
            "part": p.name,
            "path": p.path,
            "volume_mm3": vol_mm3,
            "volume_cm3": vol_mm3 / 1000.0,
            "area_mm2": O.surface_area(p.shape),
            "com": list(vp["com"]),
            "density_g_cm3": rho,
            "mass_g": None if rho is None else (vol_mm3 / 1000.0) * rho,
            "solids": O.topology_census(p.shape)["solids"],
        }
        if id_of is not None:
            rec["part_id"] = id_of(p)
        if inertia:
            vals, vecs = O.principal_axes(vp["inertia"])
            rec["inertia"] = vp["inertia"]
            rec["principal_moments"] = vals
            rec["principal_axes"] = vecs
        rows.append(rec)

    rows.sort(key=lambda r: -(r["mass_g"] or r["volume_mm3"]))
    total_v = sum(r["volume_mm3"] for r in rows)
    massed = [r for r in rows if r["mass_g"] is not None]
    total_m = sum(r["mass_g"] for r in massed) if massed else None
    com = None
    if total_m:
        com = [sum(r["mass_g"] * r["com"][i] for r in massed) / total_m
               for i in range(3)]
    elif total_v:
        com = [sum(r["volume_mm3"] * r["com"][i] for r in rows) / total_v
               for i in range(3)]
    return {"path": path, "parts": rows, "total_volume_mm3": total_v,
            "total_mass_g": total_m, "com": com,
            "unmeasured": len(rows) - len(massed)}


def show(d):
    rows = []
    for r in d["parts"]:
        rows.append([
            r["part"],
            "%.1f" % r["volume_mm3"],
            "%.2f" % r["volume_cm3"],
            "-" if r["density_g_cm3"] is None else "%.2f" % r["density_g_cm3"],
            "-" if r["mass_g"] is None else "%.2f" % r["mass_g"],
            C.fmt_xyz(r["com"], 2),
        ])
    C.table(rows, ["part", "volume mm3", "cm3", "rho", "mass g", "centre of mass"],
            aligns=["<", ">", ">", ">", ">", "<"])
    C.heading("Total")
    print("  volume      %.1f mm3  (%.2f cm3)"
          % (d["total_volume_mm3"], d["total_volume_mm3"] / 1000.0))
    if d["total_mass_g"] is not None:
        print("  mass        %.2f g  (%.4f kg)"
              % (d["total_mass_g"], d["total_mass_g"] / 1000.0))
        if d["unmeasured"]:
            print("  NOTE        %d part(s) had no density assigned and are "
                  "excluded from mass" % d["unmeasured"])
    else:
        print("  mass        - (pass --density or --material to get masses)")
    if d["com"]:
        kind = "mass" if d["total_mass_g"] else "volume"
        print("  centroid    %s   (%s-weighted)" % (C.fmt_xyz(d["com"], 3), kind))

    if d["parts"] and "principal_moments" in d["parts"][0]:
        C.heading("Principal moments of inertia (about each part's centroid, mm^5)")
        C.table([[r["part"]] + ["%.4g" % v for v in r["principal_moments"]]
                 for r in d["parts"]], ["part", "I1", "I2", "I3"])


def main(argv=None):
    ap = C.base_parser(__doc__)
    ap.add_argument("--part", action="append", default=[])
    ap.add_argument("--density", help="g/cm3, or a material name (%s...)"
                                      % ", ".join(sorted(MATERIALS)[:4]))
    ap.add_argument("--material", action="append", default=[], metavar="PAT=MAT",
                    help="per-part density, e.g. --material rail=aluminium")
    ap.add_argument("--inertia", action="store_true",
                    help="also report principal moments of inertia")
    a = ap.parse_args(argv)
    data = collect(a.file, a.part, resolve_density(a.density), a.material, a.inertia)
    C.emit(data, a.json, show)


if __name__ == "__main__":
    sys.exit(main())
