# models.py

## Function

Shared types: the vocabulary the load-case modules speak. In v1 it was also
the vocabulary of every gate and AWS handler.

Copied from v1 (`cra.models`); imports rewritten to `case.`.

In v2 only part of it has callers: `Aim` (`case_cli.py`, `loadcase`),
`ImpactorSpec` (`impactor`, `tooth`, `loadcase`), and `WeightClass`,
`EnergyLevel`, `OpponentArchetype` (`impactor`, `weapon_size`). The rest — `Bonus`,
`PartRole`, `MaterialSource`, `CaseChoice`, `GateResult`, `PartAssignment`,
`Job` — is v1 job-pipeline vocabulary (gates B0–B7, DynamoDB, the Batch
handover) with no v2 caller, present because the file was copied whole. The
sections below that describe it are v1 history, kept for the reasoning.

## Interface

- Enums: `WeightClass`, `Bonus`, `PartRole`, `MaterialSource`, `CaseChoice`,
  `EnergyLevel`, `OpponentArchetype`.
  - `CaseChoice` — `both` / `lc1` / `lc2`, with `.keys` (the case keys it
    runs, in report order) and `.runs(case)`.
- `GateResult` — the uniform return of every v1 gate. `.ok()` / `.fail()`.
- `PartAssignment` — one row of the v1 submit form: `part_id`, `role`,
  `material`, `material_source` (defaults to `MaterialSource.DEFAULT`).
- `EnergyLevel` — `typical` / `high`: picks the weapon's RPM
  (`impactor.RPM_PRESETS`). `OpponentArchetype` — `horizontal` /
  `vertical_drum`: the default weapon shape (bar / disc) and the spin axis.
- `ImpactorSpec` — the opponent's spinning weapon and its tooth, resolved by
  `impactor.impactor_spec` from the presets and any user overrides. Required
  fields `weight_class`, `energy_level`, `archetype`, `ke_j`, `v_tip_ms`,
  `m_eff_kg`, `r_arc_mm`, `material`; defaulted fields `assumptions`, `rpm`,
  `weapon_shape`, `weapon_mass_kg`, `plate_thickness_mm`, `weapon_od_mm`,
  `bar_width_mm` (None for a disc), `tooth_width_mm` / `tooth_depth_mm` /
  `tooth_length_mm` (the head across the strike, radial, along the travel),
  `opponent_mass_kg` (what the weapon's hub carries), `overrides` (the keys
  the user set) and `warnings` (e.g. over 300 mph).
- `Aim` — where the user put the strike, and whether they put it there.
  `from_dict()` / `to_dict()` — how it crosses the form and JSON boundaries;
  in v2 `case_cli.py` parses SETUP.json's `"aim"` with `from_dict()`.
- `Job` — one v1 submission, upload to captured payment.
  - `approved_document()` / `from_approved()` — the v1 Batch crossing.
  - `NOT_HANDED_OVER` — fields deliberately not crossed, with a reason each.

## Implementation

**`ImpactorSpec`'s weapon fields are defaulted** (added 2026-10-08, after the
v1 fields); only `impactor_spec` constructs a spec and it fills them all,
and `tooth` and `loadcase` read the tooth sizes and `opponent_mass_kg` from it
rather than recomputing them, so an override reaches the deck. Its zero
defaults are not meaningful values: a hand-built spec must set them.

**Enums are `StrEnum`** so members drop straight into JSON bodies (v1 also:
DynamoDB items and HTML form values) without `.value` noise, and a raw string
round trips through `WeightClass("12lb")`.

**Every v1 gate returned `GateResult`**, which is what made the runner a loop
rather than a case per gate.

