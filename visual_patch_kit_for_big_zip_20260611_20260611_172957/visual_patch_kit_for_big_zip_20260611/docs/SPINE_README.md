# PubCast Runtime Spine

This package is the canonical runtime spine foundation for PubCast AI. It was
added as an isolated, reversible layer and does not modify existing WebSocket,
audio, mocap, or active UI contracts.

## What It Owns

- `event_bus.py`: immutable event envelopes with subscribe, unsubscribe, sync
  emit, async emit, event history, wildcard subscriptions, handler failure
  isolation, diagnostic error history, and teardown.
- `performer_registry.py`: authoritative performer lifecycle and state mutation
  methods that emit events for spawn, movement, animation, and station changes.
- `performers/performer_state.py`: JSON-serializable performer state object.
- `station_registry.py` and `stations/base_station.py`: operational station
  contract and station lifecycle registry.
- `performers/locomotion.py`: target-based avatar walking with movement events
  and simple walk-cycle pose generation.
- `performers/animation_authority.py`: priority-based animation arbitration.
- `performers/motion_retargeting.py`: elastic cross-skeleton retargeting that
  maps source motion intent onto the selected avatar's available joints, scales
  motion by body proportions, and supports both natural target-body equivalents
  and opt-in literal source-shadow mapping.
- `performers/motion_feedback.py`: feedback bridge that listens to animation
  retarget diagnostics and emits live compensation guidance for avatar AI,
  Jeremy stage-direction, UI panels, or debug tooling.
- `performers/visual_patch.py`: non-destructive visual repair coordinator that
  turns mesh/outfit glitches, Jeremy patch requests, or severe motion feedback
  into live voxel overlay patches that can later be baked into mesh overlays.
- `visual/styling_aide.py`: the visual patch-up kit. It routes physical visual
  requests for set dressing, costuming, digital makeup, and emergency repair
  into non-destructive plans and events.
- `performers/skeleton_binding.py`: adapter that creates and serializes the real
  `modules.avatar_skeleton_system.PubCastSkeleton` for performer state.
- `performers/animation_presets.py`: Python mirror of the JS walk, sit, wave,
  type, and idle presets with skeleton-joint validation.
- `performers/mocap_diagnostics.py`: checks incoming mocap joint names against
  canonical PubCast skeleton targets.
- `performers/contact.py`: resolves avatar/object contact such as couch sitting,
  typewriter typing, button/TV reaching, and emits `performer:contact`.
- `performers/interaction_fit.py`: proportional prop/station fitting for phones,
  chairs, stairs, typing surfaces, steering wheels, and misaligned contact
  boxes. It emits non-destructive proxy adjustments and avatar anchor offsets
  instead of altering real prop assets. When enabled, it can also request live
  adaptive prop extensions for oversized or undersized performers.
- `tests/test_spine_runtime.py`: focused stdlib tests for the spine layer.

## Intentionally Untouched

- Existing WebSocket message contracts.
- `modules/mocap_precision.py`.
- Audio and microphone systems.
- Existing active UI files.

## Skeleton Integration

`PerformerRegistry` binds a default `PubCastSkeleton` to newly spawned performers
through `modules.avatar_skeleton_system.create_standard_skeleton()`. The skeleton
module remains unchanged; the spine uses an adapter so performer state can carry
the skeleton object and serialize its joints, bind pose, and retargeting map for
events or replication.

## Animation And Contact Hardening

The frontend preset file from `animation_presets.js` has been copied into
`static/js/avatar/animation_presets.js`. The runtime spine also has a Python
mirror so tests can validate track names against the active skeleton before an
animation is used.

All animation frames pass through `MotionRetargeter` before animation authority.
The default retarget mode is `target_equivalent`: the chosen avatar's skeleton
owns its natural limits, so a bird walk on a human becomes a human-equivalent
walk instead of forcing bird knees onto a human rig. The opt-in
`literal_source` mode shadows source motion more directly for intentionally
strange or uncanny results while still dropping missing joints and clamping hard
joint limits.

