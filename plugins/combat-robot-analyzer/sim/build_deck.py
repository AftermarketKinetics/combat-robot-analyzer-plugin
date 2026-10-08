#!/usr/bin/env python3
"""Turn a gmsh mesh plus a YAML spec into an OpenRadioss deck.

Writes the Radioss starter (<name>_0000.rad) and engine (<name>_0001.rad) files
in the fixed-column block format the solver expects, and a JSON summary that
includes the estimated stable timestep so the cost of the run is visible before
it is launched.

Card layouts follow the .cfg definitions shipped with OpenRadioss under
hm_cfg_files/config/CFG/radioss2025, not guesswork.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import math
import os
import sys

import meshio
import numpy as np
import yaml

# meshio cell type -> (Radioss keyword, nodes per element, topological dim)
ELEMENTS = {
    "quad": ("SHELL", 4, 2),
    "triangle": ("SH3N", 3, 2),
    "tetra": ("TETRA4", 4, 3),
    "hexahedron": ("BRICK", 8, 3),
}

# Consistent unit systems. Radioss does no conversion; it only records what the
# numbers mean, so the material data and the mesh have to agree with the choice.
UNITS = {
    "mm_Mg_s": {"mass": "Mg", "length": "mm", "time": "s",
                "stress": "MPa", "density": "Mg/mm3", "force": "N"},
    "m_kg_s": {"mass": "kg", "length": "m", "time": "s",
               "stress": "Pa", "density": "kg/m3", "force": "N"},
    "mm_kg_ms": {"mass": "kg", "length": "mm", "time": "ms",
                 "stress": "GPa", "density": "kg/mm3", "force": "kN"},
}

INVERS = 2025  # input format version declared in /BEGIN


# --------------------------------------------------------------------------
# fixed-format field writers
# --------------------------------------------------------------------------

def i10(v) -> str:
    return f"{int(v):10d}"


def f20(v) -> str:
    """A float in 20 columns, which is what every %20lg card field expects."""
    s = f"{float(v):>20.12g}"
    if len(s) > 20:  # very long mantissa; fall back to exponent form
        s = f"{float(v):>20.8e}"
    return s[:20] if len(s) > 20 else s


def title(text: str) -> str:
    return f"{text[:100]:<100}".rstrip()


# --------------------------------------------------------------------------
# node selection
# --------------------------------------------------------------------------

def select_nodes(spec, points, part_nodes, bbox):
    """Resolve one entry of the YAML `sets:` block to node indices.

    Supported selectors: all, part, box (absolute), rel_box (fractions of the
    bounding box), plane (axis + coordinate + tolerance).
    """
    if not isinstance(spec, dict):
        raise ValueError(f"set definition must be a mapping, got {type(spec).__name__}")

    mask = np.ones(len(points), dtype=bool)
    used = False

    if spec.get("all"):
        used = True

    if "part" in spec:
        pattern = spec["part"]
        matched = set()
        hits = [n for n in part_nodes if fnmatch.fnmatch(n, pattern)]
        if not hits:
            raise ValueError(f"set selector part='{pattern}' matched no part "
                             f"(have: {sorted(part_nodes)})")
        for name in hits:
            matched |= part_nodes[name]
        sel = np.zeros(len(points), dtype=bool)
        sel[list(matched)] = True
        mask &= sel
        used = True

    lo, hi = bbox
    for key, source in (("box", None), ("rel_box", "rel")):
        if key not in spec:
            continue
        used = True
        box = spec[key] or {}
        for axis_i, axis in enumerate("xyz"):
            for bound in ("min", "max"):
                field = f"{axis}{bound}"
                if field not in box:
                    continue
                value = float(box[field])
                if source == "rel":
                    value = lo[axis_i] + value * (hi[axis_i] - lo[axis_i])
                col = points[:, axis_i]
                mask &= (col >= value) if bound == "min" else (col <= value)

    if "plane" in spec:
        used = True
        pl = spec["plane"]
        axis_i = "xyz".index(str(pl["axis"]).lower())
        at = float(pl["at"])
        tol = float(pl.get("tol", 1e-6))
        mask &= np.abs(points[:, axis_i] - at) <= tol

    if not used:
        raise ValueError(f"set definition has no recognised selector: {spec}")

    idx = np.flatnonzero(mask)
    return idx


# --------------------------------------------------------------------------
# materials
# --------------------------------------------------------------------------

def material_cards(mat_id, name, m):
    """Emit /MAT for the requested law. Returns a list of lines."""
    law = str(m.get("law", "johnson_cook")).lower()
    rho = float(m["density"])
    out = []

    if law in ("elastic", "law1"):
        E = float(m["young"])
        nu = float(m["poisson"])
        out.append(f"/MAT/LAW1/{mat_id}")
        out.append(title(name))
        out.append("#              RHO_I")
        out.append(f20(rho))
        out.append("#                  E                  Nu")
        out.append(f20(E) + f20(nu))
        return out

    if law in ("johnson_cook", "plas_johns", "law2"):
        E = float(m["young"])
        nu = float(m["poisson"])
        a = float(m.get("yield", m.get("hardening_a", 0.0)))
        b = float(m.get("hardening_b", 0.0))
        n = float(m.get("hardening_n", 1.0))
        eps_max = float(m.get("eps_max", 0.0))
        sig_max = float(m.get("sig_max", 0.0))
        c = float(m.get("strain_rate_c", 0.0))
        eps_dot_0 = float(m.get("strain_rate_eps0", 0.0))
        # Rate-term handling. The solver warns (ID 1220) when a rate term is
        # active without filtering, because explicit dynamics feeds LAW2 the
        # raw element strain rate and the noise inflates the flow stress.
        # A sensible F_cut depends on the mesh and timestep, not the material,
        # so both stay opt-in and default to today's unfiltered behaviour.
        icc = int(m.get("icc", 0))
        fsmooth = int(m.get("strain_rate_filter", m.get("fsmooth", 0)))
        f_cut = float(m.get("f_cut", 0.0))
        chard = float(m.get("chard", 0.0))
        out.append(f"/MAT/LAW2/{mat_id}")
        out.append(title(name))
        out.append("#              RHO_I")
        out.append(f20(rho))
        out.append("#                  E                  Nu     Iflag    flagVP                Pmin")
        out.append(f20(E) + f20(nu) + i10(0) + i10(0) + f20(0.0))
        out.append("#                  a                   b                   n           EPS_p_max            SIG_max0")
        out.append(f20(a) + f20(b) + f20(n) + f20(eps_max) + f20(sig_max))
        out.append("#                  c           EPS_DOT_0       ICC   Fsmooth               F_cut               Chard")
        out.append(f20(c) + f20(eps_dot_0) + i10(icc) + i10(fsmooth)
                   + f20(f_cut) + f20(chard))
        out.append("#                  m              T_melt              rhoC_p                 T_r               T_max")
        out.append(f20(0.0) + f20(0.0) + f20(0.0) + f20(0.0) + f20(0.0))
        return out

    if law in ("ogden", "law42"):
        return ogden_cards(mat_id, name, m)

    if law in ("mooney_rivlin", "mooney"):
        c10 = float(m["c10"])
        c01 = float(m["c01"])
        synth = dict(m)
        synth.update({"mu": [c10, -c01], "alpha": [2.0, -2.0]})
        return ogden_cards(mat_id, name, synth)

    if law in ("fabric", "fabri", "law19"):
        return fabri_cards(mat_id, name, m)

    if law in ("compsh", "law25", "composite"):
        return compsh_cards(mat_id, name, m)

    raise ValueError(f"material '{name}': unsupported law '{law}' "
                     f"(use elastic, johnson_cook, ogden, mooney_rivlin, "
                     "fabric, or compsh)")


def ogden_cards(mat_id, name, m):
    """Emit /MAT/OGDEN (Law 42), radioss2025 format.

    Always writes 10 Mu + 10 Alpha fields (4 rows of 5 each), unused slots
    zeroed.  The ORDER field in the card controls the Prony series length
    (viscoelasticity), NOT the number of Ogden mu/alpha terms.

    Card layout: hm_cfg_files/config/CFG/radioss2025/MAT/matl42_Ogden.cfg
    """
    rho = float(m["density"])
    nu = float(m.get("poisson", 0.495))
    sigma_cut = float(m.get("sigma_cut", 1.0e30))
    iform = int(m.get("iform", 0))

    mu_list = m.get("mu", [])
    alpha_list = m.get("alpha", [])
    if not mu_list or not alpha_list:
        raise ValueError(f"material '{name}': ogden requires 'mu' and 'alpha' lists")
    if len(mu_list) != len(alpha_list):
        raise ValueError(f"material '{name}': ogden 'mu' and 'alpha' must have "
                         "the same length")
    if len(mu_list) > 10:
        raise ValueError(f"material '{name}': ogden supports at most 10 terms")

    mu = [float(x) for x in mu_list] + [0.0] * (10 - len(mu_list))
    alpha = [float(x) for x in alpha_list] + [0.0] * (10 - len(alpha_list))

    # Optional Prony viscoelastic series
    prony = m.get("prony") or {}
    n_prony = 0
    gamma_arr, tau_arr = [], []
    if prony.get("terms"):
        for g, t in prony["terms"]:
            gamma_arr.append(float(g))
            tau_arr.append(float(t))
        n_prony = len(gamma_arr)

    out = [f"/MAT/OGDEN/{mat_id}"]
    out.append(title(name))
    out.append("#              RHO_I")
    out.append(f20(rho))
    # Nu(f20) sigma_cut(f20) Jstrain(i10) funIDbulk(i10) Fscale(f20) M(i10) Iform(i10)
    out.append("#                 Nu           sigma_cut           funIDbulk"
               "         Fscale_bulk         M    I_form")
    out.append(f20(nu) + f20(sigma_cut) + i10(0) + i10(0)
               + f20(1.0) + i10(n_prony) + i10(iform))
    out.append("#               Mu_1                Mu_2                Mu_3"
               "                Mu_4                Mu_5")
    out.append("".join(f20(mu[i]) for i in range(5)))
    out.append("#               Mu_6                Mu_7                Mu_8"
               "                Mu_9               Mu_10")
    out.append("".join(f20(mu[i]) for i in range(5, 10)))
    out.append("#            alpha_1             alpha_2             alpha_3"
               "             alpha_4             alpha_5")
    out.append("".join(f20(alpha[i]) for i in range(5)))
    out.append("#            alpha_6             alpha_7             alpha_8"
               "             alpha_9            alpha_10")
    out.append("".join(f20(alpha[i]) for i in range(5, 10)))

    if n_prony > 0:
        out.append("# Shear modulus")
        for i in range(0, n_prony, 5):
            out.append("".join(f20(gamma_arr[j])
                               for j in range(i, min(i + 5, n_prony))))
        out.append("# Time relaxation")
        for i in range(0, n_prony, 5):
            out.append("".join(f20(tau_arr[j])
                               for j in range(i, min(i + 5, n_prony))))
    return out


def fabri_cards(mat_id, name, m):
    """Emit /MAT/FABRI (Law 19), radioss120 format.

    Simple orthotropic elastic shell material.  Requires /PROP/SH_ORTH
    (TYPE9) — NOT compatible with /PROP/SHELL (TYPE1).
    No built-in yield or failure; use /FAIL cards for element erosion.

    Card layout: hm_cfg_files/config/CFG/radioss120/MAT/matl19_fabri.cfg
    """
    rho = float(m["density"])
    e11 = float(m["e11"])
    e22 = float(m["e22"])
    nu12 = float(m.get("nu12", 0.3))
    g12 = float(m["g12"])
    g23 = float(m["g23"])
    g31 = float(m["g31"])

    out = [f"/MAT/FABRI/{mat_id}"]
    out.append(title(name))
    out.append("#              RHO_I")
    out.append(f20(rho))
    out.append("#                E11                 E22                NU12")
    out.append(f20(e11) + f20(e22) + f20(nu12))
    out.append("#                G12                 G23                 G31")
    out.append(f20(g12) + f20(g23) + f20(g31))
    # R_E (reduction factor), blank, ZEROSTRESS, FSCALE_POR, SENS_ID
    out.append("#                R_E                              ZEROSTRESS"
               "          FSCALE_POR   SENS_ID")
    out.append(f20(float(m.get("r_e", 0.0))) + f20(0.0)
               + f20(float(m.get("zero_stress", 0.0)))
               + f20(float(m.get("porosity_scale", 0.0))) + i10(0))
    return out


def compsh_cards(mat_id, name, m):
    """Emit /MAT/COMPSH (Law 25), radioss2019 format, Tsai-Wu formulation.

    Orthotropic composite shell with Tsai-Wu failure criterion.  Only the
    Iform=0 (Tsai-Wu) path is emitted; CRASURV (Iform=1) is not supported.

    Card layout: hm_cfg_files/config/CFG/radioss2019/MAT/matl25_compsh.cfg
    """
    rho = float(m["density"])
    e11 = float(m["e11"])
    e22 = float(m["e22"])
    nu12 = float(m.get("nu12", 0.3))
    e33 = float(m.get("e33", 0.0))
    g12 = float(m["g12"])
    g23 = float(m["g23"])
    g31 = float(m["g31"])

    # Failure strains (defaults: effectively infinite = no failure)
    eps_f1 = float(m.get("eps_f1", 1.0e20))
    eps_f2 = float(m.get("eps_f2", 1.0e20))
    eps_t1 = float(m.get("eps_t1", 1.0e20))
    eps_m1 = float(m.get("eps_m1", 1.0e20))
    eps_t2 = float(m.get("eps_t2", 1.0e20))
    eps_m2 = float(m.get("eps_m2", 1.0e20))
    dmax = float(m.get("dmax", 0.999))

    # Tsai-Wu yield stresses
    sig_1yt = float(m.get("sigma_1yt", 1.0e20))
    sig_2yt = float(m.get("sigma_2yt", 1.0e20))
    sig_1yc = float(m.get("sigma_1yc", 1.0e20))
    sig_2yc = float(m.get("sigma_2yc", 1.0e20))
    sig_12yc = float(m.get("sigma_12yc", sig_1yc))
    sig_12yt = float(m.get("sigma_12yt", sig_1yt))
    tw_alpha = float(m.get("alpha", 1.0))

    # Plastic work / element deletion
    wpmax = float(m.get("wpmax", 1.0e20))
    wpref = float(m.get("wpref", 1.0))
    ioff = int(m.get("ioff", 0))
    ratio = float(m.get("ratio", 1.0))

    # Hardening
    beta = float(m.get("beta", 0.0))
    hard_n = float(m.get("hardening_n", 1.0))
    fmax = float(m.get("fmax", 1.0e20))

    # Rate
    src = float(m.get("strain_rate_c", 0.0))
    srp = float(m.get("strain_rate_eps0", 0.0))
    icc = int(m.get("icc", 1))

    # Delamination
    gamma_ini = float(m.get("gamma_ini", 1.0e20))
    gamma_max = float(m.get("gamma_max", 1.1e20))
    d3max = float(m.get("d3max", 0.0))

    # Filtering
    fsmooth = int(m.get("fsmooth", 0))
    fcut = float(m.get("fcut", 1.0e20))

    out = [f"/MAT/COMPSH/{mat_id}"]
    out.append(title(name))
    out.append("#              RHO_I")
    out.append(f20(rho))
    out.append("#                E11                 E22                NU12"
               "     Iform                           E33")
    out.append(f20(e11) + f20(e22) + f20(nu12) + i10(0)
               + " " * 10 + f20(e33))
    out.append("#                G12                 G23                 G31"
               "              EPS_f1              EPS_f2")
    out.append(f20(g12) + f20(g23) + f20(g31) + f20(eps_f1) + f20(eps_f2))
    out.append("#             EPS_t1              EPS_m1              EPS_t2"
               "              EPS_m2                dmax")
    out.append(f20(eps_t1) + f20(eps_m1) + f20(eps_t2) + f20(eps_m2)
               + f20(dmax))
    out.append("#              Wpmax               Wpref      Ioff"
               "                         ratio")
    out.append(f20(wpmax) + f20(wpref) + i10(ioff) + " " * 10 + f20(ratio))
    out.append("#                  b                   n                fmax")
    out.append(f20(beta) + f20(hard_n) + f20(fmax))
    out.append("#            sig_1yt             sig_2yt             sig_1yc"
               "             sig_2yc               alpha")
    out.append(f20(sig_1yt) + f20(sig_2yt) + f20(sig_1yc) + f20(sig_2yc)
               + f20(tw_alpha))
    out.append("#           sig_12yc            sig_12yt                c_12"
               "          Eps_rate_0       ICC")
    out.append(f20(sig_12yc) + f20(sig_12yt) + f20(src) + f20(srp) + i10(icc))
    out.append("#          GAMMA_ini           GAMMA_max               d3max")
    out.append(f20(gamma_ini) + f20(gamma_max) + f20(d3max))
    out.append("#  Fsmooth                Fcut")
    out.append(i10(fsmooth) + f20(fcut))
    return out


def tsh_comp_cards(prop_id, name, layup, mat_id_map, opts):
    """Emit /PROP/TSH_COMP (TYPE22), radioss2025 format.

    Composite thick-shell property with per-ply material, thickness fraction,
    fibre angle, and z-position.  Requires hexahedral (BRICK) mesh elements.

    Card layout: hm_cfg_files/config/CFG/radioss2025/PROP/prop_p22_tsh_comp.cfg
    """
    n_ply = len(layup)
    total_t = sum(float(ply["thickness"]) for ply in layup)
    if total_t <= 0:
        raise ValueError(f"property '{name}': layup total thickness must be > 0")

    out = [f"/PROP/TSH_COMP/{prop_id}"]
    out.append(title(name))
    # Isolid(i10) Ismstr(i10) blank(20) Icstr(i10) Inpts(i10) Iint(i10) blank(10) Dn(f20)
    isolid = int(opts.get("isolid", 14))
    ismstr = int(opts.get("ismstr", 0))
    icstr = int(opts.get("icstr", 1))
    inpts = int(opts.get("inpts", 222))   # 2x2x2 integration default
    out.append("#   Isolid    Ismstr                         Icstr     Inpts"
               "      Iint                            Dn")
    out.append(i10(isolid) + i10(ismstr) + " " * 20 + i10(icstr) + i10(inpts)
               + i10(n_ply) + " " * 10 + f20(0.0))
    # qa, qb
    out.append("#                 qa                  qb")
    out.append(f20(float(opts.get("qa", 1.1))) + f20(float(opts.get("qb", 0.05))))
    # Reference vector + skew + Iorth + Ipos
    out.append("#                 Vx                  Vy                  Vz"
               "   skew_ID     Iorth      Ipos")
    out.append(f20(0.0) + f20(0.0) + f20(0.0) + i10(0) + i10(0) + i10(0))
    # Ashear
    out.append("#             Ashear")
    out.append(f20(float(opts.get("ashear", 1.0))))
    # Per-ply cards
    out.append("#                Phi                ti/t                  Zi   mat_IDi")
    z_running = -0.5
    for ply in layup:
        ti_t = float(ply["thickness"]) / total_t
        z_center = z_running + ti_t / 2
        phi = float(ply.get("angle", 0))
        mid = mat_id_map[ply["material"]]
        out.append(f20(phi) + f20(ti_t) + f20(z_center) + i10(mid))
        z_running += ti_t
    # Trailing cards
    out.append("#         DeltaT_min            vdef_min            vdef_max"
               "             ASP_max             COL_min")
    out.append(f20(0.0) * 5)
    out.append("#                     Icontrol")
    out.append(" " * 20 + i10(0))
    return out


def sh_orth_cards(prop_id, name, thickness, opts):
    """Emit /PROP/SH_ORTH (TYPE9), radioss2024 format.

    Orthotropic shell property required by /MAT/FABRI (Law 19).  Adds a
    material orientation vector (Vx, Vy, Vz) and angle Phi that define
    the fibre reference direction in the shell plane.

    Card layout: hm_cfg_files/config/CFG/radioss2024/PROP/prop_p9_sh_orth.cfg
    """
    if thickness is None:
        raise ValueError(f"part '{name}' is a shell part and needs a `thickness`")
    nip = int(opts.get("integration_points", 5))
    ishell = int(opts.get("ishell", 12))  # 12 = QBAT/DKT18, works with ortho
    vx = float(opts.get("vx", 1.0))
    vy = float(opts.get("vy", 0.0))
    vz = float(opts.get("vz", 0.0))
    phi = float(opts.get("phi", 0.0))

    out = [f"/PROP/SH_ORTH/{prop_id}"]
    out.append(title(name))
    out.append("#   Ishell    Ismstr     Ish3n    Idrill"
               "                            P_Thick_Fail")
    out.append(i10(ishell) + i10(0) + i10(2) + i10(0)
               + " " * 20 + f20(0.0))
    out.append("#                 Hm                  Hf                  Hr"
               "                  Dm                  Dn")
    out.append(f20(0.0) * 3
               + f20(float(opts.get("damping", 0.0))) + f20(0.0))
    out.append("#        N   ISTRAIN               Thick              Ashear"
               "     Iskew    ITHICK     IPLAS")
    out.append(i10(nip) + i10(0) + f20(thickness) + f20(0.0)
               + i10(0) + i10(1) + i10(1))
    out.append("#                 Vx                  Vy                  Vz"
               "                 Phi      Ipos        Ip")
    out.append(f20(vx) + f20(vy) + f20(vz) + f20(phi) + i10(0) + i10(0))
    return out


def property_cards(prop_id, name, dim, thickness, opts, layup=None,
                   mat_id_map=None, mat_law=None):
    out = []
    if layup is not None:
        return tsh_comp_cards(prop_id, name, layup, mat_id_map, opts)
    if dim == 2:
        if mat_law in ("fabric", "fabri", "law19"):
            return sh_orth_cards(prop_id, name, thickness, opts)
        if thickness is None:
            raise ValueError(f"part '{name}' is a shell part and needs a `thickness`")
        nip = int(opts.get("integration_points", 5))
        out.append(f"/PROP/SHELL/{prop_id}")
        out.append(title(name))
        out.append("#   Ishell    Ismstr     Ish3n    Idrill    Ipinch                  P_Thick_Fail")
        out.append(i10(opts.get("ishell", 24)) + i10(0) + i10(2) + i10(0) + i10(0)
                   + " " * 10 + f20(0.0))
        out.append("#                 Hm                  Hf                  Hr                  Dm                  Dn")
        out.append(f20(0.0) * 3 + f20(float(opts.get("damping", 0.0))) + f20(0.0))
        out.append("#        N                         Thick              Ashear              Ithick     Iplas      Ipos")
        out.append(i10(nip) + " " * 10 + f20(thickness) + f20(0.0)
                   + " " * 10 + i10(1) + i10(1) + i10(0))
        return out

    out.append(f"/PROP/SOLID/{prop_id}")
    out.append(title(name))
    out.append("#   Isolid    Ismstr      Iale     Icpre  Itetra10     Inpts   Itetra4    Iframe                  Dn")
    out.append(i10(opts.get("isolid", 14)) + i10(0) + i10(0) + i10(0) + i10(0)
               + i10(0) + i10(0) + i10(0) + f20(0.0))
    out.append("#                 qa                  qb                   h              Lambda                  Mu")
    out.append(f20(1.1) + f20(0.05) + f20(0.0) + f20(0.0) + f20(0.0))
    # Both remaining cards are required even when left at defaults; omitting
    # them makes the reader warn that /PROP/SOLID is truncated.
    out.append("#         deltaT_min            vdef_min            vdef_max             ASP_max             COL_min")
    out.append(f20(0.0) * 5)
    out.append("#     Ndir sphpartID  Icontrol")
    out.append(i10(0) + i10(0) + i10(0))
    return out


# --------------------------------------------------------------------------
# failure / element erosion
# --------------------------------------------------------------------------

def fail_johnson_cards(mat_id, f):
    """Emit /FAIL/JOHNSON using the radioss2025 card layout.

    Johnson-Cook damage: D = Σ(Δεp / εf) where εf depends on triaxiality,
    strain rate and temperature via parameters D1–D5. Element deleted at D=1.

    Card format: hm_cfg_files/config/CFG/radioss2025/FAIL/fail_johnson.cfg
    """
    d1 = float(f.get("d1", 0.0))
    d2 = float(f.get("d2", 0.0))
    d3 = float(f.get("d3", 0.0))
    d4 = float(f.get("d4", 0.0))
    d5 = float(f.get("d5", 0.0))
    eps_dot_0 = float(f.get("eps_dot_0", 1.0))
    ifail_sh = int(f.get("ifail_shell", 0))
    ifail_so = int(f.get("ifail_solid", 1))  # 1 = delete at first IP
    eps_min = float(f.get("eps_min", 0.0))
    dadv = float(f.get("dadv", 0.0))
    ixfem = int(f.get("ixfem", 0))
    failip = int(f.get("failip", 0))

    out = [f"/FAIL/JOHNSON/{mat_id}"]
    out.append("#                 D1                  D2                  D3"
               "                  D4                  D5")
    out.append(f20(d1) + f20(d2) + f20(d3) + f20(d4) + f20(d5))
    out.append("#      EPSILON_DOT_0  IFAIL_SH  IFAIL_SO            EPSF_MIN"
               "                DADV               IXFEM")
    out.append(f20(eps_dot_0) + i10(ifail_sh) + i10(ifail_so)
               + f20(eps_min) + f20(dadv) + " " * 10 + i10(ixfem))
    out.append("#FAILIP")
    out.append(i10(failip))
    return out


def fail_cockcroft_cards(mat_id, f):
    """Emit /FAIL/COCKCROFT using the radioss2025 card layout.

    Cockcroft-Latham: integrates max(σ1, 0) · dεp. Element fails when the
    integral reaches C0.  One parameter — simpler than Johnson-Cook but
    doesn't capture triaxiality dependence as well.

    Card format: hm_cfg_files/config/CFG/radioss2025/FAIL/fail_cockcroft.cfg
    """
    c0 = float(f.get("c0", 0.0))
    alpha = float(f.get("alpha", 1.0))
    failip = int(f.get("failip", 0))

    out = [f"/FAIL/COCKCROFT/{mat_id}"]
    out.append("#                 C0               ALPHA               FAILIP")
    out.append(f20(c0) + f20(alpha) + i10(failip))
    return out


def fail_tensstrain_cards(mat_id, f):
    """Emit /FAIL/TENSSTRAIN using the radioss2025 card layout.

    Simple tensile strain failure: damage begins at eps_t1, element deleted at
    eps_t2. Between the two, stress is linearly degraded.

    Card format: hm_cfg_files/config/CFG/radioss2025/FAIL/fail_tensstrain.cfg
    """
    eps_t1 = float(f.get("eps_t1", 0.0))
    eps_t2 = float(f.get("eps_t2", 0.0))
    fct_id = int(f.get("fct_id", 0))
    eps_f1 = float(f.get("eps_f1", 0.0))
    eps_f2 = float(f.get("eps_f2", 0.0))
    s_flag = int(f.get("s_flag", 1))  # 1 = equivalent strain, simplest
    failip = int(f.get("failip", 0))

    out = [f"/FAIL/TENSSTRAIN/{mat_id}"]
    out.append("#         EPSILON_T1          EPSILON_T2    FCT_ID"
               "          EPSILON_F1          EPSILON_F2     S_Flag")
    out.append(f20(eps_t1) + f20(eps_t2) + i10(fct_id)
               + f20(eps_f1) + f20(eps_f2) + i10(s_flag))
    out.append("# FAILIP")
    out.append(i10(failip))
    return out


def derived_failure(m):
    """A failure block from a material's `eps_max`, for specs that ask for it.

    LAW2's own EPS_p_max does not delete solid elements in these decks (a
    4130 shell reached 0.51 strain against eps_max 0.28, intact), so erosion
    needs a /FAIL card. Johnson-Cook failure with only D1 = eps_max deletes an
    element when its equivalent plastic strain reaches eps_max whatever the
    stress state — exactly what `eps_max` means. Tension-strain was tried
    first and missed material crushed in compression under the tooth
    (survivors at 6.5 plastic strain on the fixture spike). Materials without
    eps_max (elastic, hyperelastic) get none.
    """
    eps = float(m.get("eps_max") or 0.0)
    if eps <= 0 or str(m.get("law", "johnson_cook")).lower() not in ("johnson_cook", "plas_johns", "law2"):
        return None
    return {"model": "johnson_cook", "d1": eps}


FAIL_MODELS = {
    "johnson_cook": fail_johnson_cards,
    "johnson": fail_johnson_cards,
    "cockcroft": fail_cockcroft_cards,
    "cockcroft_latham": fail_cockcroft_cards,
    "tensstrain": fail_tensstrain_cards,
    "tensile_strain": fail_tensstrain_cards,
}


# --------------------------------------------------------------------------
# contact interface
# --------------------------------------------------------------------------

def parse_contact_spec(spec):
    """Normalise the contact: value into a list of interface dicts, or None."""
    raw = spec.get("contact")
    if raw is None or raw is False:
        return None
    if raw is True:
        return [{}]
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return raw
    return None


def surf_part_cards(surf_id, name, part_ids):
    """Emit /SURF/PART listing the external faces of the given shell parts."""
    out = [f"/SURF/PART/{surf_id}", title(name)]
    for chunk_start in range(0, len(part_ids), 10):
        out.append("".join(i10(v) for v in part_ids[chunk_start:chunk_start + 10]))
    return out


def free_faces(resolved_parts, part_ids):
    """Compute free (boundary) faces of solid elements for contact surfaces.

    A face shared by two elements is interior; one appearing exactly once is
    on the boundary and participates in contact.  Returns a list of tuples
    (each 3 or 4 node indices in original mesh numbering).
    """
    counts = {}   # sorted-node-key -> original-node-tuple
    singles = {}

    def add_face(nodes):
        key = tuple(sorted(int(n) for n in nodes))
        if key in counts:
            counts[key] += 1
            singles.pop(key, None)
        else:
            counts[key] = 1
            singles[key] = tuple(int(n) for n in nodes)

    for p in resolved_parts:
        if p["id"] not in set(part_ids):
            continue
        for etype, conn in p["cells"].items():
            _, _, dim = ELEMENTS[etype]
            if dim != 3:
                continue
            if etype == "tetra":
                for el in conn:
                    add_face((el[0], el[2], el[1]))   # outward-facing
                    add_face((el[0], el[1], el[3]))
                    add_face((el[1], el[2], el[3]))
                    add_face((el[0], el[3], el[2]))
            elif etype == "hexahedron":
                for el in conn:
                    add_face((el[0], el[3], el[2], el[1]))
                    add_face((el[4], el[5], el[6], el[7]))
                    add_face((el[0], el[1], el[5], el[4]))
                    add_face((el[1], el[2], el[6], el[5]))
                    add_face((el[2], el[3], el[7], el[6]))
                    add_face((el[3], el[0], el[4], el[7]))

    return list(singles.values())


def surf_seg_cards(surf_id, name, faces, renum):
    """Emit /SURF/SEG from a list of free-face node tuples."""
    out = [f"/SURF/SEG/{surf_id}", title(name)]
    for seg_i, face in enumerate(faces, start=1):
        nodes = [renum[int(n)] for n in face]
        while len(nodes) < 4:
            nodes.append(0)        # pad tri faces; n4=0 marks a triangle
        out.append(i10(seg_i) + "".join(i10(n) for n in nodes[:4]))
    return out


def inter_type7_cards(inter_id, name, grnod_id, surf_id, opts):
    """Emit /INTER/TYPE7 using the radioss2020 card layout.

    Card format follows hm_cfg_files/config/CFG/radioss2026/INTER/inter_type7.cfg
    FORMAT(radioss2020) — compatible with INVERS 2025.
    """
    friction = float(opts.get("friction", 0.0))
    gap_scale = float(opts.get("gap_scale", 1.0))
    inacti = int(opts.get("initial_penetration", 0))
    stfac = float(opts.get("stiffness_scale", 0.0))

    out = [f"/INTER/TYPE7/{inter_id}", title(name)]

    igap = int(opts.get("igap", 1))  # 1 = gap from element characteristics

    out.append("# grnod_id   surf_id      Istf      Ithe      Igap                Ibag      Idel     Icurv      Iadm")
    out.append(i10(grnod_id) + i10(surf_id) + i10(0) + i10(0) + i10(igap)
               + " " * 10 + i10(0) + i10(0) + i10(0) + i10(0))

    out.append("#          Fscalegap             Gap_max             Fpenmax                         Itied")
    out.append(f20(gap_scale) + f20(0.0) + f20(0.0) + " " * 20 + i10(0))

    out.append("#              Stmin               Stmax   Percent_mesh_size               dtmin  Irem_gap   Irem_i2")
    out.append(f20(0.0) + f20(0.0) + f20(0.4) + f20(0.0) + i10(0) + i10(0))

    out.append("#              Stfac                Fric              GAPmin              Tstart               Tstop")
    out.append(f20(stfac) + f20(friction) + f20(0.0) + f20(0.0) + f20(1.0e30))

    vis_s = float(opts.get("damping", 0.01))  # default 1% critical damping

    out.append("#      IBC                        Inacti               VIS_S               VIS_F              Bumult")
    out.append(" " * 7 + "000" + " " * 20 + i10(inacti)
               + f20(vis_s) + f20(1.0) + f20(0.2))

    out.append("#    Ifric    Ifiltr               Xfreq     Iform   sens_ID   fct_IDF             AscaleF   fric_ID")
    out.append(i10(0) + i10(0) + f20(1.0) + i10(0) + i10(0) + i10(0) + f20(1.0) + i10(0))

    return out


# --------------------------------------------------------------------------
# imposed displacement / velocity
# --------------------------------------------------------------------------

DOF_MAP = {
    "x": "X", "y": "Y", "z": "Z",
    "rx": "XX", "ry": "YY", "rz": "ZZ",
}


def funct_cards(funct_id, name, xy_pairs):
    """Emit /FUNCT (radioss51 format) from a list of [x, y] pairs."""
    out = [f"/FUNCT/{funct_id}", title(name)]
    out.append("#                  X                   Y")
    for x, y in xy_pairs:
        out.append(f20(x) + f20(y))
    return out


def impdisp_cards(imp_id, name, funct_id, direction, grnod_id, opts):
    """Emit /IMPDISP (radioss51 format).

    Card layout from hm_cfg_files/config/CFG/radioss2025/LOADS/impdisp.cfg.
    direction: one of X, Y, Z, XX, YY, ZZ.
    """
    xscale = float(opts.get("time_scale", 1.0))
    magnitude = float(opts.get("scale", 1.0))
    tstart = float(opts.get("start_time", 0.0))
    tstop = float(opts.get("stop_time", 1.0e30))

    out = [f"/IMPDISP/{imp_id}", title(name)]
    out.append("#   Ifunct       DIR     Iskew   Isensor   Gnod_id")
    out.append(i10(funct_id) + f"{direction:>10s}" + i10(0) + i10(0) + i10(grnod_id))
    out.append("#            Scale_x             Scale_y              Tstart               Tstop")
    out.append(f20(xscale) + f20(magnitude) + f20(tstart) + f20(tstop))
    return out


def impvel_cards(imp_id, name, funct_id, direction, grnod_id, opts):
    """Emit /IMPVEL (radioss51 format).

    Same layout as /IMPDISP but header changes to /IMPVEL.
    """
    xscale = float(opts.get("time_scale", 1.0))
    magnitude = float(opts.get("scale", 1.0))
    tstart = float(opts.get("start_time", 0.0))
    tstop = float(opts.get("stop_time", 1.0e30))

    out = [f"/IMPVEL/{imp_id}", title(name)]
    out.append("#funct_IDT       Dir   skew_ID sensor_ID  grnod_ID")
    out.append(i10(funct_id) + f"{direction:>10s}" + i10(0) + i10(0) + i10(grnod_id))
    out.append("#            Scale_x             Scale_y              Tstart               Tstop")
    out.append(f20(xscale) + f20(magnitude) + f20(tstart) + f20(tstop))
    return out


# --------------------------------------------------------------------------
# timestep estimate
# --------------------------------------------------------------------------

def inivel_axis_cards(iv_id, iv, grnod_id):
    """Emit /FRAME/FIX + /INIVEL/AXIS: rigid rotation about an arbitrary axis.

    `axis: {origin: [x,y,z], direction: [dx,dy,dz], omega: rad/s}` spins the
    set about the line through `origin` along `direction` (right-hand rule),
    plus an optional translational `vector` (global, mm/s). /FRAME/FIX's two
    vector lines are the frame's local **Y** and **Z** axes (the cfg names them
    globalyaxis/globalzaxis; X = Y x Z), so the spin axis goes on the Y line
    and /INIVEL/AXIS rotates about DIR = Y. Passing it as the first line with
    DIR = X spun the body about a perpendicular axis (caught by the fixture spike).
    Cards: radioss110/SYSTEM/frame_fix.cfg, radioss2025/LOADS/inivel_axis.cfg.
    """
    ax = iv["axis"]
    o = [float(v) for v in ax["origin"]]
    d = np.asarray(ax["direction"], dtype=float)
    d = d / np.linalg.norm(d)
    helper = np.array([0.0, 0.0, 1.0]) if abs(d[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    p = np.cross(d, helper)
    p = p / np.linalg.norm(p)
    # Translation is given in global axes; the card wants it in the frame's
    # (rows: local X = Y x Z, local Y = spin axis, local Z = p).
    frame = np.vstack([np.cross(d, p), d, p])
    vt = frame @ np.asarray(iv.get("vector") or [0.0, 0.0, 0.0], dtype=float)
    name = iv.get("name", f"inivel_{iv_id}")
    return [
        f"/FRAME/FIX/{iv_id}", title(f"{name}_axis"),
        "#                 Ox                  Oy                  Oz",
        f20(o[0]) + f20(o[1]) + f20(o[2]),
        "#        Y-axis: X1                  Y1                  Z1",
        f20(d[0]) + f20(d[1]) + f20(d[2]),
        "#        Z-axis: X2                  Y2                  Z2",
        f20(p[0]) + f20(p[1]) + f20(p[2]),
        f"/INIVEL/AXIS/{iv_id}", title(name),
        "#      DIR  FRAME_ID  GRNOD_ID",
        f"{'Y':>10}" + i10(iv_id) + i10(grnod_id),
        "#                Vxt                 Vyt                 Vzt                  VR",
        f20(vt[0]) + f20(vt[1]) + f20(vt[2]) + f20(float(ax["omega"])),
        "#             tstart   sens_ID",
        f20(float(iv.get("start_time", 0.0))) + i10(0),
    ]


def rbody_cards(rb_id, rb, main_node, grnod_id):
    """Emit /RBODY (radioss2021 layout): `set` nodes move as one rigid body.

    `mass` and `inertia` ([Jxx, Jyy, Jzz, Jxy, Jyz, Jxz], global axes, about
    the main node) are *added* at the main node (ICOG=1) — how a body that is
    not meshed (the rest of a weapon, the rest of a robot) carries its mass.
    Card: radioss2021/RBODY/rbody.cfg.
    """
    J = [float(v) for v in (rb.get("inertia") or [0.0] * 6)]
    return [
        f"/RBODY/{rb_id}", title(rb.get("name", f"rbody_{rb_id}")),
        "#  node_ID   sens_ID   Skew_ID    Ispher                Mass   grnd_ID     Ikrem      ICoG   surf_ID",
        i10(main_node) + i10(0) + i10(0) + i10(0) + f20(float(rb.get("mass", 0.0)))
        + i10(grnod_id) + i10(0) + i10(1) + i10(0),
        "#                Jxx                 Jyy                 Jzz",
        f20(J[0]) + f20(J[1]) + f20(J[2]),
        "#                Jxy                 Jyz                 Jxz",
        f20(J[3]) + f20(J[4]) + f20(J[5]),
        "#  Ioptoff   Iexpams     Ifail",
        i10(0) + i10(0) + i10(0),
    ]


def wave_speed(m):
    """Acoustic wave speed for timestep estimation.

    Returns sqrt(E/rho) for isotropic laws, sqrt(K/rho) for Ogden, and
    sqrt(max(E11,E22)/rho) for orthotropic.
    """
    law = str(m.get("law", "johnson_cook")).lower()
    rho = float(m["density"])
    if rho <= 0:
        return 0.0

    if law in ("ogden", "law42", "mooney_rivlin", "mooney"):
        if "bulk_modulus" in m:
            K = float(m["bulk_modulus"])
        else:
            nu = float(m.get("poisson", 0.495))
            if law in ("mooney_rivlin", "mooney"):
                mu_vals = [float(m["c10"]), -float(m["c01"])]
                al_vals = [2.0, -2.0]
            else:
                mu_vals = [float(x) for x in m.get("mu", [0])]
                al_vals = [float(x) for x in m.get("alpha", [1])]
            mu0 = sum(u * a for u, a in zip(mu_vals, al_vals)) / 2
            if abs(1 - 2 * nu) < 1e-12:
                nu = 0.499
            K = 2 * abs(mu0) * (1 + nu) / (3 * (1 - 2 * nu))
        return math.sqrt(K / rho) if K > 0 else 0.0

    if law in ("compsh", "law25", "composite", "fabric", "fabri", "law19"):
        e_max = max(float(m.get("e11", 0)), float(m.get("e22", 0)))
        return math.sqrt(e_max / rho) if e_max > 0 else 0.0

    E = float(m.get("young", 0))
    return math.sqrt(E / rho) if E > 0 else 0.0


def min_edge_length(points, conn):
    """Shortest edge over a sample of elements, which bounds the timestep."""
    sample = conn if len(conn) <= 20000 else conn[
        np.linspace(0, len(conn) - 1, 20000).astype(int)]
    p = points[sample]
    best = math.inf
    n = p.shape[1]
    for a in range(n):
        for b in range(a + 1, n):
            d = np.linalg.norm(p[:, a] - p[:, b], axis=1)
            d = d[d > 0]
            if d.size:
                best = min(best, float(d.min()))
    return best


# --------------------------------------------------------------------------
# deck assembly
# --------------------------------------------------------------------------

def load_parts(mesh, spec_parts):
    """Group mesh cells into named parts using the gmsh physical groups."""
    tag_to_name = {}
    for name, (tag, _dim) in mesh.field_data.items():
        tag_to_name[int(tag)] = name

    physical = None
    for key in ("gmsh:physical", "cell_tags", "medit:ref"):
        if key in mesh.cell_data:
            physical = mesh.cell_data[key]
            break

    parts = {}
    for block_i, block in enumerate(mesh.cells):
        if block.type not in ELEMENTS:
            continue
        tags = (physical[block_i] if physical is not None
                else np.ones(len(block.data), dtype=int))
        for tag in np.unique(tags):
            name = tag_to_name.get(int(tag), f"part_{int(tag)}")
            rows = block.data[tags == tag]
            parts.setdefault(name, {}).setdefault(block.type, []).append(rows)

    merged = {}
    for name, by_type in parts.items():
        merged[name] = {t: np.vstack(chunks) for t, chunks in by_type.items()}
    return merged


def match_part_spec(name, spec_parts):
    """First `parts:` entry whose glob matches, so `match: '*'` acts as default."""
    for entry in spec_parts:
        if fnmatch.fnmatch(name, str(entry.get("match", "*"))):
            return entry
    return None


def build(mesh_path, spec, name, outdir):
    mesh = meshio.read(mesh_path)
    points = np.asarray(mesh.points, dtype=float)
    bbox = (points.min(axis=0), points.max(axis=0))

    spec_parts = spec.get("parts") or [{"match": "*"}]
    materials = spec.get("materials") or {}
    parts = load_parts(mesh, spec_parts)
    if not parts:
        sys.exit(f"build_deck: {mesh_path} contains no supported elements "
                 f"({', '.join(sorted(ELEMENTS))})")

    unit_key = str(spec.get("units", "mm_Mg_s"))
    if unit_key not in UNITS:
        sys.exit(f"build_deck: unknown unit system '{unit_key}' "
                 f"(choose from {', '.join(sorted(UNITS))})")
    units = UNITS[unit_key]

    # --- resolve parts -> material/property, and assign ids -----------------
    resolved = []
    used_nodes = set()
    part_nodes = {}
    elem_id = 0
    report_parts = []

    for pid, pname in enumerate(sorted(parts), start=1):
        entry = match_part_spec(pname, spec_parts)
        if entry is None:
            sys.exit(f"build_deck: part '{pname}' matches no entry in `parts:`")

        by_type = parts[pname]
        dims = {ELEMENTS[t][2] for t in by_type}
        if len(dims) > 1:
            sys.exit(f"build_deck: part '{pname}' mixes 2D and 3D elements; "
                     "mesh shells and solids as separate parts")
        dim = dims.pop()

        nodes_here = set()
        counts = {}
        for etype, conn in by_type.items():
            nodes_here |= set(np.unique(conn).tolist())
            counts[etype] = len(conn)
            elem_id += len(conn)
        used_nodes |= nodes_here
        part_nodes[pname] = nodes_here

        layup_spec = entry.get("layup")
        if layup_spec:
            # Composite layup: per-ply material, thickness, angle
            if dim == 2:
                sys.exit(f"build_deck: part '{pname}' has a layup but uses "
                         "shell elements; composite layups require hexahedral "
                         "(brick) elements for thick-shell (TSH_COMP) "
                         "properties — use 'element: hex' or assign a single "
                         "COMPSH material instead")
            for i, ply in enumerate(layup_spec):
                ply_mat = ply.get("material")
                if not ply_mat:
                    sys.exit(f"build_deck: part '{pname}' layup ply {i+1} "
                             "has no material")
                if ply_mat not in materials:
                    sys.exit(f"build_deck: part '{pname}' layup ply {i+1} "
                             f"references material '{ply_mat}' which is not "
                             "defined under `materials:`")
                if "thickness" not in ply:
                    sys.exit(f"build_deck: part '{pname}' layup ply {i+1} "
                             "has no thickness")
            total_t = sum(float(ply["thickness"]) for ply in layup_spec)
            first_mat = layup_spec[0]["material"]
            resolved.append({
                "id": pid, "name": pname, "dim": dim, "cells": by_type,
                "material": first_mat, "mat": materials[first_mat],
                "thickness": total_t, "layup": layup_spec,
                "opts": entry.get("options") or {},
            })
            report_parts.append({"name": pname, "id": pid, "dim": dim,
                                 "material": f"layup ({len(layup_spec)} plies)",
                                 "elements": counts})
        else:
            mat_name = entry.get("material")
            if mat_name is None:
                sys.exit(f"build_deck: part '{pname}' has no `material` "
                         "or `layup`")
            if mat_name not in materials:
                sys.exit(f"build_deck: part '{pname}' references material "
                         f"'{mat_name}', which is not defined under "
                         "`materials:`")
            resolved.append({
                "id": pid, "name": pname, "dim": dim, "cells": by_type,
                "material": mat_name, "mat": materials[mat_name],
                "thickness": entry.get("thickness"),
                "opts": entry.get("options") or {},
            })
            report_parts.append({"name": pname, "id": pid, "dim": dim,
                                 "material": mat_name, "elements": counts})

    # --- material validation & shell formulation overrides ------------------
    for part in resolved:
        law = str(part["mat"].get("law", "johnson_cook")).lower()
        if law in ("fabric", "fabri", "law19"):
            if part["dim"] == 3:
                sys.exit(f"build_deck: material '{part['material']}' uses "
                         f"law 'fabric' which requires shell elements, but "
                         f"part '{part['name']}' is solid — use a shell "
                         "mesh (element: quad) or 'compsh' for thick shells")
            if "ishell" not in part["opts"]:
                # Fabric (orthotropic) requires Ishell=12 (Q4/DKT); the
                # default Ishell=24 (QEPH) is isotropic only.
                part["opts"]["ishell"] = 12
        if law in ("compsh", "law25", "composite"):
            if part["dim"] == 2:
                sys.exit(f"build_deck: material '{part['material']}' uses "
                         "law 'compsh' which requires thick-shell (hex) "
                         f"elements, but part '{part['name']}' uses thin "
                         "shells — use 'law: fabric' for quad shells or "
                         "mesh with hex elements")

    # Radioss wants contiguous node ids; renumber and keep the map.
    node_order = sorted(used_nodes)
    renum = {old: new for new, old in enumerate(node_order, start=1)}

    # --- node sets ----------------------------------------------------------
    sets = {}
    for set_i, (sname, sspec) in enumerate(sorted((spec.get("sets") or {}).items()), start=1):
        try:
            idx = select_nodes(sspec, points, part_nodes, bbox)
        except ValueError as exc:
            sys.exit(f"build_deck: set '{sname}': {exc}")
        idx = [i for i in idx.tolist() if i in renum]
        if not idx:
            sys.exit(f"build_deck: set '{sname}' selected no nodes — check the "
                     f"selector against the bounding box "
                     f"{bbox[0].tolist()} .. {bbox[1].tolist()}")
        # `with_nodes` adds named `extra_nodes` (e.g. a rigid body's main node)
        sets[sname] = {"id": set_i, "nodes": idx, "extra": list(sspec.get("with_nodes") or [])}

    def set_id(sname, context):
        if sname not in sets:
            sys.exit(f"build_deck: {context} references undefined set "
                     f"'{sname}' (defined: {', '.join(sorted(sets)) or 'none'})")
        return sets[sname]["id"]

    # --- starter ------------------------------------------------------------
    L = ["#RADIOSS STARTER", "/BEGIN", title(name),
         i10(INVERS) + i10(0),
         f"{units['mass']:>20}{units['length']:>20}{units['time']:>20}",
         f"{units['mass']:>20}{units['length']:>20}{units['time']:>20}"]

    L.append("/NODE")
    L.append("#   node               X               Y               Z")
    for old in node_order:
        x, y, z = points[old]
        L.append(i10(renum[old]) + f20(x) + f20(y) + f20(z))
    # Free-standing nodes that belong to no element: a rigid body's main node
    # (e.g. on a weapon's spin axis). Numbered after the mesh nodes.
    extra_ids = {}
    for k, en in enumerate(spec.get("extra_nodes") or [], start=1):
        extra_ids[en["name"]] = len(node_order) + k
        x, y, z = (float(v) for v in en["xyz"])
        L.append(i10(extra_ids[en["name"]]) + f20(x) + f20(y) + f20(z))

    eid = 0
    for part in resolved:
        for etype, conn in sorted(part["cells"].items()):
            keyword, npe, _ = ELEMENTS[etype]
            L.append(f"/{keyword}/{part['id']}")
            L.append("#  elemID" + "".join(f"      n{i + 1}" for i in range(npe)))
            for row in conn:
                eid += 1
                L.append(i10(eid) + "".join(i10(renum[int(v)]) for v in row))

    # --- material ID map (one ID per unique material name) ------------------
    all_mat_names = set()
    for part in resolved:
        if "layup" in part:
            for ply in part["layup"]:
                all_mat_names.add(ply["material"])
        else:
            all_mat_names.add(part["material"])
    mat_id_map = {mname: i for i, mname in
                  enumerate(sorted(all_mat_names), start=1)}

    # Emit /MAT cards — once per unique material
    for mname, mid in sorted(mat_id_map.items(), key=lambda x: x[1]):
        L.extend(material_cards(mid, mname, materials[mname]))

    # Emit /PROP and /PART cards — one per part
    for part in resolved:
        layup = part.get("layup")
        mat_law = str(part["mat"].get("law", "johnson_cook")).lower()
        L.extend(property_cards(part["id"], part["name"], part["dim"],
                                part["thickness"], part["opts"],
                                layup=layup, mat_id_map=mat_id_map,
                                mat_law=mat_law))
        part_mat_id = mat_id_map[part["material"]]
        L.append(f"/PART/{part['id']}")
        L.append(title(part["name"]))
        L.append("#  prop_ID    mat_ID subset_ID")
        L.append(i10(part["id"]) + i10(part_mat_id) + i10(0))

    # --- failure criteria (element erosion) --------------------------------
    has_failure = False
    emitted_fail = set()
    derive = bool(spec.get("erosion_from_eps_max"))
    for part in resolved:
        fail = part["mat"].get("failure") or (derived_failure(part["mat"]) if derive else None)
        if not fail:
            continue
        mname = part["material"]
        if mname in emitted_fail:
            continue  # already emitted for this material
        mid = mat_id_map[mname]
        model = str(fail.get("model", "")).lower()
        if not model:
            sys.exit(f"build_deck: material '{mname}' has a "
                     "`failure:` block but no `model:` "
                     f"(use {', '.join(sorted(set(FAIL_MODELS.values()), key=lambda f: f.__name__))}"
                     ")")
        if model not in FAIL_MODELS:
            sys.exit(f"build_deck: material '{mname}': unknown "
                     f"failure model '{model}' "
                     f"(use {', '.join(sorted(FAIL_MODELS))})")
        L.extend(FAIL_MODELS[model](mid, fail))
        emitted_fail.add(mname)
        has_failure = True

    for sname, s in sorted(sets.items(), key=lambda kv: kv[1]["id"]):
        L.append(f"/GRNOD/NODE/{s['id']}")
        L.append(title(sname))
        ids = [renum[i] for i in s["nodes"]]
        for extra in s["extra"]:
            if extra not in extra_ids:
                sys.exit(f"build_deck: set '{sname}': with_nodes names unknown extra node '{extra}'")
            ids.append(extra_ids[extra])
        for chunk_start in range(0, len(ids), 10):
            L.append("".join(i10(v) for v in ids[chunk_start:chunk_start + 10]))

    for bc_i, bc in enumerate(spec.get("boundary_conditions") or [], start=1):
        fixed = {str(d).lower() for d in (bc.get("fix") or [])}
        tra = "".join("1" if a in fixed else "0" for a in ("x", "y", "z"))
        rot = "".join("1" if a in fixed else "0" for a in ("rx", "ry", "rz"))
        L.append(f"/BCS/{bc_i}")
        L.append(title(bc.get("name", f"bc_{bc_i}")))
        L.append("#  Tra rot   skew_ID  grnod_ID")
        L.append(f"   {tra} {rot}" + i10(0)
                 + i10(set_id(bc["set"], f"boundary_conditions[{bc_i}]")))

    for iv_i, iv in enumerate(spec.get("initial_velocity") or [], start=1):
        if "axis" in iv:
            L.extend(inivel_axis_cards(iv_i, iv, set_id(iv["set"], f"initial_velocity[{iv_i}]")))
            continue
        vx, vy, vz = (float(v) for v in iv["vector"])
        L.append(f"/INIVEL/TRA/{iv_i}")
        L.append(title(iv.get("name", f"inivel_{iv_i}")))
        L.append("#                 Vx                  Vy                  Vz   Gnod_id   Skew_id")
        L.append(f20(vx) + f20(vy) + f20(vz)
                 + i10(set_id(iv["set"], f"initial_velocity[{iv_i}]")) + i10(0))
        # The reader warns unless the optional start-time/sensor card is present.
        L.append("#             tstart   sens_ID")
        L.append(f20(float(iv.get("start_time", 0.0))) + i10(0))

    # --- rigid bodies ---------------------------------------------------------
    for rb_i, rb in enumerate(spec.get("rigid_bodies") or [], start=1):
        if rb.get("main") not in extra_ids:
            sys.exit(f"build_deck: rigid_bodies[{rb_i}]: main must name an extra_nodes entry")
        L.extend(rbody_cards(rb_i, rb, extra_ids[rb["main"]],
                             set_id(rb["set"], f"rigid_bodies[{rb_i}]")))

    # --- prescribed motion (imposed displacement / velocity) ---------------
    prescribed = spec.get("prescribed") or []
    funct_id_base = 1000   # high base to avoid collisions
    has_prescribed = bool(prescribed)
    for pm_i, pm in enumerate(prescribed, start=1):
        dof = str(pm.get("dof", "z")).lower()
        if dof not in DOF_MAP:
            sys.exit(f"build_deck: prescribed[{pm_i}]: unknown dof '{dof}' "
                     f"(use x, y, z, rx, ry, rz)")
        direction = DOF_MAP[dof]
        pm_type = str(pm.get("type", "displacement")).lower()
        if pm_type not in ("displacement", "velocity"):
            sys.exit(f"build_deck: prescribed[{pm_i}]: type must be "
                     "'displacement' or 'velocity'")

        # Build the time curve
        ctrl = spec.get("control") or {}
        end_time = float(ctrl.get("end_time", 1.0e-3))
        if "function" in pm:
            xy = pm["function"]
        elif "ramp" in pm:
            # ramp: linearly from 0 to <value> over the simulation time
            xy = [[0, 0], [end_time, float(pm["ramp"])]]
        else:
            sys.exit(f"build_deck: prescribed[{pm_i}]: needs either 'function' "
                     "(list of [time, value] pairs) or 'ramp' (final value)")

        funct_id = funct_id_base + pm_i
        pname = pm.get("name", f"prescribed_{pm_i}")
        L.extend(funct_cards(funct_id, f"funct_{pname}", xy))

        grnod = set_id(pm["set"], f"prescribed[{pm_i}]")
        opts = {
            "scale": pm.get("scale", 1.0),
            "time_scale": pm.get("time_scale", 1.0),
            "start_time": pm.get("start_time", 0.0),
            "stop_time": pm.get("stop_time", 1.0e30),
        }
        if pm_type == "displacement":
            L.extend(impdisp_cards(pm_i, pname, funct_id, direction, grnod, opts))
        else:
            L.extend(impvel_cards(pm_i, pname, funct_id, direction, grnod, opts))

    # --- time history ------------------------------------------------------
    # Without a /TH block the T01 file is written but stays empty, and
    # th_to_csv fails with "END OF FILE DURING READING".
    out_spec = spec.get("output") or {}
    th_id = 0
    if out_spec.get("parts", True):
        th_id += 1
        L.append(f"/TH/PART/{th_id}")
        L.append(title("part energies"))
        L.append("#      var       var       var       var       var       var       var       var       var       var")
        L.append(f"{'DEF':<10}")
        L.append("#      Obj       Obj       Obj       Obj       Obj       Obj       Obj       Obj       Obj       Obj")
        pids = [p["id"] for p in resolved]
        for chunk_start in range(0, len(pids), 10):
            L.append("".join(i10(v) for v in pids[chunk_start:chunk_start + 10]))

    for sname in out_spec.get("node_sets") or []:
        if sname not in sets:
            sys.exit(f"build_deck: output.node_sets references undefined set "
                     f"'{sname}' (defined: {', '.join(sorted(sets)) or 'none'})")
        th_id += 1
        ids = [renum[i] for i in sets[sname]["nodes"]]
        L.append(f"/TH/NODE/{th_id}")
        L.append(title(f"nodes: {sname}"))
        L.append("#      var       var       var       var       var       var       var       var       var       var")
        L.append(f"{'DEF':<10}")
        L.append("#      Obj       Obj       Obj       Obj       Obj       Obj       Obj       Obj       Obj       Obj")
        for chunk_start in range(0, len(ids), 10):
            L.append("".join(i10(v) for v in ids[chunk_start:chunk_start + 10]))

    # --- contact interfaces ---------------------------------------------------
    contact_specs = parse_contact_spec(spec)
    has_contact = False
    if contact_specs and len(resolved) > 1:
        has_contact = True
        # ID space for contact objects starts above user-defined sets.
        next_set_id = max((s["id"] for s in sets.values()), default=0) + 1
        surf_id_base = 1  # /SURF IDs are a separate namespace

        # Are the parts shells or solids?  (Mixed isn't supported yet.)
        part_dims = {p["dim"] for p in resolved}
        is_solid = 3 in part_dims

        for ci, cspec in enumerate(contact_specs, start=1):
            # Determine which parts participate.  Default: all of them.
            secondary_pattern = cspec.get("secondary", "*")
            main_pattern = cspec.get("main", "*")

            sec_pids = [p["id"] for p in resolved
                        if fnmatch.fnmatch(p["name"], secondary_pattern)]
            main_pids = [p["id"] for p in resolved
                         if fnmatch.fnmatch(p["name"], main_pattern)]

            if not sec_pids or not main_pids:
                sys.exit(f"build_deck: contact[{ci}]: secondary='{secondary_pattern}' "
                         f"matched {len(sec_pids)} parts, main='{main_pattern}' "
                         f"matched {len(main_pids)} parts — need at least one each")

            # Gather nodes belonging to the secondary-side parts.
            sec_nodes = set()
            for p in resolved:
                if p["id"] in sec_pids:
                    for conn in p["cells"].values():
                        sec_nodes |= set(np.unique(conn).tolist())
            sec_node_ids = sorted(renum[i] for i in sec_nodes if i in renum)

            # /GRNOD/NODE for secondary nodes
            grnod_id = next_set_id
            next_set_id += 1
            L.append(f"/GRNOD/NODE/{grnod_id}")
            cname = cspec.get("name", f"contact_{ci}")
            L.append(title(f"{cname}_secondary"))
            for chunk_start in range(0, len(sec_node_ids), 10):
                L.append("".join(i10(v) for v in
                                 sec_node_ids[chunk_start:chunk_start + 10]))

            # /SURF — shell parts use /SURF/PART; solid parts need /SURF/SEG
            # built from the free (boundary) faces of the mesh.
            surf_id = surf_id_base + ci - 1
            if is_solid:
                faces = free_faces(resolved, main_pids)
                if not faces:
                    sys.exit(f"build_deck: contact[{ci}]: no boundary faces "
                             "found on main-side solid parts")
                L.extend(surf_seg_cards(surf_id, f"{cname}_main",
                                        faces, renum))
            else:
                L.extend(surf_part_cards(surf_id, f"{cname}_main", main_pids))

            # /INTER/TYPE7
            L.extend(inter_type7_cards(ci, cname, grnod_id, surf_id, cspec))

    L.append("/END")

    # --- engine -------------------------------------------------------------
    ctrl = spec.get("control") or {}
    end_time = float(ctrl.get("end_time", 1.0e-3))
    anim_dt = float(ctrl.get("animation_dt", end_time / 20.0))
    th_dt = float(ctrl.get("th_dt", end_time / 200.0))

    E = [f"/RUN/{name}/1", f20(end_time),
         f"/VERS/{INVERS}",
         "/ANIM/DT", f20(0.0) + f20(anim_dt)]
    default_anim = ["ELEM/VONM", "ELEM/EPSP", "VECT/VEL", "VECT/DISP"]
    if has_failure:
        # Add damage + element-off output so the VTK shows erosion progression.
        # ELEM/DAM1 = scalar damage (direction 1),
        # ELEM/OFF = element status (deleted elements).
        # Keywords from hm_cfg_files/config/CFG/radioss2019/CARDS/eng_anim_elem.cfg
        # Note: ELEM/FAIL is listed in cfg but rejected by the engine; omit it.
        default_anim.extend(["ELEM/DAM1", "ELEM/OFF"])
    for req in ctrl.get("animation", default_anim):
        E.append(f"/ANIM/{req}")
    E.append("/TFILE/1")
    E.append(f20(th_dt))
    if "timestep_scale" in ctrl or "timestep_min" in ctrl:
        E.append("/DT/NODA/CST")
        E.append(f20(ctrl.get("timestep_scale", 0.9)) + f20(ctrl.get("timestep_min", 0.0)))
    E.append(f"/PRINT/{int(ctrl.get('print_every', -100))}")
    E.append("/STOP")
    E.append(f20(0.0) + f20(0.0) + f20(0.0) + i10(0) + i10(0))
    E.append("/END")

    # --- timestep estimate --------------------------------------------------
    estimate = None
    worst = math.inf
    for part in resolved:
        m = part["mat"]
        c = wave_speed(m)
        if c <= 0:
            continue
        for etype, conn in part["cells"].items():
            lmin = min_edge_length(points, conn)
            if math.isfinite(lmin):
                worst = min(worst, lmin / c)
    if math.isfinite(worst):
        dt = 0.9 * worst
        estimate = {
            "stable_timestep": dt,
            "cycles_for_end_time": int(end_time / dt) if dt > 0 else None,
            "note": "elastic estimate from shortest edge and wave speed; "
                    "the solver's own timestep will differ somewhat",
        }

    os.makedirs(outdir, exist_ok=True)
    starter = os.path.join(outdir, f"{name}_0000.rad")
    engine = os.path.join(outdir, f"{name}_0001.rad")
    with open(starter, "w") as fh:
        fh.write("\n".join(L) + "\n")
    with open(engine, "w") as fh:
        fh.write("\n".join(E) + "\n")

    notes = []
    if len(resolved) > 1 and not has_contact:
        notes.append(
            f"this deck has {len(resolved)} parts but no contact definition — "
            "they will interpenetrate freely; add a `contact:` block to enable "
            "contact interfaces (/INTER/TYPE7)")
    if not (spec.get("boundary_conditions") or []) and not (spec.get("rigid_bodies") or []):
        notes.append("no boundary conditions defined — the model is unrestrained")

    result = {
        "name": name,
        "warnings": notes,
        "starter": os.path.abspath(starter),
        "engine": os.path.abspath(engine),
        "units": {"system": unit_key, **units},
        "nodes": len(node_order),
        "elements": eid,
        "parts": report_parts,
        "sets": {k: {"id": v["id"], "nodes": len(v["nodes"])} for k, v in sets.items()},
        "bounding_box": {"min": bbox[0].tolist(), "max": bbox[1].tolist()},
        "end_time": end_time,
        "timestep": estimate,
    }
    if has_contact:
        result["contact"] = {
            "type": "TYPE7",
            "interfaces": len(contact_specs),
            "note": "penalty node-to-surface contact",
        }
    if has_prescribed:
        result["prescribed"] = {
            "count": len(prescribed),
            "entries": [
                {
                    "dof": DOF_MAP[str(pm.get("dof", "z")).lower()],
                    "type": str(pm.get("type", "displacement")).lower(),
                    "set": pm.get("set"),
                }
                for pm in prescribed
            ],
        }
    if has_failure:
        fail_entries = []
        for part in resolved:
            fail = part["mat"].get("failure") or (
                derived_failure(part["mat"]) if spec.get("erosion_from_eps_max") else None)
            if fail:
                fail_entries.append({
                    "part": part["name"],
                    "material": part["material"],
                    "model": str(fail.get("model", "")).lower(),
                })
        result["failure"] = {
            "count": len(fail_entries),
            "note": "element erosion active — elements deleted when failure criterion met",
            "entries": fail_entries,
        }
    return result


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="build_deck.py",
        description="Build an OpenRadioss deck from a gmsh mesh and a YAML spec",
    )
    p.add_argument("mesh", help="mesh file from mesh_step.py (.msh)")
    p.add_argument("-c", "--config", required=True, help="simulation spec (YAML)")
    p.add_argument("-o", "--out-dir", default="build", help="output directory (default: build)")
    p.add_argument("-n", "--name", help="run name (default: from the spec, else the mesh basename)")
    p.add_argument("--json", dest="json_out", help="also write the summary here")
    args = p.parse_args(argv)

    if not os.path.isfile(args.mesh):
        sys.exit(f"build_deck: no such mesh: {args.mesh}")
    if not os.path.isfile(args.config):
        sys.exit(f"build_deck: no such config: {args.config}")

    with open(args.config) as fh:
        spec = yaml.safe_load(fh) or {}

    name = args.name or spec.get("name") or os.path.splitext(os.path.basename(args.mesh))[0]
    # Radioss derives output filenames from the run name, so keep it tame.
    name = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in str(name))

    summary = build(args.mesh, spec, name, args.out_dir)
    text = json.dumps(summary, indent=2)
    if args.json_out:
        with open(args.json_out, "w") as fh:
            fh.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
