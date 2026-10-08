# OpenRadioss, from the upstream prebuilt linux64 release.
#
# Building from source needs gfortran + a bespoke build script and takes the
# better part of an hour; the official release tarball is the same compiler
# output and patchelfs cleanly, so that is what we use.
{ lib
, stdenv
, fetchurl
, unzip
, autoPatchelfHook
, makeWrapper
, openmpi
, util-linux
, libxcrypt-legacy
}:

stdenv.mkDerivation rec {
  pname = "openradioss";
  version = "20260728";

  src = fetchurl {
    url = "https://github.com/OpenRadioss/OpenRadioss/releases/download/latest-${version}/OpenRadioss_linux64.zip";
    hash = "sha256-WY7XspBae6zI0XgUcMJQrHnXVYw5upYnaO3zZEZE/jM=";
  };

  nativeBuildInputs = [ unzip autoPatchelfHook makeWrapper ];

  buildInputs = [
    stdenv.cc.cc.lib # libstdc++, libgomp, libquadmath
    openmpi # the _ompi engine variants
    util-linux # libuuid, for the bundled libapr
    libxcrypt-legacy # libcrypt.so.1, likewise
  ];

  sourceRoot = "OpenRadioss";

  installPhase = ''
    runHook preInstall

    # The solver resolves hm_cfg_files and extlib relative to OPENRADIOSS_PATH,
    # so the tree has to stay intact rather than being split across bin/lib.
    mkdir -p $out/share
    cp -r . $out/share/OpenRadioss
    find $out/share/OpenRadioss -name '*:Zone.Identifier' -delete

    root=$out/share/OpenRadioss
    mkdir -p $out/bin
    for exe in $root/exec/*; do
      name=$(basename "$exe")
      chmod +x "$exe"
      makeWrapper "$exe" "$out/bin/$name" \
        --set-default OPENRADIOSS_PATH "$root" \
        --set-default RAD_CFG_PATH "$root/hm_cfg_files" \
        --set-default RAD_H3D_PATH "$root/extlib/h3d/lib/linux64" \
        --prefix LD_LIBRARY_PATH : "$root/extlib/hm_reader/linux64:$root/extlib/h3d/lib/linux64"
    done

    runHook postInstall
  '';

  meta = with lib; {
    description = "Open-source explicit finite element solver (Altair Radioss)";
    homepage = "https://www.openradioss.org/";
    license = licenses.agpl3Only;
    platforms = [ "x86_64-linux" ];
  };
}
