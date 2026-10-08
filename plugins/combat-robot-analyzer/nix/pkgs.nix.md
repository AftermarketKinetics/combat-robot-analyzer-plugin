# pkgs.nix

## Function
The single nixpkgs pin for v2. Every nix expression in v2 imports nixpkgs
through this file, so the dev shell and the sandbox image always agree.

## Interface
- Evaluates to an instantiated nixpkgs set (`import ./nix/pkgs.nix`).
- Imported by `shell.nix` and `sandbox/image.nix`.

## Implementation
- Pinned to nixos-26.05 rev `70cc4559`, the rev v1's `flake.lock` locked, because
  `nix/openradioss.nix` is known to autoPatchelf cleanly against it.
- `fetchTarball` with an explicit sha256, never a channel.

## Assertions
- [ ] There is exactly one nixpkgs pin in v2; nothing else calls `fetchTarball` for nixpkgs or uses `<nixpkgs>`.
- [ ] The sha256 matches the rev (`nix-prefetch-url --unpack` of the archive URL).