Retarget reports include a `compatibility_score` from `0.0` to `1.0`,
`risk_level` (`good`, `usable`, `strained`, or `likely_bad`), and warnings such
as extreme body ratios, invalid sanitized source channels, many dropped joints,
or many clamped channels. These diagnostics are meant to tell the UI or debug
tools when an animation is flexible enough to use and when it is likely to look
bad without a custom pass.

`MotionFeedbackCoordinator` can subscribe to `performer:animated`, read the
retarget report, and publish:

- `motion:feedback` for general diagnostics.
- `avatar:compensation_hint` for live animation compensation such as longer
  blends, reduced amplitude, conservative stride/reach scaling, or simpler
  fallback motions.
- `jeremy:stage_direction` for private conductor-style guidance when the motion
  needs narrative or performance help.

It also accepts optional sink callables, so the real Jeremy Cricket or avatar AI
systems can be wired in later without making the skeleton layer depend directly
on those systems.

## Visual Patch Flow

The visual patch-up kit treats visual repair as part of the physical production
realm rather than as animation math or color encoding. Visual patches are
temporary overlays, not edits to the original
avatar, set, prop, or outfit asset. The intended lifecycle is:

1. `avatar:mesh_glitch`, `jeremy:visual_patch_request`, or severe
   `motion:feedback` arrives.
2. `VisualPatchCoordinator` emits `avatar:visual_patch` with a small voxel
   scaffold anchored to a body joint, such as a shoulder, chest, cuff, or calf.
3. If `bake_to_mesh` is true, it also emits
   `avatar:visual_patch_bake_requested` with `swap_strategy` set to keep the
   live voxels visible until a baked mesh is ready.
4. A voxel/mesh renderer can shade, texture, and bake the patch into a mesh
   overlay, then emit `avatar:visual_patch_mesh_ready`.
5. The coordinator emits `avatar:visual_patch_retired` so the live voxel
   scaffold can disappear and the baked mesh overlay remains.

`VisualPatchUpKit` listens for `visual:style_request` and `jeremy:style_request`
and can route the request to:

- `avatar:visual_patch` for urgent repair.
- `costume:style_adjustment` for clothing and outfit changes.
- `avatar:digital_makeup` for face, lips, eyes, cheeks, and skin overlays.
- `set:style_adjustment` for room, wall, lighting, backdrop, and set dressing.

This keeps the visual styling layer broad enough to help the whole production
while still allowing animation and skeleton systems to ask for emergency cover
when they need it.

`VisualPatchWorkflow` adds the operator-facing workflow around that kit. A menu,
director panel, or permissioned avatar can emit `visual:patch_workflow_request`
with a highlighted region, a short text description, and either a `2d` or `3d`
selection mode. The workflow checks requester permission, inspects the selected
region description, records screen and optional world measurements, tries a
direct material/texture repair first, and only falls back to live voxel overlay
patching when the issue appears physically mismatched.

Selections can also carry uploaded `avatar_file_ref` and `object_file_ref`
values plus `contact_constraints`, such as `contact: sit`,
`avatar_anchor: pelvis`, and `object_anchor: seat`. The first lightweight loader
can measure OBJ bounds directly and records metadata for heavier formats such as
GLTF, GLB, FBX, or VRM so a deeper mesh loader can take over later. It skips
malformed OBJ vertices, defers large OBJ files to a dedicated mesh loader, and
marks invalid 3D size hints instead of crashing the workflow. This allows the
system to start solving "make this avatar meet this chair/phone/stair" problems
from the actual uploaded files while keeping the original assets unchanged.

Patch workflow requests can also include `repair_effort`, `effort`, or
`meticulous_repair`. This is not a separate mode: the workflow still answers as
a live patch path, but the effort policy changes how careful it is before bake.
`live` uses a bounded coarse overlay, `balanced` refines from depth and asset
metadata, and `meticulous` keeps the live first response while requiring extra
quality passes and a remeasure before baking the temporary mesh.

Object contact is resolved separately from raw mocap. This keeps live mocap,
locomotion, and interaction poses from fighting each other: contact resolution
chooses an intended animation, required anchors, reachability, and a pose delta,
then emits a `performer:contact` event for downstream replication or rendering.

