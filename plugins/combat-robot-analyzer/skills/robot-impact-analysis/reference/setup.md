# Setup reference

`robot-impact set CASE 'JSON'` changes only the keys you pass. Every value is
checked against the model's parts and the material library, and nothing is
saved when any check fails. The full setup is in `CASE/setup.json`;
`robot-impact show CASE` prints it with what is still missing.

| Key | Value |
|---|---|
| `target_id` | the struck part's id, e.g. `"p035"`. Changing it clears `aim`. |
| `materials` | `{part_id: material_key}`; `null` unsets one. Keys from `robot-impact materials`. |
| `default_material` | material for every part not named in `materials`. |
| `impactor` | the opponent's weapon (below). |
| `end_time` | contact time in seconds, 1e-5 to 5e-4, or `"auto"` (default: until the hit is over). |
| `up_axis` | `auto` (the thinnest dimension), `x`, `y`, `z`, `-x`, `-y` or `-z`. Sets a horizontal spinner's axis and a drum's plane. |
| `mesh_size` | element size at the strike, mm, 0.5 to 5 (default 1.5; coarser with distance). |
| `aim` | `{"point": [x,y,z], "direction": [x,y,z]}`: a point on the target's surface and the outward normal there, mm. `null` or `clear_aim: true` returns to the automatic strike point. |
| `include` | explicit neighbour parts to carry as stand-ins (`null` = the suggested radius scope). |
| `exclude` | parts to drop from the scope. |
| `standins` | `false` meshes the target alone (use after a build crashed). |
| `exclude_span` | avoid aiming across the full span of the part (default true). |

## The weapon (`impactor`)

Three choices pick the presets:

- `weight_class`: `1lb`, `3lb`, `12lb` or `30lb` (the opponent's class).
- `energy_level`: `typical` or `high` (the preset RPM).
- `archetype`: `horizontal` (spins about the up axis) or `vertical_drum` (spins about a horizontal axis across the attack). It also picks the default shape.

By default the weapon is a quarter of the class weight in steel: a 6:1 bar for
horizontal, a solid disc for vertical. Its plate is 4 / 10 / 12 / 20 mm thick
(1 / 3 / 12 / 30 lb), and it spins at this preset RPM (typical / high):

| Class | Bar | Disc |
|---|---|---|
| 1 lb | 12,000 / 16,000 | 20,000 / 25,000 |
| 3 lb | 10,000 / 14,000 | 18,000 / 24,000 |
| 12 lb | 6,000 / 8,000 | 10,000 / 14,000 |
| 30 lb | 4,000 / 5,500 | 7,000 / 9,500 |

The RPM preset follows the shape, so a horizontal weapon set to a disc gets
the disc RPM. Tip speed and energy follow from the RPM. The tooth is the end
of the weapon: plate thickness across the strike, plate thickness deep, and
the bar's width (or 0.3 × a disc's diameter) along its travel.

**Any number can be overridden** inside `impactor`; `null` restores the preset:

| Key | Notes |
|---|---|
| `weapon_shape` | `bar` or `disc` |
| `weapon_mass_kg`, `plate_thickness_mm`, `weapon_od_mm`, `bar_width_mm` | Tied together by steel's density, so leave at least one unset and it is derived. `bar_width_mm` applies to bars only. |
| `rpm`, `tip_speed_ms`, `energy_j` | Give at most one; the other two are derived from the weapon's mass. |
| `tooth_width_mm`, `tooth_depth_mm`, `tooth_length_mm` | Across the strike (along the spin axis), radial depth, and along the travel. |
| `opponent_mass_kg` | The robot carrying the weapon (default: the class limit). It must outweigh the weapon. |

Example: a 12 lb drum, 250 mm across, at 9,000 RPM:

```bash
robot-impact set CASE '{"impactor": {"weight_class": "12lb", "archetype": "vertical_drum", "weapon_od_mm": 250, "rpm": 9000}}'
```

The result's `weapon` lists every resolved number and which are `custom`. A
tip speed over 300 mph (134 m/s) comes back as a warning. That is allowed, but
confirm with the user before building.
