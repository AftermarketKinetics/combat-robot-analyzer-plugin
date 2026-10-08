#!/usr/bin/env python3
"""Fastener bill of materials from an assembly's part names plus real volumes.

STEP does not record threads, so a fastener can only be recognised from what
the CAD library called it -- ``M8x70``, ``BHCS``, ``90695A033_Medium-Strength
Steel Thin-Profile Hex Nut``.  This script classifies leaf parts by name,
groups identical fasteners, measures each instance with OpenCASCADE and
reports quantity, per-piece and total mass, and where the group sits (radius
about a spin axis, levels along it, angular spacing).

Densities are guessed from the name ("Alloy Steel", "18-8 Stainless", nylon)
and default to steel; override with --density or --material.
"""

from __future__ import annotations

import math
import re
import sys

import _common as C
from step_measure import MATERIALS, resolve_density

# Categories are tested in this order: "Nylon-Insert Locknut" must land on
# `nut`, not on `insert`, and every screw keyword is checked last because
# "shoulder screw" and "hex bolt" also contain the generic words.
CATEGORIES = [
    ("nut", ("locknut", "lock nut", "nyloc", "nylock", "hex nut", "jam nut",
             "flange nut", "t-nut", "tee nut", "weld nut", "wing nut", "nut")),
    ("washer", ("belleville", "shim ring", "washer")),
    ("rivet", ("rivet",)),
    ("pin", ("dowel pin", "roll pin", "spring pin", "clevis pin", "cotter")),
    ("standoff", ("standoff", "spacer post", "pillar")),
    ("insert", ("heat-set", "heat set", "threaded insert", "helicoil",
                "rivnut", "nutsert", "press-fit insert")),
    ("screw", ("shcs", "bhcs", "fhcs", "sbhcs", "socket head", "button head",
               "flat head", "cap screw", "machine screw", "shoulder screw",
               "set screw", "grub", "self-tapping", "thumb screw", "screw",
               "hex bolt", "carriage bolt", "bolt", "threaded rod", "stud")),
]

# Non-fastener hardware, only reported with --hardware.
HARDWARE = [
    ("spring", ("compression spring", "extension spring", "torsion spring",
                "spring")),
    ("bearing", ("ball bearing", "thrust bearing", "roller bearing",
                 "bearing", "oilite", "bushing", "bush")),
    ("seal", ("o-ring", "oring", "gasket", "seal")),
    ("magnet", ("magnet",)),
    ("retaining ring", ("retaining ring", "circlip", "snap ring", "e-clip")),
]

HEADS = [
    ("socket-head", ("shcs", "socket head", "socket-head")),
    ("button-head", ("bhcs", "sbhcs", "button head", "button-head")),
    ("flat-head", ("fhcs", "flat head", "flat-head", "countersunk", "csk")),
    ("shoulder", ("shoulder",)),
    ("set", ("set screw", "grub")),
    ("hex", ("hex head", "hex bolt", "hex cap")),
]

# name fragment -> material key in step_measure.MATERIALS
NAME_MATERIALS = [
    ("stainless", "stainless"), ("18-8", "stainless"), ("316 ", "stainless"),
    ("a2-70", "stainless"), ("a4-80", "stainless"),
    ("titanium", "titanium"), ("brass", "brass"), ("bronze", "bronze"),
    ("aluminum", "aluminium"), ("aluminium", "aluminium"),
    ("nylon", "nylon"), ("delrin", "delrin"), ("pom", "delrin"),
    ("alloy steel", "steel"), ("steel", "steel"), ("zinc-plated", "steel"),
]

# words that carry no size information, dropped before deciding whether a bare
# "M10" is the whole name or just part of a longer description
NOISE = set("""steel stainless alloy zinc plated black oxide screw screws bolt
bolts nut nuts washer washers hex socket button flat head cap shcs bhcs fhcs
long short thread threaded partially fully medium strength grade class metric
x mm and the of - _ ( ) [ ]""".split())

# nouns that mean "structure", not "hardware", when they end a part name
STRUCT_TAIL = set("""mount mounts plate plates bracket holder cage housing
block body spacer shell rail rails arm adapter base cover frame boss hub
carrier clamp guard tray chassis assembly assem""".split())

RE_MCMASTER = re.compile(r"\b(\d{4,6}[A-Z]\d{2,4})(?![0-9A-Za-z])")
RE_METRIC_LEN = re.compile(r"\bM(\d{1,2}(?:\.\d)?)\s*[x×]\s*(\d{1,3}(?:\.\d)?)\b",
                           re.IGNORECASE)
