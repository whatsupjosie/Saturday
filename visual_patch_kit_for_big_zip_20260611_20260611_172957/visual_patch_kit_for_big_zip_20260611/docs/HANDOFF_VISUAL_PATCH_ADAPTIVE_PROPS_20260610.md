# Handoff: Visual Patch-Up Kit And Adaptive Props

Date: 2026-06-10

## Status

This work is in a good closure state. The runtime spine now has a tested,
non-destructive path for visual repair, uploaded avatar/object measurement, and
adaptive prop extensions.

The original avatar, outfit, set, and prop assets are not edited. The system
emits plans and events for live voxel overlays, temporary proxy alignment, and
later mesh-bake replacement.

## What Is Working

- Visual Patch-Up Kit request flow:
  - Menu/director/avatar request payloads can describe a selected area.
  - Requests are permission checked.
  - Selections can be `2d` or `3d`.
  - Direct material/texture repair is attempted before voxel fallback.
  - Live voxel patch requests can be routed through the visual patch system.

- Uploaded avatar/object support:
  - `avatar_file_ref` and `object_file_ref` can be attached to a visual selection.
  - OBJ files are measured for bounds, size, face count, and vertex count.
  - Large OBJ files are deferred to a dedicated mesh loader instead of being read in the lightweight path.
  - Malformed OBJ vertices are skipped and counted.
  - Bad 3D world size hints become warnings, not crashes.

- Adaptive prop extensions:
  - Large avatar plus standard chair can emit `object:adaptive_prop_extension_proposed`.
  - Approved proposals emit `object:adaptive_prop_extension` and may update the visible scene.
  - Proposals wait for user/director OK by default.
  - `adaptive_prop_auto_approve: true` can activate a proposal immediately.
  - Small avatar plus standard chair can receive a temporary foot-support extension.
  - Large avatar plus standard table/desk can receive a temporary surface-lift extension.
  - Adaptive extensions preserve original props.
  - Extensions carry voxel dimensions, material hints, bake policy, and retire policy.
  - Voxel dimensions are capped with `max_adaptive_voxel_axis` and `max_adaptive_voxel_count`.
  - `adaptive_prop_extension: false` is honored, including when supplied as text.
  - Extensions track avatar presence and can regress after an absence timeout.
  - Retired extensions are cached briefly, then expired from cache.
  - A clicked/approved extension can be saved to inventory and/or kept in the room.
  - Adaptive objects can include profile/silhouette anatomy clearance zones.
  - Seated pose profiles can drive chair or car-seat shape allowances.
  - Seat extensions can include a subtractive rear tail slot.
  - Hand-held objects can include grip clearance for nonstandard hands, claws, hooves, or extra hands.

## Key Files

- `runtime/spine/visual/patch_workflow.py`
- `runtime/spine/visual/styling_aide.py`
- `runtime/spine/performers/visual_patch.py`
- `runtime/spine/performers/interaction_fit.py`
- `runtime/spine/performers/contact.py`
- `runtime/spine/event_bus.py`
- `runtime/spine/tests/test_spine_runtime.py`
- `runtime/spine/README.md`

## Main Events

- `visual:patch_workflow_request`
- `visual:patch_workflow_report`
- `visual:style_request`
- `avatar:visual_patch`
- `avatar:visual_patch_bake_requested`
- `avatar:visual_patch_mesh_ready`
- `avatar:visual_patch_retired`
- `interaction:fit_report`
- `avatar:interaction_compensation`
- `object:adaptive_prop_extension_proposed`
- `object:adaptive_prop_extension`
- `object:adaptive_prop_extension_rejected`
- `object:adaptive_prop_extension_saved`
- `object:adaptive_prop_extension_kept_in_room`
- `object:adaptive_prop_extension_retired`
- `object:adaptive_prop_extension_cached`
- `object:adaptive_prop_extension_cache_expired`

## Example Adaptive Prop Meaning

If a performer is 135% of a standard human and sits in a normal chair, the chair
asset stays normal. The fit coordinator computes the mismatch and emits an
adaptive extension proposal. A renderer or voxel engine should wait until the
proposal is approved, unless auto-approve is enabled. After approval, it can
build a temporary seat extension, shade it to match the chair, bake it into a
temporary mesh overlay, and retire it when the interaction ends.

If the avatar leaves, the coordinator can mark the performer absent. After the
absence timeout, defaulting to 120 seconds, the extension regresses and is cached.
The cache defaults to 600 seconds before expiring. If the user clicks the object
and saves it to inventory or keeps it in the room, that persistence is recorded
and normal absence cleanup will not remove a kept room object.

Adaptive clearance is profile and silhouette driven. If the avatar has tail
joints, or the chair request includes `tail_clearance: true`, the adaptive seat
proposal includes a `tail_clearance` subtractive zone and a matching
`subtractive_cutouts` entry for the voxel patch. Seated profiles can also add
wing side relief and shoulder clearance, while hand-held objects can request
adaptive grip sleeves. This lets the voxel/mesh layer build around the avatar's
actual pose outline instead of relying on a single species label.

Live/meticulous repair effort is now explicit without becoming a separate mode.
Visual workflow requests can include `repair_effort`, `effort`, or
`meticulous_repair`, and adaptive prop metadata can include
`adaptive_prop_effort`. The live path still responds immediately with a voxel
overlay plan, while `balanced` and `meticulous` increase quality passes,
require remeasurement before bake, and can use finer default voxel resolution
unless explicit object caps are set.

## Validation

Last known good validation:

```powershell
& 'C:\Users\hardc\OneDrive\Documents\Playground\Python312\python.exe' -m unittest runtime.spine.tests.test_spine_runtime -v
# Ran 64 tests OK

& 'C:\Users\hardc\OneDrive\Documents\Playground\Python312\python.exe' -m compileall runtime\spine
# OK
```

## Known Next Steps

- Wire these events into the actual UI menu/director panel.
- Connect `object:adaptive_prop_extension` to the real voxel renderer.
- Add a dedicated GLTF/GLB/FBX/VRM mesh measurement loader.
- Add visual preview tooling so the user can approve or reject a patch before bake.
- Add integration tests once the renderer and UI event bridge are available.
