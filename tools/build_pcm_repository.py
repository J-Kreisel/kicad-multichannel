#!/usr/bin/env python3
"""Create a subscribable PCM feed from the release archive."""

import argparse
import hashlib
import json
import shutil
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_BASE = "https://raw.githubusercontent.com/J-Kreisel/kicad-multichannel/pcm"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_repository(archives, output):
    packages = sorted(archives.glob("*-kicad-pcm.zip"))
    if len(packages) != 1:
        raise ValueError("Expected exactly one PCM archive")
    archive = packages[0]
    with zipfile.ZipFile(archive) as package:
        metadata = json.loads(package.read("metadata.json"))
        install_size = sum(item.file_size for item in package.infolist())
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(archive, output / archive.name)
    metadata["versions"][0].update({
        "download_url": f"{RAW_BASE}/{archive.name}",
        "download_sha256": sha256(archive),
        "download_size": archive.stat().st_size,
        "install_size": install_size,
    })
    packages_path = output / "packages.json"
    packages_path.write_text(json.dumps({"packages": [metadata]}, indent=2) + "\n")
    updated = int(time.time())
    repository = {
        "$schema": "https://go.kicad.org/pcm/schemas/v2", "schema_version": 2,
        "name": "KiCad Multichannel Layout",
        "maintainer": {"name": "Johannes", "contact": {"web": "https://github.com/J-Kreisel"}},
        "packages": {"url": f"{RAW_BASE}/packages.json", "sha256": sha256(packages_path),
                     "update_timestamp": updated,
                     "update_time_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(updated))},
    }
    (output / "repository.json").write_text(json.dumps(repository, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archives", type=Path, default=ROOT / "dist")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/repository")
    args = parser.parse_args()
    build_repository(args.archives, args.output)
