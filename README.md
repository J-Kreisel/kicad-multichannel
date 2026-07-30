# KiCad Multichannel Layout

Multichannel placement and routing copier. Pick one source channel and any
number of target channels; the tool matches footprints pairwise, copies their
relative placement, and replaces safely matched target routing.

## How it works

1. **Channel list** — the first named group is the source/main channel; all
   following groups are targets.
2. **Matching** — reject pairs below 95% pad-layout compatibility, then
   iteratively lock the least-ambiguous pair. Each pad whose net relationship
   agrees with already locked pairs contributes `+1 / pad count`; a
   contradiction contributes `-1 / pad count`, with the net score floored at
   zero. Placement is only a tiebreaker.
3. **Offset voting** — each matched pair votes for an offset bucket. The bucket
   with the most votes wins; ties are broken by picking the bucket whose
   average movement is closest to the overall mean movement.
4. **Copy placement** — every matched target footprint is moved using the
   chosen translation and rotation.
5. **Replace routing** — for each routed source net, every source-group pad on
   that net must map to a pad on one target net through the accepted footprint
   matches. Existing tracks and vias for that net inside the target group are
   then removed and replaced by transformed copies from the source group.

Routing outside the target group is not removed. Zones and routed arcs are
ignored. Group membership is changed only for replaced and newly created
tracks/vias; footprints, rule areas, and all other group members are preserved.

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
- `multichannel_layout/board_reader.py` — reads KiCad groups into channels with
  footprints, straight tracks, and vias.
- `multichannel_layout/matching.py` — iterative ambiguity-first matcher.
- `flake.nix` / `.envrc` — dev shell with `kicad-python`.

## License

TBD.
