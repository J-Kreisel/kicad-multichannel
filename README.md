# KiCad Multichannel Layout

Minimal footprint-only channel copier. Pick one source channel and any number
of target channels; the tool matches footprints pairwise and copies the
relative placement from the source.

## How it works

1. **Channel list** — the first named group is the source/main channel; all
   following groups are targets.
2. **Matching** — iteratively lock the least-ambiguous source↔target pair and
   re-score the rest using net topology and relative placement of already
   locked pairs.
3. **Offset voting** — each matched pair votes for an offset bucket. The bucket
   with the most votes wins; ties are broken by picking the bucket whose
   average movement is closest to the overall mean movement.
4. **Copy** — every matched target footprint is moved to
   `source_position + chosen_offset` and gets the source rotation.

Tracks/zones are ignored completely.

## Usage

Inside the Nix flake dev shell (`direnv` loads it automatically):

```sh
# list available channel groups
python multichannel_layout.py --list

# preview matchings and offsets for CH1 -> CH2, CH3, CH4
python multichannel_layout.py CH1 CH2 CH3 CH4 --dry-run

# verbose: print every matched pair and its individual offset
python multichannel_layout.py CH1 CH3 --dry-run --verbose

# actually copy placement
python multichannel_layout.py CH1 CH2 CH3 CH4
```

The offset bucket size defaults to 0.5 mm (500 000 nm). Override with
`--bucket-size NM`.

Because this talks to KiCad over the IPC API, you still need a running PCB
editor with the API enabled and the env vars `KICAD_API_SOCKET` /
`KICAD_API_TOKEN` set (KiCad sets them for its child processes; for a fully
external run, copy them from a plugin log or launch the shell from KiCad).

## Files

- `multichannel_layout.py` — CLI entry point.
- `multichannel_layout/board_reader.py` — reads KiCad groups → `Channel` with
  footprints only.
- `multichannel_layout/matching.py` — iterative ambiguity-first matcher.
- `flake.nix` / `.envrc` — dev shell with `kicad-python`.

## License

TBD.
