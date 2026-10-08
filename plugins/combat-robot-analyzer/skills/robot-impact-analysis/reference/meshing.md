# Meshing advice

What `build` does to mesh a robot, what goes wrong, and which setup changes
fix it. The numbers come from the 12 lb robot `inertial-v6`, whose struck part
is a thin 4130 steel shell. It was meshed and solved repeatedly while this
pipeline was built.

## What build does

- **Graded mesh on the struck part.** Elements are `mesh_size` (default
  1.5 mm) within 20 mm of the strike, or within two tooth widths if that is
  larger. They grow by 0.15 mm per mm of distance, up to 4 × `mesh_size`.
  The shell meshed uniformly at 1.5 mm was about 828k elements; graded, it was
  about 125k. The solve cost scales with that count.
- **Mesher: HXT, with Frontal as fallback.** HXT, gmsh's parallel 3D
  mesher, meshed the shell in 4.6 s with cleaner elements. The older Frontal
  mesher took 771 s. Some multi-body assemblies fail HXT's input check
  ("a vertex lies in a segment"). The build then retries with Frontal
  automatically, which is slower but works.
- **Netgen's optimiser is off on graded meshes**, because it crashed gmsh on
  them.
- **Needle-element check.** After meshing, every element's shape quality is
  measured. If any are needles (gamma < 0.02), the build re-meshes up to four
  times, refining only the part that had them:
  - on the struck part, the far element size halves;
  - on a stand-in block, its element size shrinks by 30%.
  After four attempts, or when nothing is left to refine, the build is
  refused. Each re-mesh adds a build warning.
- **Stand-in blocks.** Neighbouring parts that carry the load into the chassis
  are meshed coarsely, at 4 × `mesh_size`, with ordinary unstructured
  elements. A regular-grid option exists in the code but is off.

## Why needle elements matter

A wall thinner than the local element size gets no node inside it. The mesher
then joins the wall's two faces with flat elements tens of mm long.
inertial-v6's 3.8 mm lip at a 6 mm far size produced 203 of them, 45–60 mm
from the strike.

**None of the usual checks catch them.** The timestep was normal, the
solver's starter accepted the deck, and the cost estimate was fine. But they
hit their failure strain at almost no load and were deleted at about 7 µs.
Their neighbours turned inside out, and every energy went NaN at 8.5 µs. The
solver still reported "normal termination".

Running gmsh's or Netgen's optimiser after meshing does not remove them,
because there is no interior node to move. What works is an element size below
the wall thickness. The same case re-meshed at a 3 mm far size was clean:
160k elements, and the 500 µs solve finished with a −2.5% energy error.

## Setup changes that help

| Problem | Try |
|---|---|
| Solve estimate over budget | Raise `mesh_size` to 2–2.5 mm (the element count falls steeply, between the square and the cube of the size ratio); set a fixed `end_time` shorter than the auto estimate; `exclude` neighbours that don't carry load to the strike. |
| Build refused for needle elements | Lower `mesh_size`, so the whole grading scales down and thin walls get interior nodes; or `exclude` the part named, if it is a neighbour. |
| Build crashed (signal 6 or 11) | gmsh crashed fusing the stand-in blocks. Retry with `standins: false`; load paths into the rest of the robot are then lost, so say so. |
| Struck wall thinner than about 2 × `mesh_size` | Lower `mesh_size` so at least two elements span the wall at the strike. Otherwise the bending and through-thickness failure are poorly resolved even when the run is stable. |
| Many tiny features (fillets, text, holes) near the strike | They force small elements and a small timestep. Prefer simplified CAD of the struck part; aim away from them if the user agrees. |
| Model in inches or with odd units | `init` reports the model's units and size; check the overall dimensions look right (mm) before setting anything else. |

## Reading a build's numbers

- **`elements`**: the cost driver together with the timestep. 50–200k is
  typical for a 12 lb robot's struck region.
- **Timestep**: the solve estimate uses the solver starter's own per-node
  timestep, not a guess from element sizes. On graded meshes a guess from
  element sizes was off by orders of magnitude.
- **`warnings` mentioning re-meshing**: expected on thin walls. Pass them on;
  they explain a higher element count.
- **After solving**: elements deleted in the first few microseconds, far from
  the strike, are a mesh problem, not damage. Results with NaN energies cannot
  be used at all. See [troubleshooting.md](troubleshooting.md).
