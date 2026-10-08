#!/usr/bin/env bash
# Exercise every script against a corpus of real STEP files.
#
#   STEP_TEST_DIR=/path/to/files bash tests/run_tests.sh
#
# Skips cleanly when the corpus is missing, and skips the OpenCASCADE cases
# when neither `OCC` nor `nix-shell` is available.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS="$HERE/../scripts"
DIR="${STEP_TEST_DIR:-./plugins/cad-step/tests/corpus/}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0; fail=0; skip=0
ok()   { printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail+1)); }
skp()  { printf '  \033[33mSKIP\033[0m %s\n' "$1"; skip=$((skip+1)); }

have_occ() {
  python3 -c 'import OCC' 2>/dev/null && return 0
  command -v nix-shell >/dev/null 2>&1 && return 0
  return 1
}

# checkfail NAME FILE ARGS... -- PATTERN   (command is expected to exit non-zero)
checkfail() {
  local name="$1"; shift
  local file="$1"; shift
  if [ ! -f "$DIR/$file" ]; then skp "$name (missing $file)"; return; fi
  local args=() ; while [ "$1" != "--" ]; do args+=("$1"); shift; done; shift
  local out rc
  out="$("${args[@]}" 2>&1)"; rc=$?
  if [ $rc -eq 0 ]; then bad "$name (expected non-zero exit)"; return; fi
  if echo "$out" | grep -qE "$1"; then ok "$name"
  else bad "$name (no match for /$1/)"; echo "$out" | head -6 | sed 's/^/        /'; fi
}

# check NAME FILE ARGS... -- PATTERN
check() {
  local name="$1"; shift
  local file="$1"; shift
  if [ ! -f "$DIR/$file" ]; then skp "$name (missing $file)"; return; fi
  local args=() ; while [ "$1" != "--" ]; do args+=("$1"); shift; done; shift
  local pattern="$1"
  local out
  out="$("${args[@]}" 2>&1)"
  if [ $? -ne 0 ]; then bad "$name (exit != 0)"; echo "$out" | tail -5 | sed 's/^/        /'; return; fi
  if echo "$out" | grep -qE "$pattern"; then ok "$name"
  else bad "$name (no match for /$pattern/)"; echo "$out" | head -12 | sed 's/^/        /'; fi
}

echo "corpus: $DIR"
if [ ! -d "$DIR" ]; then
  echo
  echo "  ***  NOTHING WAS TESTED  ***"
  echo "  corpus directory does not exist: $DIR"
  echo "  set STEP_TEST_DIR to a directory of .step/.stp files"
  echo
  echo "0 passed, 0 failed, everything skipped"
  exit 0
fi

ASM="inertial-v6.step"
PART="wep-rail.step"
PLATE="bottom-plate.step"
INCH="SDP_sdpsi_a_6a_4-10df05012.stp"
SPLINE="Repeat Drive Magnum 3536.STEP"
SPRING="spring-loading-mechanism.step"
MULTI="meowtybrain.step"

echo
echo "== text tier =="

check "info: assembly detected" "$ASM" \
  python3 "$SCRIPTS/step_info.py" "$DIR/$ASM" -- 'kind +assembly'
check "info: many products" "$ASM" \
  python3 "$SCRIPTS/step_info.py" "$DIR/$ASM" -- 'products +[0-9]{2,}'
check "info: many instances" "$ASM" \
  python3 "$SCRIPTS/step_info.py" "$DIR/$ASM" -- 'instances \(NAUO\) +[0-9]{2,}'
check "info: AP214 schema" "$ASM" \
  python3 "$SCRIPTS/step_info.py" "$DIR/$ASM" -- 'AUTOMOTIVE_DESIGN'
check "info: millimetre units" "$ASM" \
  python3 "$SCRIPTS/step_info.py" "$DIR/$ASM" -- 'Units +millimetre = 1 mm'
check "info: INCH file resolves to 25.4 mm" "$INCH" \
  python3 "$SCRIPTS/step_info.py" "$DIR/$INCH" -- 'Units +INCH = 25.4 mm'
check "info: single part is not an assembly" "$PLATE" \
  python3 "$SCRIPTS/step_info.py" "$DIR/$PLATE" -- 'kind +single part'
