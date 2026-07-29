{
  description = "Development shell for the KiCad Multichannel Layout plugin";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils, ... }:
    flake-utils.lib.eachSystem [ "x86_64-linux" ] (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          config.allowUnfree = true;
        };

        python = pkgs.python3;

        # Shared libraries that pip's PySide6 wheel expects the host system to
        # provide on NixOS (the wheel ships Qt/PySide libs, but not the host
        # X11/GL/fontconfig etc.).
        runtimeLibs = with pkgs; [
          libglvnd
          glib
          freetype
          fontconfig
          libxkbcommon
          xorg.libX11
          xorg.libXext
          xorg.libXrender
          xorg.libxcb
          xorg.libXau
          xorg.libXdmcp
          xorg.xcbutil
          xorg.xcbutilimage
          xorg.xcbutilkeysyms
          xorg.xcbutilrenderutil
          xorg.xcbutilwm
          xorg.xcbutilcursor
          dbus
          expat
          zlib
          gcc.cc.lib
          zstd
          harfbuzz
          libpng
          pcre2
          util-linux
          libselinux
          libffi
          bzip2
          systemdLibs
          wayland
        ];
      in
      {
        devShells.default = pkgs.mkShell {
          name = "kicad-multichannel-layout";
          venvDir = ".venv";

          buildInputs = [
            python
            pkgs.python3Packages.venvShellHook
          ];

          postVenvCreation = ''
            pip install -r requirements.txt
          '';

          shellHook = ''
            venvShellHook

            export PYTHONPATH="$PWD:''${PYTHONPATH:-}"
            export QT_QPA_PLATFORM=xcb
            export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath runtimeLibs}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

            echo "KiCad Multichannel Layout dev shell"
            echo "  Python: $(python --version)"
            echo "  Venv:   $venvDir"
            echo ""
            echo "List channels:"
            echo "  python multichannel_layout.py --list"
            echo ""
            echo "Dry-run match + offset votes:"
            echo "  python multichannel_layout.py CH1 CH2 CH3 CH4 --dry-run"
            echo ""
            echo "Actually copy placement:"
            echo "  python multichannel_layout.py CH1 CH2 CH3 CH4"
            echo ""
            echo "KiCad sets KICAD_API_SOCKET and KICAD_API_TOKEN for its child"
            echo "processes. For a fully external run, copy those from a plugin log"
            echo "or launch this terminal from within KiCad."
          '';
        };
      }
    );
}
