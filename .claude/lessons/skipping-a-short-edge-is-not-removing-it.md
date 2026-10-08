# `Mesh.ToleranceEdgeLength` skips a short edge without closing the wire, so the face can no longer be meshed

gmsh's `Mesh.ToleranceEdgeLength` reads like the fix for a CAD micro-edge that
forces a needle element: "skip a model edge in mesh generation if its length is
less than the tolerance". It is a *discretization filter*, not a topology edit
— the edge leaves the mesh and stays in the model. The face's boundary wire is
then open at exactly that spot, and 2D meshing fails:

```
The 1D mesh seems not to be forming a closed loop (2 boundary nodes are considered once)
```

**The unmatched-node count is the fingerprint.** Measured on two corpus solids
at gmsh 4.15.0-git: the part with 2 micro-edges reports 2 boundary nodes, the
part with 40 reports 20. If that number tracks the count of edges under the
tolerance, this is the mechanism and no value of the tolerance will help —
0.01, 0.05 and 0.30 mm all failed identically.

The `Geometry.OCC*` healing family (`OCCFixSmallEdges`, `Geometry.Tolerance`)
is the other obvious reach and fails differently: the solid comes back as
surfaces and the tet mesh is empty. Same verdict, and the same one
`docs/history/meshing-and-solving-issues.md` recorded for `--heal`/`OCCSewFaces`
years of investigation earlier.

## How to act next time

Removing a short edge from a B-rep means **extending its neighbours to meet**
— a wire repair. `ShapeFix_Wireframe.FixSmallEdges` is the operation that
attempts it, and `docs/history/geometry-simplifier.md` measured it at ×0.972
on the same solid, so the cheap-looking gmsh option and the expensive OCC path
are both closed for this class. Check whether the short edge is *interior to a
wire that survives without it* before reaching for the tolerance at all.

## The general form

An option whose documentation says "skip" is not doing what an option that
says "remove" would do. The half of the model the filter does not touch is
where the failure lands — here, the topology that still expects the edge to be
there. See [[a-fake-must-not-be-more-permissive]] for the same shape in a
different place: the cheap stand-in that models only half the contract.