check "info: B-spline heavy file parses" "$SPLINE" \
  python3 "$SCRIPTS/step_info.py" "$DIR/$SPLINE" -- 'B_SPLINE'

check "tree: root product" "$ASM" \
  python3 "$SCRIPTS/step_tree.py" "$DIR/$ASM" --depth 0 -- '^inertial-v6'
check "tree: instance total" "$ASM" \
  python3 "$SCRIPTS/step_tree.py" "$DIR/$ASM" -- '[0-9]{2,} instances total'
check "tree: nested subassembly" "$ASM" \
  python3 "$SCRIPTS/step_tree.py" "$DIR/$ASM" -- '4520-Rotor-Assem'

check "placements: motor is rotated 90 deg about X" "$ASM" \
  python3 "$SCRIPTS/step_placements.py" "$DIR/$ASM" --part BA-4520-Motor -- \
  '90.00 deg / [+-]X'
check "placements: relative frame changes coordinates" "$ASM" \
  python3 "$SCRIPTS/step_placements.py" "$DIR/$ASM" --relative-to wep-rails -- \
  'frame of .wep-rails.'

check "features: rail 30 mm through bore" "$PART" \
  python3 "$SCRIPTS/step_features.py" "$DIR/$PART" -- '30\.000 +9\.530 +through'
check "features: rail wall thickness 9.53" "$PART" \
  python3 "$SCRIPTS/step_features.py" "$DIR/$PART" --planes -- '\+X +9\.530'
check "features: rail is 46 mm tall" "$PART" \
  python3 "$SCRIPTS/step_features.py" "$DIR/$PART" --planes -- '\+Z +46\.000'
check "features: bolt circle found" "$SPRING" \
  python3 "$SCRIPTS/step_features.py" "$DIR/$SPRING" -- 'Bolt circles'
check "features: M3 clearance recognised" "$SPRING" \
  python3 "$SCRIPTS/step_features.py" "$DIR/$SPRING" -- 'M3 close clearance'
check "features: blind pocket stays blind" "$SPRING" \
  python3 "$SCRIPTS/step_features.py" "$DIR/$SPRING" --part 4520-Mount -- ' blind '
check "features: --world changes frame" "$ASM" \
  python3 "$SCRIPTS/step_features.py" "$DIR/$ASM" --part wep-plate --world -- \
  'assembly frame'

check "bbox: rail extents" "$PART" \
  python3 "$SCRIPTS/step_bbox.py" "$DIR/$PART" -- '9\.53, 263\.008, 46'

check "query: resolves a placement" "$PLATE" \
  python3 "$SCRIPTS/step_query.py" "$DIR/$PLATE" --type AXIS2_PLACEMENT_3D --limit 1 -- \
  'origin '
check "query: back-references" "$PLATE" \
  python3 "$SCRIPTS/step_query.py" "$DIR/$PLATE" --type MANIFOLD_SOLID_BREP -- \
  'used by'

check "diff: identical files report no change" "$PART" \
  python3 "$SCRIPTS/step_diff.py" "$DIR/$PART" "$DIR/$PART" -- \
  '0 added, 0 removed, 0 moved, 0 reshaped'
check "diff: different parts differ" "$PART" \
  python3 "$SCRIPTS/step_diff.py" "$DIR/$PART" "$DIR/$PLATE" -- \
  'Products only in'

check "json: valid JSON from step_info" "$ASM" \
  python3 -c "import json,subprocess,sys; json.loads(subprocess.check_output([sys.executable,'$SCRIPTS/step_info.py','$DIR/$ASM','--json'])); print('json ok')" -- \
  'json ok'
check "json: valid JSON from step_features" "$PART" \
  python3 -c "import json,subprocess,sys; d=json.loads(subprocess.check_output([sys.executable,'$SCRIPTS/step_features.py','$DIR/$PART','--json'])); print('holes',len(d['holes']))" -- \
  'holes [0-9]'

check "fasteners: names classify to designations" "$ASM" \
  python3 -c "import sys; sys.path.insert(0,'$SCRIPTS'); import step_fasteners as F; ok = F.classify('M8x70 FHCS')[1]=='M8x70 flat-head screw' and F.classify('90695A033_Medium-Strength Steel Thin-Profile Hex Nut v1')[1]=='hex nut 90695A033' and F.classify('#6-32 x 1/2 socket head cap screw')[1]=='#6-32 socket-head screw' and F.classify('- M10 Steel')[1]=='M10 fastener'; print('classify ok' if ok else 'classify BAD')" -- \
  'classify ok'
