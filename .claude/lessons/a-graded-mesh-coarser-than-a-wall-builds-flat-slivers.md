# A graded mesh coarser than a wall builds flat slivers no optimiser can fix

When a graded mesh's far element size is larger than a thin wall, HXT puts no node inside the wall and joins its two faces with flat tets tens of mm long.

- Seen 2026-10-08 on inertial-v6's 3.8 mm lip with far = 6 mm: 203 tets with gamma 0.0003–0.001, edges 40–60 mm, 45–60 mm from the strike. All four nodes of 199 of the 203 lay on the surface.
- They looked harmless: the stable timestep was normal, the build passed the starter, and the cost estimate was fine. But they reached eps_max at almost no load and were eroded at ~7 µs. Their neighbours inverted, the solver hit a negative-stiffness error, and every energy went NaN at 8.5 µs. The solver still reported "NORMAL TERMINATION".
- Running gmsh's default optimiser or Netgen *after* meshing did not remove them. There is no interior node to move, so they cannot be improved. Switching to Delaunay (algo 1) was no better.
- What works is a smaller far size, below the wall thickness (3 mm was clean there). So check gamma after meshing (`loadcase._slivers`) and re-mesh, refining only the part that had slivers.
- The timestep, min edge and starter checks do not catch this. Check element quality (gamma) directly.
