# The one nixpkgs pin for v2: shell.nix and the sandbox image both import this,
# so the dev shell and the image solve with the same gmsh, python and libs.
#
# nixos-26.05 at rev 70cc4559 — the same rev v1's flake.lock locked, so the
# OpenRadioss derivation (nix/openradioss.nix) is known to patchelf cleanly
# against it. Bump by taking a new rev and re-running
#   nix-prefetch-url --unpack https://github.com/NixOS/nixpkgs/archive/<rev>.tar.gz
import (builtins.fetchTarball {
  url = "https://github.com/NixOS/nixpkgs/archive/70cc4559b10a6062b05ff1af17e0add065ccaed9.tar.gz";
  sha256 = "1hm4wf9dns700453kw87v8fc0ln13wiqh5bb9fq80g1r83r79v2n";
}) { }