check "fasteners: structure is not mistaken for hardware" "$ASM" \
  python3 -c "import sys; sys.path.insert(0,'$SCRIPTS'); import step_fasteners as F; bad=[n for n in ('battery-cage-and-bearing-mount','spring-loaded:1','thrust-bearing-spacer','screw-boss-plate') if F.classify(n, hardware=True)[0]]; print('no false hardware' if not bad else 'FALSE POSITIVE %s' % bad)" -- \
  'no false hardware'
check "fasteners: equal-angle group reads as a bolt circle" "$ASM" \
  python3 -c "import sys,math; sys.path.insert(0,'$SCRIPTS'); import step_fasteners as F; p=[[50*math.cos(math.radians(a)),50*math.sin(math.radians(a)),12.0] for a in range(0,360,60)]; L=F._layout(p,2,[0,1]); print('spacing %.1f radius %.1f levels %s' % (L['spacing_deg'], L['radius_mean'], L['levels']))" -- \
  'spacing 60.0 radius 50.0 levels \[12.0\]'

# timing budget: the text tier must stay interactive on the big assembly
if [ -f "$DIR/$ASM" ]; then
  t0=$(date +%s%N)
  python3 "$SCRIPTS/step_info.py" "$DIR/$ASM" >/dev/null 2>&1
  ms=$(( ($(date +%s%N) - t0) / 1000000 ))
  if [ "$ms" -lt 8000 ]; then ok "timing: step_info on 9 MB assembly in ${ms} ms"
  else bad "timing: step_info took ${ms} ms (budget 8000)"; fi
fi

echo
echo "== geometry tier (OpenCASCADE) =="
if ! have_occ; then
  skp "all OpenCASCADE tests (no OCC and no nix-shell)"
