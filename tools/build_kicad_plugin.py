#!/usr/bin/env python3
"""Build a pure-Python KiCad PCM archive (no native toolchain required)."""

import argparse
import json
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(destination=None):
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    destination = destination or ROOT / "dist" / f"kicad-multichannel-{version}-kicad-pcm.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    metadata = json.loads((ROOT / "kicad_plugin/metadata.template.json").read_text())
    metadata["versions"] = [{"version": version, "status": "testing",
                             "kicad_version": "10.0", "runtime": "ipc",
                             "platforms": ["linux", "windows", "macos"]}]
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("metadata.json", json.dumps(metadata, indent=2) + "\n")
        for path in sorted((ROOT / "multichannel_layout").rglob("*.py")):
            archive.write(path, "plugins/" + path.relative_to(ROOT).as_posix())
        for path in sorted((ROOT / "kicad_plugin").rglob("*")):
            if not path.is_file() or path.name == "metadata.template.json" or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(ROOT / "kicad_plugin").as_posix()
            archive.write(path, relative if relative.startswith("resources/") else "plugins/" + relative)
        archive.write(ROOT / "LICENSE", "plugins/LICENSE")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    print(build(parser.parse_args().output))