RE_METRIC = re.compile(r"\bM(\d{1,2}(?:\.\d)?)\b")
RE_NUMBER = re.compile(r"#(\d{1,2})-(\d{2})\b")
RE_FRACTION = re.compile(r"\b(\d{1,2}/\d{1,2})\s*\"?\s*-\s*(\d{2})\b")
RE_INSTANCE = re.compile(r"(\s*<\d+>|\s*\(\d+\)|\s+[vV]\d+|[_:-]\d+)$")


def _clean(name):
    """Strip the instance decorations CAD tools bolt onto copies."""
    raw = (name or "").strip()
    n = raw.replace("(Mirror)", "").strip()
    prev = None
    while prev != n:
        prev = n
        n = RE_INSTANCE.sub("", n).strip()
    n = n or raw
    # never let instance-stripping eat a thread size: "#6-32" is not "#6 <32>"
    for rx in (RE_METRIC_LEN, RE_NUMBER, RE_FRACTION):
        if rx.search(raw) and not rx.search(n):
            return raw
    return n


def _last_token(clean):
    toks = [t for t in re.split(r"[^A-Za-z0-9]+", clean.lower()) if t]
    while toks and (toks[-1].isdigit() or re.fullmatch(r"v\d+", toks[-1])):
        toks.pop()
    return toks[-1] if toks else ""


def _tail_ok(clean, kw, cat):
    """True when the matched keyword is the noun the name ends on.

    CAD part names put the noun last -- "Thin-Profile Hex Nut", "Compression
    Springs".  Without this, "battery-cage-and-bearing-mount" and
    "spring-loaded" would be billed as a bearing and a spring.
    """
    last = _last_token(clean).rstrip("s")
    if not last or not kw:
        return False
    nouns = {cat.split()[-1], re.split(r"[^a-z0-9]+", kw.lower())[-1]}
    if cat == "screw":
        nouns.update(("bolt", "stud", "rod", "shcs", "bhcs", "fhcs"))
    return any(last.endswith(n.rstrip("s")) for n in nouns)


def _size_of(name):
    """Return (designation, is_explicit) for the thread size named."""
    m = RE_METRIC_LEN.search(name)
    if m:
        return "M%sx%s" % (_trim(m.group(1)), _trim(m.group(2))), True
    m = RE_NUMBER.search(name)
    if m:
        return "#%s-%s" % (m.group(1), m.group(2)), True
    m = RE_FRACTION.search(name)
    if m:
        return '%s"-%s' % (m.group(1), m.group(2)), True
    m = RE_METRIC.search(name)
    if m:
        # A bare "M10" is a size only when it is essentially the whole name --
        # otherwise "M10" could be a revision code inside a longer label.
        rest = RE_METRIC.sub(" ", name).lower()
        rest = re.sub(r"[^a-z0-9]+", " ", rest)
        words = [w for w in rest.split() if w and w not in NOISE]
        return "M%s" % _trim(m.group(1)), not words
    return None, False


def _trim(s):
    return s.rstrip("0").rstrip(".") if "." in s else s


def _category(low, table):
    for cat, keys in table:
        for k in keys:
            if k in low:
                return cat, k
    return None, None


def classify(name, extra_patterns=(), hardware=False):
    """Name -> (kind, designation), or (None, None) when it is not hardware."""
    clean = _clean(name)
    low = clean.lower()
    size, explicit = _size_of(clean)
    sized = bool(size and explicit)
    forced = bool(extra_patterns) and C.match(clean, list(extra_patterns),
                                              default=False)
    cat, kw = _category(low, CATEGORIES)
    if cat is None and hardware:
        cat, kw = _category(low, HARDWARE)
    if cat is not None and not forced and not _tail_ok(clean, kw, cat):
        # a keyword buried mid-name is usually describing what the part mounts
        # rather than what it is ("battery-cage-and-bearing-mount").  A thread
        # size rescues it -- catalogue names put the noun first, as in "Hex Nut
        # Style 1 ANSI B18.2.4.1M - M10 Steel Grade 2H Plain" -- unless the
        # name still ends on a structural noun.
        if not size or _last_token(clean) in STRUCT_TAIL:
            cat = None
    if cat is None:
        if RE_METRIC_LEN.search(clean):
            # "M8x70" on its own is unambiguously a screw ...
            cat, kw = "screw", None
        elif sized or forced:
            # ... but a bare "M10" says only that something is M10-sized.
            cat, kw = "fastener", None
        else:
            return None, None
    head, _ = _category(low, HEADS)
    return cat, _label(clean, cat, kw, size, head)


