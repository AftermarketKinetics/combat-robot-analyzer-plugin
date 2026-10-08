# build_deck.py

## Function
Stage 2: turn a gmsh mesh plus a simulation spec YAML into an OpenRadioss
starter/engine deck pair. The agent edits the spec; this file is the only
thing that writes deck text.

Copied from the openradioss-sim plugin (`~/code/impact-simulator-skill`,
commit `f8e97bd`); it is a copy, not a submodule, so fixes must be ported by
hand in either direction. **v2 has changed it** (2026-10-08 impact-physics
rework; the plugin is still at `f8e97bd` without them). Every change is additive — a spec that uses none
of the new keys produces the same deck as the plugin's copy — so the port back
is the following five pieces:

1. `derived_failure(m)` and the `erosion_from_eps_max` spec key (erosion from a
   material's `eps_max`).
2. `inivel_axis_cards(iv_id, iv, grnod_id)` and the `axis` branch of
   `initial_velocity` (rigid rotation about an arbitrary axis).
3. `rbody_cards(rb_id, rb, main_node, grnod_id)` and the `rigid_bodies` key.
4. `extra_nodes` — free-standing nodes written after the mesh nodes.
5. `with_nodes` on a `sets` entry — adds named extra nodes to that `/GRNOD`.

## Interface
- CLI: `build_deck.py MESH.msh -c SPEC.yaml [-o OUT_DIR] [-n NAME] [--json PATH]`.
- Writes `<name>_0000.rad` (starter) and `<name>_0001.rad` (engine) in OUT_DIR.
- Stdout: JSON summary incl. estimated stable timestep and contact notes.
- Spec keys read: `name, units, parts[match, material], materials{…}, sets,
  boundary_conditions, initial_velocity, contact, control, output, prescribed`
  (see `fixtures/projectile_impact.yaml`), plus v2's:
  - `erosion_from_eps_max: true` — every part whose material has no explicit
    `failure:` block, has `eps_max > 0` and is LAW2 (`johnson_cook` /
    `plas_johns` / `law2`) gets `/FAIL/JOHNSON` with `D1 = eps_max` (other D's
    0). Elastic and hyperelastic cards get none.
  - `extra_nodes: [{name, xyz}]` — numbered `len(mesh nodes) + 1 ...` in list
    order, after the renumbered mesh nodes.
  - `sets.<name>.with_nodes: [extra node name, ...]` — appended to that set's
    `/GRNOD/NODE`; an unknown name exits.
  - `rigid_bodies: [{name, set, main, mass, inertia}]` — one `/RBODY` each
    (radioss2021 layout), `main` must name an `extra_nodes` entry (else exit);
    `mass` and `inertia` (`[Jxx, Jyy, Jzz, Jxy, Jyz, Jxz]`, global axes, about
    the main node) are added at the main node with `ICoG = 1`.
  - `initial_velocity[].axis: {origin, direction, omega}` (optional `vector`
    in global mm/s, optional `start_time`) — `/FRAME/FIX` + `/INIVEL/AXIS`
    with `DIR = Y`, both with the entry's index as id.
- Materials must be defined inline under `materials:`; entries are copied from `materials.yaml`.

## Implementation
- Card layouts follow OpenRadioss's `hm_cfg_files/config/CFG/radioss2025`
  definitions (individual cards cite their own cfg version).
- **LAW2's own `EPS_p_max` does not delete solid elements in these decks** — a
  4130 shell reached 0.51 strain against `eps_max` 0.28, intact — so erosion
  needs a `/FAIL` card. Johnson-Cook failure with only `D1 = eps_max` deletes
  an element when its equivalent plastic strain reaches `eps_max` whatever the
  stress state, which is what `eps_max` means. `/FAIL/TENSSTRAIN` was tried
  first and missed material crushed in compression under the tooth (survivors
  at 6.5 plastic strain on the fixture spike). An explicit `failure:` block on
  a material always wins over the derived one.
- **`/FRAME/FIX`'s two vector lines are the frame's local Y and Z axes** (the
  cfg names them globalyaxis/globalzaxis; X = Y × Z), so the spin axis goes on
  the first (Y) line, a perpendicular on the second, and `/INIVEL/AXIS`
  rotates about `DIR = Y`. Putting the axis first with `DIR = X` spun the body
  about a perpendicular axis (caught by the fixture spike). A translational
  `vector` is converted from global axes into the frame's. Cards:
  `radioss110/SYSTEM/frame_fix.cfg`, `radioss2025/LOADS/inivel_axis.cfg`.
- **`/RBODY` with `ICoG = 1`** adds `mass`/`inertia` at the main node rather
  than recomputing the centre of gravity — which is how a body that is not
  meshed (the rest of a weapon, the rest of a robot) carries its mass. The
  main node is an `extra_nodes` entry because it must sit where no mesh node
  is (a spin axis, a centre of mass). Card: `radioss2021/RBODY/rbody.cfg`.
- `inivel_axis_cards` and `rbody_cards` sit under the file's "timestep
  estimate" section banner, not a section of their own (placement only).
- 1545 lines, deliberately not split: it is reused from the plugin.
- Summary notes for v2 decks: the "no boundary conditions defined — the model
  is unrestrained" note is skipped when the spec has `rigid_bodies` (a free
  model is then intended), and the `failure` block lists derived
  (`erosion_from_eps_max`) entries as well as explicit `failure:` blocks.
- The stable timestep estimate still includes rigid parts' elements.

## Assertions
- [ ] Every material a part references must exist under `materials:` or the build fails loudly.
- [ ] Output is deterministic for the same mesh and spec.
- [ ] A spec using none of `erosion_from_eps_max`, `extra_nodes`, `with_nodes`,
      `rigid_bodies` or `initial_velocity[].axis` produces exactly the plugin
      copy's deck (the v2 changes are additive, so a port is a merge).
- [ ] `/INIVEL/AXIS` uses `DIR = Y` with the spin axis on `/FRAME/FIX`'s first
      vector line; changing one without the other spins about the wrong axis.
- [ ] A rigid body's `main` and every `with_nodes` name resolve to an
      `extra_nodes` entry, or the build exits.
- [ ] The v2 cards are tested in `sim/tests/test_deck_cards.py`: spin axis on
      the frame's Y line with `DIR = Y`, translation converted into the frame,
      `/RBODY` mass/inertia at the main node with `ICoG = 1`, and `D1 = eps_max`
      derived only for LAW2 cards with an `eps_max`. `sim/tests/test_loadcase.py`
      checks the spec `loadcase.deck_spec` writes.
