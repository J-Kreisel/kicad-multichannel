import hashlib
import json
import zipfile
from pathlib import Path

from tools.build_kicad_plugin import build
from tools.build_pcm_repository import build_repository


def test_pcm_archive_and_feed(tmp_path):
    archive = build(tmp_path / "kicad-multichannel-test-kicad-pcm.zip")
    with zipfile.ZipFile(archive) as package:
        names = package.namelist()
        assert "plugins/plugin.json" in names
        assert "plugins/multichannel_layout/gui.py" in names
        assert "plugins/multichannel_layout/service.py" in names
        assert "plugins/requirements.txt" in names
        assert "resources/icon.png" in names
        assert not any("__pycache__" in name or "tests/" in name for name in names)
        manifest = json.loads(package.read("plugins/plugin.json"))
        assert "plugins/" + manifest["actions"][0]["entrypoint"] in names
        for icon in manifest["actions"][0]["icons-light"] + manifest["actions"][0]["icons-dark"]:
            assert "plugins/" + icon in names
        metadata = json.loads(package.read("metadata.json"))
        assert metadata["identifier"] == manifest["identifier"]
        assert metadata["versions"][0]["runtime"] == "ipc"
    output = tmp_path / "feed"
    build_repository(tmp_path, output)
    repository = json.loads((output / "repository.json").read_text())
    assert repository["packages"]["sha256"] == hashlib.sha256((output / "packages.json").read_bytes()).hexdigest()
    version = json.loads((output / "packages.json").read_text())["packages"][0]["versions"][0]
    assert version["download_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert (output / Path(version["download_url"]).name).is_file()
