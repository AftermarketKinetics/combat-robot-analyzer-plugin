# setup_edit.py

## Function
Changes a case's setup the way the web app's `update_setup` tool does: only
the given keys change, values are checked against the model's parts and the
material library, and nothing is saved when any check fails. Also lists the
material library.

## Interface
- `update(case, patch) -> {setup, missing, weapon?}` — keys: `target_id`, `materials` (`null` unsets), `default_material`, `impactor` (merged; `null` clears an override), `end_time` (`"auto"` or 1e-5..5e-4), `up_axis`, `mesh_size` (0.5..5), `include`, `exclude`, `standins`, `exclude_span`, `aim` (`{point, direction}`, stored with `human_placed: true`), `clear_aim`. Raises CaseError on an unknown key or a bad value.
- `weapon_card(case, impactor) -> dict | None` — the resolved weapon from `case_cli.py weapon` (writes `WEAPON_IN`), or None without a weight class.
- `materials_table() -> list[dict]`, `library() -> dict`, constants `END_TIME_RANGE`, `MESH_SIZE_RANGE`, `UP_AXES`, `KEYS`, `WEAPON_IN`.

## Implementation
- Changing `target_id` clears the aim: an aim belongs to the part it was placed on.
- The weapon check runs last, after every other check, because it is the slow one (a subprocess); an impactor it rejects raises before `save`.
- `aim` exists here because there is no 3D viewer to click in: Claude sets it from a point the user names.

## Assertions
- [ ] Nothing is saved when any check fails, the weapon check included.
- [ ] Changing the target clears the aim.