**`CaseChoice` defaults to `BOTH`, and one impact is not a discount** (v1:
PLAN.md decision 63). Both was the product; choosing one narrowed it at the
same price, which is what the skip warning B3.5 emitted ("the other case only,
at the same price") had always told builders. What it did buy was real: v1's
`Settings.per_case_budget` split the element-cycle cap across the cases that
run, so a single case got the whole 2.028e10 rather than half — a bot refused
on cost with both may fit with one.

It crossed the Lambda-to-Batch boundary in `approved_document`, because B4
read the targets it produced and a job rebuilt without it would silently run
both.

**`MaterialSource` defaults to `DEFAULT`, and the direction matters.** A
producer that says nothing has not reviewed anything, so silence reads as
"nobody looked". The other default would let a report claim a material was
confirmed when it was not, which is precisely what v1's PLAN.md decision 42
asks the report to prevent. An older page or an older `assignments.json`
therefore understates what the builder did and never overstates it.

**The value is never read alone.** In v1 the two producers defaulted to
different things — the submit form to `tpu_shore95a` (`web/build.py`) and the
library to `steel_1018` (`cra.materials.DEFAULT_MATERIAL`) — so `DEFAULT` on
its own does not say what physics ran. The row carries `material` beside it,
and every message built from this names the material rather than attributing
it to a form.

**`GUESSED` has no producer in the tree.** `tools/partviewer` was the only
one — it inferred a material from a part name through
`cra.materials.guess_material` — and it was deleted on 2026-09-23. The member
stays because the value is in the data: v1's `corpus-roles.json` carries 37
guessed rows and v1's `cra.calibrate.roles.load_roles` refuses an unknown
source, so dropping it would make the committed table unreadable. The submit
form never had the inference and emitted only `CHOSEN` and `DEFAULT`; one
vocabulary serves both because the report says the same sentence whichever
produced the row. Neither `cra.materials` nor `cra.calibrate` was copied to v2.

**`PartAssignment` is deliberately not a field on `Job`.** `Job` was the
DynamoDB metadata row and v1's PLAN.md §9 keeps per-part data out of it; the
table was written to the job's working directory for B5 instead.

**`Aim.human_placed` is not decoration.** Automatic placement aims at the support
point of an inscribed ellipsoid, which across the corpus lands off the material
46 times in 58 — so a strike nobody confirmed is a strike that is probably
wrong, and the report has to be able to say which it was.

**`Aim.direction` runs from the target *outwards***, towards where the tooth
starts, so the tooth travels along `-direction`. Same convention as
`placement.Approach`, because it overwrites three of its fields rather than
replacing it.

**`NOT_HANDED_OVER` is the guard, and it is the interesting part.** A field that
exists on one side of a process boundary and silently does not cross was v1's
most repeated defect — `solid_index` dropped from B3's projection, then
`part_id` and `solid_index` dropped from the mesh worker's `Approach` rebuild,
then all four aim fields never persisted at all. Each was invisible because
the fallback worked. (v2 still has a crossing of this shape: `build_case`
passes `asdict(approach)` into `mesh_geometry` and `_rebuild_approach` reads
it back.)

**There is no free-text field on `Job`, and that is structural.** A `notes`
box existed until 2026-09-15 and was removed: nothing read it. v1's PLAN §5
layer 2 had the Haiku judge screening it, but `get_judge` was called from
exactly one place — `b1_strip`, at Tier 0 — which ran *before* the submit form
existed and screened STEP product names and header strings. The notes reached
no gate, no prompt and no report; they were collected, stored, and dropped. A
field that looks like it steers the analysis and does not is worse than no
field.

**`notified_at` is the only field written after the job is over.** It was the
stamp v1's `handlers/notify.py` set once SES had accepted the delivery mail,
and the guard it read before composing one — EventBridge delivers at least
once, and a Batch job can be re-driven. `None` means nobody has been told
anything about this job.

**`expires_at` is Unix epoch seconds as an `int`, not an ISO string like
`created_at`**, and the two differ for a reason rather than by accident.
DynamoDB's TTL acts only on a Number attribute and silently ignores every other
type, so a string here would leave the table configured, the attribute present,
and nothing ever deleted. It is also on `NOT_HANDED_OVER`: the retention tier
was decided by whether the row describes a transaction, which was settled at
submit, before the job was ever enqueued — so the Batch task neither read nor
extended it. See v1's PLAN.md §9 for the two windows.

## Assertions

- **Every field of `Job` is either in `approved_document()` or on
  `NOT_HANDED_OVER` with a reason.** Holds in v2 (checked by walking
  `dataclasses.fields(Job)`), but no v2 test enforces it — v1's
  `tests/test_run_job.py` / `tests/test_handlers.py` did, and were not copied.
- **`from_approved` is strict.** A job that cannot be rebuilt exactly must fail
  loudly before anything is meshed, rather than quietly becoming a different job
  with defaults in the gaps.
- **Both aims must survive the crossing.** Without them v1's B4 fell back to
  the automatic prefill, which v1's PLAN.md decisions 45 and 46 exist to
  forbid.
