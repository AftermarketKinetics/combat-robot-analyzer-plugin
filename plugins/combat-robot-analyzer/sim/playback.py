#!/usr/bin/env python3
"""Pack a solved run's animation frames into one compact file for the browser.

    playback.py BUILD/run/vtk -o OUT_DIR        -> OUT_DIR/playback.json + playback.bin

The VTK frames are ASCII and large (21 frames of an 823k-element run are
1.2 GB); the browser needs only the outer surface. So the boundary faces of the
tetrahedral mesh are found once, on the first frame, and every frame then
contributes just those vertices' positions and a per-face von Mises stress and
alive flag.

playback.bin layout, little-endian, in this order:
    faces      uint32[nfaces * 3]   vertex indices, wound outward
    face_part  uint8 [nfaces]       index into header "parts"
    per frame:
      verts    uint16[nverts * 3]   x,y,z interleaved, quantised over header lo/span
      stress   uint8 [nfaces]       0..255 over 0..header stress_max (MPa), clipped;
                                    stress_max is the peak over --scale-parts
                                    (every part when the flag is omitted)
      alive    uint8 [nfaces]       1 while the face's element has not eroded

Faces of eroded elements are hidden, but the interior faces they would expose
are not added: the surface is fixed at frame 0. Fine for showing where material
went; not a substitute for the wear map.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

from extract_damage import CELL_FACES, VTK_TETRA, frame_files, read_frame


def boundary_faces(tets: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Faces that belong to exactly one tetrahedron, and which one."""
    faces = np.concatenate([tets[:, list(f)] for f in CELL_FACES[VTK_TETRA]])
    owner = np.tile(np.arange(len(tets)), len(CELL_FACES[VTK_TETRA]))
    key = np.sort(faces, axis=1)
    _, inverse, counts = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    once = counts[inverse.ravel()] == 1
    return faces[once], owner[once]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="playback.py", description=__doc__.splitlines()[0])
    p.add_argument("vtk_dir")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--scale-parts", help="comma-separated part ids whose stress sets the colour "
                   "scale (default: all). Leave the impactor out, or it saturates the scale.")
    p.add_argument("-q", "--quiet", action="store_true")
    args = p.parse_args(argv)

    files = frame_files(args.vtk_dir)
    if not files:
        print(json.dumps({"error": f"no VTK frames in {args.vtk_dir}"}))
        return 1
    first = read_frame(files[0], geometry=True)
    stress_name, alive_name = first["fields"]["stress"], first["fields"]["alive"]
    if not isinstance(first["cells"], np.ndarray) or not (first["cell_types"] == VTK_TETRA).all():
        print(json.dumps({"error": "playback supports all-tetrahedral meshes only"}))
        return 1

    faces, owner = boundary_faces(first["cells"])
    used, faces = np.unique(faces, return_inverse=True)
    faces = faces.reshape(-1, 3).astype(np.uint32)
    part_ids = first["PART_ID"].astype(np.int64)[owner]
    parts = sorted(set(part_ids.tolist()))
    face_part = np.searchsorted(parts, part_ids).astype(np.uint8)

    frames = []
    for i, path in enumerate(files):
        f = first if i == 0 else read_frame(path, want={stress_name, alive_name}, geometry=True)
        stress = f.get(stress_name, np.zeros(len(first["cells"])))[owner]
        alive = f.get(alive_name, np.ones(len(first["cells"])))[owner] != 0
        frames.append((f["time"], f["points"][used].astype(np.float32), stress.astype(np.float32), alive))
        if not args.quiet:
            print(f"playback: frame {i + 1}/{len(files)}", file=sys.stderr)

    allpts = np.concatenate([fr[1] for fr in frames])
    lo, hi = allpts.min(axis=0), allpts.max(axis=0)
    span = np.maximum(hi - lo, 1e-9)
    scale_mask = np.ones(len(part_ids), dtype=bool)
    if args.scale_parts:
        scale_mask = np.isin(part_ids, [int(x) for x in args.scale_parts.split(",")])
    stress_max = float(max(fr[2][scale_mask].max(initial=0.0) for fr in frames)) or 1.0

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "playback.bin"), "wb") as out:
        out.write(faces.astype("<u4").tobytes())
        out.write(face_part.tobytes())
        for _, pts, stress, alive in frames:
            q = np.clip(np.rint((pts - lo) / span * 65535), 0, 65535).astype("<u2")
            out.write(q.tobytes())
            out.write(np.clip(np.rint(stress / stress_max * 255), 0, 255).astype(np.uint8).tobytes())
            out.write(alive.astype(np.uint8).tobytes())

    header = {
        "frames": len(frames),
        "times": [fr[0] for fr in frames],
        "nverts": int(len(used)),
        "nfaces": int(len(faces)),
        "parts": parts,
        "lo": lo.tolist(),
        "span": span.tolist(),
        "stress_max": stress_max,
        "stress_units": "MPa",
    }
    with open(os.path.join(args.out, "playback.json"), "w") as fh:
        json.dump(header, fh)
    print(json.dumps({**header, "times": len(header["times"]),
                      "bytes": os.path.getsize(os.path.join(args.out, "playback.bin"))}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