def _label(clean, cat, kw, size, head):
    if cat == "screw":
        noun = "bolt" if kw and "bolt" in kw else "screw"
        if head:
            base = "%s %s" % (head, noun)
        elif kw and kw not in ("screw", "bolt"):
            base = kw
        else:
            base = noun
    elif kw:
        base = kw if cat in kw else "%s %s" % (kw, cat)
    else:
        base = cat
    label = "%s %s" % (size, base) if size else base
    if not size:
        mc = RE_MCMASTER.search(clean)
        if mc:
            label = "%s %s" % (label, mc.group(1))
        elif base.lower() != clean.lower():
            # keep the raw name so two different unsized nuts do not merge
            label = "%s (%s)" % (label, clean[:40])
    return label


def infer_material(name):
    low = name.lower()
    if "nylon-insert" in low or "nylon insert" in low:
        low = low.replace("nylon-insert", "").replace("nylon insert", "")
    for frag, mat in NAME_MATERIALS:
        if frag in low:
            return mat
    return None


def collect(path, parts=None, extra=(), density=None, per_part=(),
            hardware=False, axis="z", positions=False):
    import occenv
    occenv.ensure_occ()
    import occshapes as O
    return collect_from(O.load_parts(path, with_colors=False), parts, extra,
                        density, per_part, hardware, axis, positions, path=path)


def collect_from(loaded, parts=None, extra=(), density=None, per_part=(),
                 hardware=False, axis="z", positions=False, path="",
                 id_of=None):
    """As :func:`collect`, but reusing already-loaded :class:`occshapes.Part` objects.

    *id_of* maps a :class:`occshapes.Part` to a stable part id; pass it to get
    a ``part_ids`` list naming the individual fasteners behind each group.
    """
    import occshapes as O

    rules = []
    for spec in per_part:
        if "=" not in spec:
            raise SystemExit("--material needs PATTERN=MATERIAL, got %r" % spec)
        pat, mat = spec.split("=", 1)
        rules.append((pat.strip(), mat.strip(), resolve_density(mat)))

    forced_rho = resolve_density(density)
    forced_name = None
    if forced_rho is not None:
        key = str(density).strip().lower().replace(" ", "-")
        forced_name = key if key in MATERIALS else "%g g/cm3" % forced_rho

    ai = {"x": 0, "y": 1, "z": 2}[axis]
    other = [i for i in (0, 1, 2) if i != ai]

    groups = {}
    skipped = []
    for p in loaded:
        if not C.match(p.name, parts) and not C.match(p.path, parts):
            continue
        kind, label = classify(p.name, extra, hardware)
        if kind is None:
            skipped.append(p.name)
            continue
        vp = O.volume_props(p.shape)
        g = groups.setdefault(label, {
            "designation": label, "kind": kind, "count": 0,
            "volume_mm3": 0.0, "positions": [], "names": set(),
            "part_ids": [],
        })
        if id_of is not None:
            g["part_ids"].append(id_of(p))
        g["count"] += 1
        g["volume_mm3"] += vp["volume"]
        g["positions"].append(list(vp["com"]))
        g["names"].add(p.name)

    rows = []
    for g in groups.values():
        name0 = sorted(g["names"])[0]
        mat, rho, src = forced_name, forced_rho, "--density" if forced_rho else None
        for pat, mname, d in rules:
            if (C.match(g["designation"], [pat], default=False)
                    or any(C.match(n, [pat], default=False) for n in g["names"])):
                mat, rho, src = mname, d, "--material"
                break
        if rho is None:
            mat = infer_material(name0)
            if mat:
                rho, src = MATERIALS[mat], "name"
            else:
                mat, rho, src = "steel", MATERIALS["steel"], "default"
        mass = g["volume_mm3"] / 1000.0 * rho
        rows.append({
            "designation": g["designation"],
            "kind": g["kind"],
            "count": g["count"],
            "volume_mm3": g["volume_mm3"],
            "material": mat,
            "density_g_cm3": rho,
            "density_source": src,
            "mass_g": mass,
            "mass_each_g": mass / g["count"],
            "example_name": name0,
            "layout": _layout(g["positions"], ai, other),
            "positions": g["positions"] if positions else None,
        })
        if id_of is not None:
            rows[-1]["part_ids"] = g["part_ids"]

    rows.sort(key=lambda r: -r["mass_g"])
    return {
        "path": path,
        "axis": axis,
        "hardware": hardware,
        "groups": rows,
        "total_count": sum(r["count"] for r in rows),
        "total_mass_g": sum(r["mass_g"] for r in rows),
        "ignored_parts": len(skipped),
    }


