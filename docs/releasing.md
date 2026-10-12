# Releases

The repository is `J-Kreisel/kicad-multichannel`; its `pcm` branch hosts the third-party
Plugin and Content Manager feed. This is not an official KiCad PCM listing.

## Before tagging

- Update `project.version` in `pyproject.toml`.
- Run `pytest` (on NixOS: `QT_QPA_PLATFORM=offscreen nix develop --command pytest`).
- Build with `python tools/build_kicad_plugin.py`.
- Install the ZIP using PCM, restart PCB Editor, and verify toolbar launch.
- On a disposable PCB, test preview, copy, membership repair, locked items,
  stale preview rejection, and undo through **Update multichannel objects**.
- Verify supported platforms; keep the package in `testing` until validation is complete.

## Publish

```sh
git push origin main
git tag -a v0.1.1 -m 'KiCad Multichannel Layout 0.1.1'  # must equal pyproject.toml version
git push origin v0.1.1
```

The release workflow runs tests, builds the PCM ZIP, creates a GitHub prerelease,
and updates `pcm/repository.json` and `pcm/packages.json` with download hashes.
It needs GitHub Actions enabled and permission to write repository contents.
The feed currently offers the latest release only. Existing installs remain installed;
GitHub Releases retains old archives.

Manual build of the feed:

```sh
python tools/build_kicad_plugin.py
python tools/build_pcm_repository.py --output dist/repository
```

## Current validation

- Unit tests for matching, geometry, planning, executors, and group invariants.
- Service tests for stale previews, duplicate groups, and partial failures.
- Offscreen GUI tests using a mocked KiCad connection.
- Archive/feed content and checksum tests.
- Local tests use KiPy 0.8.0 and Python 3.11/3.13 on Linux/NixOS.
- GitHub CI passes the same suite on Linux, Windows, and macOS (Python 3.11).
- Live KiCad 10.0.6 smoke test on a disposable copy of the previous test PCB:
  two targets each received 26 footprints, 66 tracks, and one via; stale re-apply
  was rejected; add/remove membership analysis and no-op apply succeeded.
- GUI connected to that live PCB using the launcher's NixOS library discovery
  and X11 environment. The launcher deliberately avoids overriding Python's libc.
- Undo, actual PCM installation, and Windows/macOS runtime tests remain manual
  validation work. The initial package is intentionally marked testing.
