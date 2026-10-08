# playback.py

## Function
Stage 4 (viewer): packs a solved run's VTK animation frames into one compact
binary plus a JSON header that the browser animates as the impact playback.
New in v2 — not copied from the openradioss-sim plugin; it reuses that
plugin's `extract_damage.py` frame reader.

## Interface
- CLI: `playback.py VTK_DIR -o OUT_DIR [--scale-parts ID,ID,...] [-q]` →
  `OUT_DIR/playback.json` + `OUT_DIR/playback.bin`.
- `--scale-parts` — deck part ids whose stress sets the colour scale (default all); leave the impactor out or it saturates the scale.
- `playback.json`: `{frames, times[], nverts, nfaces, parts[] (sorted PART_IDs), lo[3], span[3], stress_max, stress_units: "MPa"}`.
- `playback.bin`, little-endian, in order: `faces uint32[nfaces*3]`, `face_part uint8[nfaces]` (index into `parts`), then per frame `verts uint16[nverts*3]` (x,y,z **interleaved**, over `lo`/`span`), `stress uint8[nfaces]` (0..255 over 0..`stress_max`, clipped; `stress_max` is the peak over `--scale-parts`, or every part when the flag is omitted), `alive uint8[nfaces]`.
- Stdout: the header with `times` replaced by its count, plus `bytes` (bin size). Exit 1 with `{"error"}` if there are no frames or the mesh is not all-tetrahedral.
- `boundary_faces(tets) -> (faces, owner)` — faces belonging to exactly one tet, and that tet's index.

## Implementation
- The VTK frames are ASCII and large (21 frames of an 823k-element run are 1.2 GB); the browser needs only the outer surface. Boundary faces are found once, on frame 0 (sorted-vertex keys counted with `np.unique`), and each frame contributes only those vertices' positions plus per-face stress and alive flag.
- Faces of eroded elements are hidden, but the interior faces they would expose are not added: the surface is fixed at frame 0. Fine for showing where material went; not a substitute for the wear map.
- `lo`/`span` cover every frame's points, so deformation never clips; `span` floors at 1e-9.
- Unlike `report/geometry.json` (three coordinate blocks), vertex coordinates here are interleaved per vertex.
- Only `stress` and `alive` are read from later frames (`want=`), keeping each frame read cheap.
- Measured on the real 823k-element session: 46 s; `playback.bin` 36.7 MB (155k verts, 311k faces, 21 frames).

## Assertions
- [ ] The byte layout matches `web/src/viewer/playback.ts` exactly (order, widths, interleaving).
- [ ] `stress_max` is taken only over `--scale-parts` faces when given, and is never 0.
- [ ] Stdout carries exactly one JSON document.