def _layout(pos, ai, other):
    """Radius about the axis, levels along it, and angular spacing."""
    radii = [math.hypot(p[other[0]], p[other[1]]) for p in pos]
    levels = sorted({round(p[ai], 1) for p in pos})
    out = {
        "radius_min": min(radii), "radius_max": max(radii),
        "radius_mean": sum(radii) / len(radii),
        "levels": levels,
        "spacing_deg": None,
    }
    if len(pos) > 2 and (max(radii) - min(radii)) < 0.5 and out["radius_mean"] > 1.0:
        ang = sorted((math.degrees(math.atan2(p[other[1]], p[other[0]])) % 360.0)
                     for p in pos)
        gaps = [(b - a) for a, b in zip(ang, ang[1:])] + [ang[0] + 360.0 - ang[-1]]
        if max(gaps) - min(gaps) < 1.0:
            out["spacing_deg"] = sum(gaps) / len(gaps)
    return out


def show(d):
    C.table(
        [[r["designation"], r["count"], "%.2f" % r["mass_each_g"],
          "%.1f" % r["mass_g"], "%.2f" % (r["volume_mm3"] / 1000.0),
          "%s %s" % (r["material"],
                     "" if r["density_source"] == "name" else
                     "(%s)" % r["density_source"])]
         for r in d["groups"]],
        ["fastener", "qty", "each g", "total g", "cm3", "material"],
        aligns=["<", ">", ">", ">", ">", "<"])
    if not d["groups"]:
        print("  no fasteners recognised in part names -- STEP has no thread "
              "data, so\n  this only sees what the CAD library named things "
              "(try --pattern or --hardware)")
    C.heading("Total")
    print("  fasteners   %d in %d groups" % (d["total_count"], len(d["groups"])))
    print("  mass        %.1f g  (%.3f kg)"
          % (d["total_mass_g"], d["total_mass_g"] / 1000.0))
    print("  ignored     %d part(s) not named as %s"
          % (d["ignored_parts"], "hardware" if d.get("hardware") else "fasteners"))

    if d["groups"]:
        C.heading("Layout (radius about %s, levels along %s)"
                  % (d["axis"].upper(), d["axis"].upper()))
        rows = []
        for r in d["groups"]:
            L = r["layout"]
            rad = ("%.1f" % L["radius_mean"] if L["radius_max"] - L["radius_min"] < 0.5
                   else "%.1f-%.1f" % (L["radius_min"], L["radius_max"]))
            lv = ", ".join("%g" % v for v in L["levels"][:6])
            if len(L["levels"]) > 6:
                lv += ", ..."
            rows.append([r["designation"], r["count"], rad, lv,
                         "-" if L["spacing_deg"] is None
                         else "%.1f deg" % L["spacing_deg"]])
        C.table(rows, ["fastener", "qty", "radius", "levels", "spacing"],
                aligns=["<", ">", ">", "<", ">"])

    if any(r["positions"] for r in d["groups"]):
        C.heading("Positions")
        for r in d["groups"]:
            if not r["positions"]:
                continue
            print("  %s (%d)" % (r["designation"], r["count"]))
            for p in r["positions"]:
                print("     %s" % C.fmt_xyz(p, 2))


def main(argv=None):
    ap = C.base_parser(__doc__)
    ap.add_argument("--part", action="append", default=[],
                    help="only parts whose name or path matches")
    ap.add_argument("--pattern", action="append", default=[], metavar="PAT",
                    help="also treat names matching PAT as fasteners")
    ap.add_argument("--hardware", action="store_true",
                    help="include springs, bearings, seals, magnets, clips")
    ap.add_argument("--density", help="g/cm3 or material name, forced on every group")
    ap.add_argument("--material", action="append", default=[], metavar="PAT=MAT",
                    help="per-group density, e.g. --material '*standoff*=aluminium'")
    ap.add_argument("--axis", choices=["x", "y", "z"], default="z",
                    help="axis the radius and levels are measured about (default z)")
    ap.add_argument("--positions", action="store_true",
                    help="list every instance's centre")
    a = ap.parse_args(argv)
    resolve_density(a.density)   # fail fast, before the nix-shell re-exec
    data = collect(a.file, a.part, a.pattern, a.density,
                   a.material, a.hardware, a.axis, a.positions)
    C.emit(data, a.json, show)


if __name__ == "__main__":
    sys.exit(main())
