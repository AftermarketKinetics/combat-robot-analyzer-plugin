# Dev shell for the whole repo: the combat-robot-analyzer plugin's runtime
# (gmsh, OpenRadioss, the sim Python with pytest). nix owns all of it; nixpkgs
# is pinned in plugins/combat-robot-analyzer/nix/pkgs.nix. The cad-step plugin
# brings its own shell (plugins/cad-step/scripts re-enter nix as needed).
import ./plugins/combat-robot-analyzer/shell.nix { }
