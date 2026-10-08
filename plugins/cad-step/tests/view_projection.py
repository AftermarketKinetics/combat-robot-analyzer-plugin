"""Every drawn point of a part falls inside that part's projected bbox.

`_svg_path` bakes the projection into the coordinates, so a caller wanting to
put a world point into the sheet -- an approach arrow, a marker, a dimension --
can only do it from the transform the view group carries. This checks that
transform against the geometry the same run drew.
"""
import itertools, json, re, subprocess, sys

report, model = sys.argv[1], sys.argv[2]
d = json.loads(subprocess.check_output([sys.executable, report, model, "--json", "--skip", "clash"]))
svg = d["sections"]["drawing"]["data"]["svg"]
dot = lambda a, b: sum(x * y for x, y in zip(a, b))

worst, checked = 0.0, 0
for view in d["sections"]["drawing"]["data"]["views"]:
    g = re.search(r'<g class="view" data-view="%s"[^>]*>' % view, svg)
    if not g:
        print("no view group for %s" % view)
        raise SystemExit(1)
    g = g.group(0)
    a = dict(re.findall(r'data-([a-z]+)="([^"]*)"', g))
    vec = lambda k: [float(x) for x in a[k].split(",")]
    ox, oy, sc = float(a["ox"]), float(a["oy"]), float(a["scale"])
    body = svg[svg.index(g) + len(g): svg.index("</g>", svg.index(g))]
    for pid in set(re.findall(r'data-part="([^"]+)"', body)):
        part = next((p for p in d["parts"] if p["id"] == pid), None)
        if not part or not part.get("bbox"):
            continue
        corners = list(itertools.product(*zip(part["bbox"]["min"], part["bbox"]["max"])))
        px = [ox + dot(c, vec("right")) * sc for c in corners]
        py = [oy - dot(c, vec("up")) * sc for c in corners]
        m = re.search(r'data-part="%s"[^>]*d="([^"]*)"' % re.escape(pid), body)
        nums = [float(n) for n in re.findall(r"-?\d+\.?\d*", m.group(1))]
        for x, y in zip(nums[0::2], nums[1::2]):
            worst = max(worst, min(px) - x, x - max(px), min(py) - y, y - max(py))
            checked += 1
print("%d points checked, worst excursion %.3f px" % (checked, worst))
raise SystemExit(0 if worst < 0.5 else 1)
