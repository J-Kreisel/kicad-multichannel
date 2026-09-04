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
4. **Plan update** — matching produces one KiCad-independent `UpdatePlan` per
   target. It contains the source/target channel names and origins plus pairs
   of matched footprint references and verified net names.
5. **Copy placement** — every matched target footprint is moved using the
   chosen translation and rotation.
6. **Replace routing** — the executor resolves current tracks and vias from the
   named groups. Existing target routing for verified nets is removed and
   replaced by transformed source routing.
7. **Validate groups** — before committing, the complete group UUID topology is
   reconciled and checked. KiCad does not allow groups as IPC item containers,
   so object changes are committed first and their UUID memberships are
   repaired in subsequent commits after the objects are stable board items.
   Incorrect parent memberships are committed as removals before target-group
   memberships are added, because KiCad enforces one parent group per item.

Before applying a plan, the executor invokes KiCad's
`common.Interactive.groupLeave` action enough times to exit any nested group
editing context. KiCad otherwise implicitly inserts updated items, including an
updated target group itself, into the group currently being edited.

Each applied channel therefore creates at least two KiCad undo steps, and three
when items first need to be detached from another group. If restoration fails,
undo until the `Update multichannel objects` step has been reverted.

Routing outside the target group is not removed. Zones and routed arcs are
ignored. Group membership is changed only for replaced and newly created
tracks/vias; footprints, rule areas, and all other group members are preserved.
Locked footprints are not moved. If a verified target net has any locked track
or via, that entire net is left untouched.

An update plan has this shape:

```python
UpdatePlan(
    source_channel="CH1",
    target_channel="CH2",
    source_origin=(120_000_000, 85_000_000, 0.0),
    target_origin=(160_000_000, 85_000_000, 0.0),
    footprint_matches=(("R1", "R28"), ("C1", "C15")),
    net_matches=(("/CH1/SIGNAL", "/CH2/SIGNAL"),),
)
```

The executor receives only this plan. It resolves group IDs, footprint IDs,
routing objects, and lock state from the live board immediately before the
update.

Origin calculation and offset bucketing are hidden behind the planner. Once it
has the channel snapshots, callers only provide two names:

```python
planner = UpdatePlanner(channels, bucket_size_nm=500_000)
source_origin, target_origin = planner.find_channel_origins("CH1", "CH2")
plan = planner.build_update_plan("CH1", "CH2")
```

Locked target footprints are authoritative origin anchors. With one anchor,
its source and target footprint poses become the two origins and other matches
do not participate in bucketing. Multiple locked anchors must describe exactly
the same rigid transform. Conflicting anchors, or a locked target footprint
without a source match, make planning fail before the board is modified.

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

- `multichannel_layout.py` — minimal executable entry point.
- `multichannel_layout/cli.py` — argument parsing and reporting.
- `multichannel_layout/models.py` — KiCad-independent snapshots and plans.
- `multichannel_layout/board_reader.py` — reads channels and complete group UUID
  membership.
- `multichannel_layout/matching.py` — iterative ambiguity-first matcher.
- `multichannel_layout/transforms.py` — pure placement geometry.
- `multichannel_layout/placement.py` — matched-footprint geometry helpers.
- `multichannel_layout/origins.py` — name-based origin and bucket resolution.
- `multichannel_layout/routing.py` — verified net and routing planning.
- `multichannel_layout/planner.py` — builds the complete `UpdatePlan`.
- `multichannel_layout/group_topology.py` — group invariants and expected state.
- `multichannel_layout/executor.py` — the only KiCad mutation boundary.
- `flake.nix` / `.envrc` — dev shell with `kicad-python`.

## License

TBD.
