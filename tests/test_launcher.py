from pathlib import Path

from kicad_plugin.launch import _kicad_library_paths


def test_nixos_library_discovery_does_not_override_interpreter_runtime(monkeypatch):
    monkeypatch.setenv("KICAD_API_SOCKET", "ipc:///tmp/kicad/api-123.sock")
    maps = "\n".join([
        "address perms offset dev inode /nix/store/hash-glibc-2.42/lib/libc.so.6",
        "address perms offset dev inode /nix/store/hash-python3-3.14/lib/libpython3.14.so",
        "address perms offset dev inode /nix/store/hash-libglvnd-1.7/lib/libGL.so.1",
    ])
    monkeypatch.setattr(Path, "read_text", lambda self: maps)
    monkeypatch.setattr(Path, "glob", lambda *args: [])
    assert _kicad_library_paths() == [str(Path("/nix/store/hash-libglvnd-1.7/lib"))]
