# The plugin's runtime: gmsh, OpenRadioss, mmg and the sim Python
# (nix/sim-env.nix), entered by bin/robot-impact.
#
# nix owns everything here; the sim's Python libraries (numpy, meshio, pyyaml,
# matplotlib, pythonocc-core, pytest) come from nixpkgs, pinned in nix/pkgs.nix.
# There is no lockfile and no pip.
{ pkgs ? import ./nix/pkgs.nix }:

let
  sim = import ./nix/sim-env.nix { inherit pkgs; };
in
pkgs.mkShell {
  name = "combat-robot-analyzer";
  packages = sim.tools ++ [ sim.pythonDev ];
  shellHook = ''
    export PYTHONPATH="${toString ./.}:${toString ./sim}:${sim.gmshPythonPath}''${PYTHONPATH:+:$PYTHONPATH}"
    # cad-step's scripts must not re-enter a nix-shell of their own
    export STEP_OCC_NO_NIX=1
    # bin/robot-impact runs commands directly once inside this shell
    export CRA_IN_SHELL=1
  '';
}