else
  # cross-tier: the fast vertex box must sit inside the exact B-rep box
  check "bbox: fast tier is contained by exact tier" "$ASM" \
    python3 "$HERE/cross_tier.py" "$DIR/$ASM" -- 'cross-tier ok'
  check "measure: aluminium mass reported" "$PART" \
    python3 "$SCRIPTS/step_measure.py" "$DIR/$PART" --density aluminium -- \
    'mass +[0-9]+\.[0-9]+ g'
  checkfail "measure: unknown material rejected" "$PART" \
    python3 "$SCRIPTS/step_measure.py" "$DIR/$PART" --density unobtanium -- \
    'unknown material'
  check "fasteners: hardware bill lists the McMaster nut" "$ASM" \
    python3 "$SCRIPTS/step_fasteners.py" "$DIR/$ASM" -- 'hex nut 90695A033'
  check "fasteners: --hardware picks up the spring" "$ASM" \
    python3 "$SCRIPTS/step_fasteners.py" "$DIR/$ASM" --hardware -- \
    'compression spring 94125K631'
  check "fasteners: --density overrides the name-inferred material" "$ASM" \
    python3 "$SCRIPTS/step_fasteners.py" "$DIR/$ASM" --density titanium -- \
    'titanium \(--density\)'
  check "fasteners: layout reports radius about Z" "$ASM" \
    python3 "$SCRIPTS/step_fasteners.py" "$DIR/$ASM" -- 'Layout \(radius about Z'
  check "json: valid JSON from step_fasteners" "$ASM" \
    python3 -c "import json,subprocess,sys; d=json.loads(subprocess.check_output([sys.executable,'$SCRIPTS/step_fasteners.py','$DIR/$ASM','--json'])); print('groups',len(d['groups']),'mass',round(d['total_mass_g'],1))" -- \
    'groups [0-9]+ mass [0-9]'
  check "clash: runs on a subassembly" "$SPRING" \
    python3 "$SCRIPTS/step_clash.py" "$DIR/$SPRING" --clearance 0.2 -- \
    'pairs shared a bounding box'
  check "drawing: writes an SVG" "$PART" \
    python3 "$SCRIPTS/step_drawing.py" "$DIR/$PART" -o "$TMP/part.svg" -- \
    'visible polylines'
  if [ -s "$TMP/part.svg" ] && grep -q '<svg' "$TMP/part.svg"; then
    ok "drawing: SVG is well formed"
  elif grep -q '100%%' "$TMP/part.svg" 2>/dev/null; then
    bad "drawing: a %-format escape reached the SVG"
  else
    bad "drawing: SVG missing or empty"
  fi
  if grep -q 'width="100%%"' "$TMP/part.svg" 2>/dev/null; then
    bad "drawing: background rect carries a doubled percent"
  else
    ok "drawing: no %-format escape survives into the SVG"
  fi
  check "drawing: single view honoured" "$PART" \
    python3 "$SCRIPTS/step_drawing.py" "$DIR/$PART" --view iso -o "$TMP/iso.svg" -- \
    'views: iso'
  # Without id_of the sheet is a picture; with it, every path carries the id a
  # caller can hit-test against. A part with no visible edge in a view emits no
  # path, so ids cannot be re-attached by counting after the fact.
  check "drawing: paths carry the caller's part ids" "$SPRING" \
    python3 -c "import json,subprocess,sys; d=json.loads(subprocess.check_output([sys.executable,'$SCRIPTS/step_report.py','$DIR/$SPRING','--json','--skip','clash'])); g=d['sections']['drawing']['data']; ids=set(g['part_ids']); tagged={a.split(chr(34))[0] for a in g['svg'].split('data-part=' + chr(34))[1:]}; print(len(tagged),'tagged', tagged <= ids, len(ids),'ids')" -- \
    '[0-9]+ tagged True'
  check "drawing: each view carries the transform to put a point back in" "$SPRING" \
    python3 "$HERE/view_projection.py" "$SCRIPTS/step_report.py" "$DIR/$SPRING" -- \
    'points checked, worst excursion 0\.'
  check "drawing: no ids without a caller's id_of" "$PART" \
    python3 -c "import subprocess,sys; subprocess.check_call([sys.executable,'$SCRIPTS/step_drawing.py','$DIR/$PART','-o','$TMP/plain.svg']); print('data-part' in open('$TMP/plain.svg').read())" -- \
    'False'
  check "extract: lists parts" "$ASM" \
    python3 "$SCRIPTS/step_extract.py" "$DIR/$ASM" --list -- 'wep-plate'
  check "extract: writes a STEP file" "$ASM" \
    python3 "$SCRIPTS/step_extract.py" "$DIR/$ASM" --part wep-plate -o "$TMP/out.step" -- \
    'wrote '
  if [ -s "$TMP/out.step" ] && head -1 "$TMP/out.step" | grep -q 'ISO-10303-21'; then
    ok "extract: output re-reads as STEP"
    python3 "$SCRIPTS/step_info.py" "$TMP/out.step" >/dev/null 2>&1 \
      && ok "extract: output parses with stepcore" \
      || bad "extract: output does not parse"
  else
    bad "extract: output is not a STEP file"
  fi

  # -- shared-parse endpoint ------------------------------------------------
  # A combat robot is mostly duplicates, so the file that matters here is the
  # one with the most repeated instances, not the biggest one.
  check "join: every occurrence gets its own id" "$SPRING" \
    python3 "$HERE/join_integrity.py" "$DIR/$SPRING" -- 'parts, [0-9]+ entries'
  check "join: duplicated instances stay distinct" "$ASM" \
    python3 "$HERE/join_integrity.py" "$DIR/$ASM" -- \
    '[1-9][0-9]* products placed more than once'
  check "join: holds on a 60-product multibot" "$MULTI" \
    python3 "$HERE/join_integrity.py" "$DIR/$MULTI" -- 'parts, [0-9]+ entries'
  check "join: wireframe-only shapes are reported, not guessed" "$INCH" \
    python3 "$HERE/join_integrity.py" "$DIR/$INCH" 1 -- 'parts, [0-9]+ entries'
  check "join: a build without entity ids degrades, never guesses" "$ASM" \
    python3 "$HERE/join_degradation.py" "$DIR/$ASM" -- 'no entity ids'
  check "join: degradation is safe on the multibot too" "$MULTI" \
    python3 "$HERE/join_degradation.py" "$DIR/$MULTI" -- 'nor transforms'
  check "report: sections match the standalone scripts" "$SPRING" \
    python3 "$HERE/section_equivalence.py" "$DIR/$SPRING" -- 'sections compared'
  check "report: parts carry ids, volumes and closedness" "$SPRING" \
    python3 "$SCRIPTS/step_report.py" "$DIR/$SPRING" --skip clash,drawing -- \
    'p000 .+ exact .+ yes'
  check "report: nothing is left unreconciled" "$SPRING" \
    python3 "$SCRIPTS/step_report.py" "$DIR/$SPRING" --skip clash,drawing -- \
    '0 not reconciled between the two tiers'
  check "report: a skipped section is reported, not omitted" "$SPRING" \
    python3 -c "import json,subprocess,sys; d=json.loads(subprocess.check_output([sys.executable,'$SCRIPTS/step_report.py','$DIR/$SPRING','--json','--skip','clash,drawing'])); print(sorted(d['sections']), d['sections']['clash']['error'], len(d['parts']), 'parts')" -- \
    "'placements'.*skipped [0-9]+ parts"
  check "report: a shaft has no bore, a bored part does" "$SPRING" \
    python3 -c "import json,subprocess,sys; d=json.loads(subprocess.check_output([sys.executable,'$SCRIPTS/step_report.py','$DIR/$SPRING','--json','--skip','clash,drawing'])); b={r['name'][:11]: (r['has_cylindrical_bore'], r['max_bore_dia_mm']) for r in d['parts']}; print('shaft', b['4520-Shaft'], 'collar', b['Collar-8mm'])" -- \
    'shaft \(False, None\) collar \(True, 8'
  check "report: smallest features come with a size and a place" "$SPRING" \
    python3 -c "import json,subprocess,sys; d=json.loads(subprocess.check_output([sys.executable,'$SCRIPTS/step_report.py','$DIR/$SPRING','--json','--skip','clash,drawing'])); t=d['timestep_drivers'][0]; print(t['kind'], round(t['size_mm'],4), t['count'], len(t['center']), d['timestep_drivers_error'])" -- \
    'round 0.1375 6 3 None'
  check "report: min_feature_mm tracks the smallest of them" "$SPRING" \
    python3 -c "import json,subprocess,sys; d=json.loads(subprocess.check_output([sys.executable,'$SCRIPTS/step_report.py','$DIR/$SPRING','--json','--skip','clash,drawing'])); m={r['id']: r['min_feature_mm'] for r in d['parts']}; t=[x for x in d['timestep_drivers'] if x['part_id']=='p007'][0]; print('match', abs(m['p007']-t['size_mm'])<1e-9)" -- \
    'match True'
  check "features: fillets stay out of the tool's own output" "$SPRING" \
    python3 -c "import sys; sys.path.insert(0,'$SCRIPTS'); import step_features as f; a=f.collect('$DIR/$SPRING'); b=f.collect('$DIR/$SPRING', blends=True); print('default', 'blends' in a, 'asked', len(b['blends']))" -- \
    'default False asked [1-9]'
  check "report: a budget cuts the run short and says where" "$SPRING" \
    python3 -c "import json,subprocess,sys; d=json.loads(subprocess.check_output([sys.executable,'$SCRIPTS/step_report.py','$DIR/$SPRING','--json','--max-seconds','1'])); s=d['sections']; print(len(d['parts']),'parts',len(s),'sections', sum(1 for v in s.values() if v.get('error') or (v.get('data') or {}).get('incomplete')),'cut')" -- \
    '11 parts 7 sections [1-9] cut'
  check "report: no budget means no partial-run keys" "$SPRING" \
    python3 -c "import json,subprocess,sys; d=json.loads(subprocess.check_output([sys.executable,'$SCRIPTS/step_report.py','$DIR/$SPRING','--json','--skip','drawing'])); c=d['sections']['clash']['data']; print('incomplete' in c, 'pairs_candidate' in c, c['pairs_tested'])" -- \
    'False False 29'
  checkfail "report: an unknown section name is refused" "$SPRING" \
    python3 "$SCRIPTS/step_report.py" "$DIR/$SPRING" --skip nosuch -- \
    'unknown section'
fi

echo
printf '%d passed, %d failed, %d skipped\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ]
