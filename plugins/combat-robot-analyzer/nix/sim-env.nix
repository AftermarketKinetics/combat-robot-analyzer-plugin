# Everything the sim code in sim/ needs at runtime: gmsh, OpenRadioss, mmg and
# a Python with the scripts' libraries (pythonocc-core for the cad-step
# report). Shared by shell.nix (development) and sandbox/image.nix (what
# users' tools actually run in), so code that works in the shell works in the
# sandbox.
#
# The sandbox's Python libraries come from nix, not a lockfile: the image is a
# nix build and has no package manager inside it. The *service's* libraries
# (FastAPI, anthropic) are a different Python and live in pyproject.toml/uv.lock.
{ pkgs }:

rec {
  pythonLibs = ps: with ps; [
    numpy
    meshio
    pyyaml
    matplotlib
    pythonocc-core
  ];

  # What the sandbox runs.
  python = pkgs.python3.withPackages pythonLibs;

  # The same Python plus the test runner, for the dev shell only.
  pythonDev = pkgs.python3.withPackages (ps: pythonLibs ps ++ [ ps.pytest ]);

  openradioss = pkgs.callPackage ./openradioss.nix { };

  # gmsh ships its Python API as a bare gmsh.py beside libgmsh.so rather than
  # as a site-package, so callers must put this on PYTHONPATH.
  gmshPythonPath = "${pkgs.gmsh}/lib";

  # Everything except the Python, which each consumer picks.
  tools = [ pkgs.gmsh pkgs.mmg openradioss pkgs.bash pkgs.coreutils pkgs.gnugrep pkgs.gawk ];
}
