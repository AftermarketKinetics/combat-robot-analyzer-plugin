#!/usr/bin/env bash
# Run an OpenRadioss deck: starter, then engine.
#
#   run_sim.sh build/plate_impact_0000.rad [-nt N] [-np N] [--check]
#
# Exits non-zero if the starter reports errors, printing them rather than
# letting the engine run on a deck the starter rejected.
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
usage: run_sim.sh STARTER.rad [options]

  -nt N     SMP threads per domain (default 1)
  -np N     SPMD domains (default 1)
  --check   run the starter only, write no restart files
USAGE
  exit 2
}

[ $# -ge 1 ] || usage
STARTER_FILE=$1; shift

NT=1; NP=1; CHECK=0
while [ $# -gt 0 ]; do
  case "$1" in
    -nt) NT=$2; shift 2 ;;
    -np) NP=$2; shift 2 ;;
    --check) CHECK=1; shift ;;
    -h|--help) usage ;;
    *) echo "run_sim: unknown option: $1" >&2; usage ;;
  esac
done

[ -f "$STARTER_FILE" ] || { echo "run_sim: no such deck: $STARTER_FILE" >&2; exit 1; }

# Locate the solver: PATH first, then an OPENRADIOSS_PATH install.
if command -v starter_linux64_gf >/dev/null 2>&1; then
  STARTER=starter_linux64_gf
  ENGINE=engine_linux64_gf
elif [ -n "${OPENRADIOSS_PATH:-}" ] && [ -x "$OPENRADIOSS_PATH/exec/starter_linux64_gf" ]; then
  STARTER="$OPENRADIOSS_PATH/exec/starter_linux64_gf"
  ENGINE="$OPENRADIOSS_PATH/exec/engine_linux64_gf"
  export RAD_CFG_PATH="${RAD_CFG_PATH:-$OPENRADIOSS_PATH/hm_cfg_files}"
  export LD_LIBRARY_PATH="$OPENRADIOSS_PATH/extlib/hm_reader/linux64:$OPENRADIOSS_PATH/extlib/h3d/lib/linux64:${LD_LIBRARY_PATH:-}"
else
  cat >&2 <<'MISSING'
run_sim: OpenRadioss not found.
  Enter the shell with the solver:  nix-shell --arg withSolver true
  or point at an existing install:  export OPENRADIOSS_PATH=/path/to/OpenRadioss
MISSING
  exit 127
fi

# The solver writes its outputs into the working directory and derives every
# filename from the deck's basename, so run from where the deck lives.
DECK_DIR=$(cd "$(dirname "$STARTER_FILE")" && pwd)
STARTER_BASE=$(basename "$STARTER_FILE")
RUN_NAME=${STARTER_BASE%_0000.rad}
cd "$DECK_DIR"

echo "==> starter: $STARTER_BASE (np=$NP)" >&2
CHECK_FLAG=()
[ "$CHECK" -eq 1 ] && CHECK_FLAG=(-check)
"$STARTER" -i "$STARTER_BASE" -np "$NP" "${CHECK_FLAG[@]}" >/dev/null 2>&1 || true

STARTER_OUT="${RUN_NAME}_0000.out"
if [ ! -f "$STARTER_OUT" ]; then
  echo "run_sim: the starter produced no .out file; the deck was not read" >&2
  exit 1
fi

ERRORS=$(grep -oE '[0-9]+ ERROR\(S\)' "$STARTER_OUT" | tail -1 | grep -oE '^[0-9]+' || echo 0)
if [ "${ERRORS:-0}" -ne 0 ]; then
  echo "run_sim: starter reported $ERRORS error(s):" >&2
  grep -A8 'ERROR ID' "$STARTER_OUT" >&2 || true
  exit 1
fi
WARNINGS=$(grep -oE '[0-9]+ WARNING\(S\)' "$STARTER_OUT" | tail -1 | grep -oE '^[0-9]+' || echo 0)
[ "${WARNINGS:-0}" -ne 0 ] && echo "    starter: $WARNINGS warning(s), see $STARTER_OUT" >&2

if [ "$CHECK" -eq 1 ]; then
  echo "==> check only; stopping before the engine" >&2
  exit 0
fi

echo "==> engine: ${RUN_NAME}_0001.rad (nt=$NT)" >&2
"$ENGINE" -i "${RUN_NAME}_0001.rad" -nt "$NT" >/dev/null 2>&1 || true

ENGINE_OUT="${RUN_NAME}_0001.out"
if grep -q 'NORMAL TERMINATION' "$ENGINE_OUT" 2>/dev/null; then
  CYCLES=$(grep -oE 'TOTAL NUMBER OF CYCLES *: *[0-9]+' "$ENGINE_OUT" | grep -oE '[0-9]+$')
  echo "==> normal termination after ${CYCLES} cycles" >&2
  echo "$DECK_DIR/$ENGINE_OUT"
else
  echo "run_sim: the engine did not terminate normally; tail of $ENGINE_OUT:" >&2
  tail -25 "$ENGINE_OUT" >&2 2>/dev/null || true
  exit 1
fi