`InteractionFitCoordinator` can then evaluate whether the chosen avatar and
object actually fit each other. It can emit `interaction:fit_report` and
`avatar:interaction_compensation` with proportional corrections such as scaling
a tiny phone proxy, shifting a contact radius, adjusting chair seat height,
adding intermediate stair targets, sliding a typing surface, or resizing a
steering wheel grip proxy. These are live alignment hints; the original object
assets remain untouched.

For a performer who is much larger than a normal human, the set can still use
normal furniture. If a 135% scale avatar approaches a standard chair, the fit
coordinator can emit `object:adaptive_prop_extension_proposed` with a live voxel
build plan for the current performer. Nothing is sent to the visible scene until
the proposal is approved, unless `adaptive_prop_auto_approve` is enabled for
that prop. Approved proposals emit `object:adaptive_prop_extension`. The plan
includes target seat height, width, depth, voxel dimensions, material matching
hints, and a bake policy. The chair asset itself remains unchanged; the
extension can retire when that interaction ends or be swapped for a temporary
baked mesh overlay once ready.

The same adaptive path now covers small avatars and normal tables. A small
performer can receive a temporary foot-support block instead of forcing the
animation to dangle or overextend the legs. A large performer at a normal table
can receive a live surface-lift overlay. Adaptive prop requests honor
`adaptive_prop_extension: false` even when supplied as text, and voxel dimensions
are capped with `max_adaptive_voxel_axis` and `max_adaptive_voxel_count` so an
extreme mismatch cannot ask the renderer to build an unbounded grid.

Adaptive prop proposals carry the same repair-effort contract. Metadata such as
`adaptive_prop_effort: meticulous` keeps the live voxel overlay behavior but can
use finer default voxel size, a larger default budget unless explicit caps are
set, delayed bake timing, and quality checks such as contact stability, visual
blend, voxel budget, and anatomy clearance. Explicit
`max_adaptive_voxel_axis`, `max_adaptive_voxel_count`, and
`adaptive_voxel_size` values still win over the effort defaults.

Adaptive extensions are tied to avatar presence. A performer can be marked
absent, and `sweep_presence()` can regress their temporary extension after a
configurable timeout, defaulting to two minutes. Retired extensions are cached
for quick recovery, defaulting to ten minutes, then removed from cache. A user
can also click an approved adaptive object and save it to inventory or keep it
in the room. Kept room objects are not removed by the normal absence sweep.

Adaptive objects can also carry anatomy clearance zones from the avatar's
profile and silhouette. The fitter can merge general silhouette data with a
pose-specific profile, such as `pose_profiles.seated`, so a chair or car seat is
customized for the avatar's seated outline rather than only its standing rig.
If an avatar skeleton has tail joints, or the prop request sets
`tail_clearance: true`, the chair proposal includes a subtractive rounded rear
slot so the renderer can build a larger chair while leaving a tail channel open.
Seated profiles can also request wing, shoulder, or other upper-body clearance
relief. Hand-held objects such as cups, phones, mugs, remotes, and glasses can
receive grip clearance zones for nonstandard hands, claws, hooves, or extra
hands. All dimensions remain metric.

`modules/avatar_assets.py` was hardened to import without `pydantic`, recover
from corrupt manifests, and keep default avatar packs available.

## Troubleshooting Notes

- `EventBus.errors()` records failed subscriber calls without stopping later
  subscribers for the same event.
- Station activation through `PerformerRegistry` now emits locomotion
  `performer:state_change` events when entering and leaving `interacting`.
- Focused regression tests cover handler failure isolation and station state
  event emission.

## Validation

Focused spine validation:

```powershell
& 'C:\Users\hardc\OneDrive\Documents\Playground\Python312\python.exe' -m unittest runtime.spine.tests.test_spine_runtime -v
& 'C:\Users\hardc\OneDrive\Documents\Playground\Python312\python.exe' -c "from runtime.spine import *; print('Imports OK')"
& 'C:\Users\hardc\OneDrive\Documents\Playground\Python312\python.exe' -m compileall runtime\spine
```

Full pytest validation could not be run in this local environment because the
available Python runtime does not have `pytest` installed.
