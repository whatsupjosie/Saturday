# Visual Patch Kit Copy Bundle

Created: 2026-06-11

This folder is a non-destructive copy bundle for later PubCast consolidation.
It has not been integrated into `Downloads\big zip` or any live PubCast build.

## Purpose

Keep the Visual Patch-Up Kit, adaptive prop fitting, approval lifecycle, and
live/meticulous repair-effort files together so they can be reviewed or moved
later without hunting through the working spine project.

## Included Runtime Files

- `runtime/spine/visual/voxel_patch_adapter.py`
  Unhooked adapter for turning Visual Patch-Up Kit patch requests and adaptive
  prop extensions into PubWorld/voxel payloads. This is included for future
  wiring but is not imported or connected to a live runtime path in this bundle.

- `runtime/spine/visual/patch_workflow.py`
  Operator/director/avatar selected-area repair workflow.

- `runtime/spine/visual/styling_aide.py`
  Visual Patch-Up Kit routing for physical patches, set styling, costume, and
  digital makeup requests.

- `runtime/spine/performers/visual_patch.py`
  Low-level visual patch coordinator and live voxel-to-bake request payloads.

- `runtime/spine/performers/interaction_fit.py`
  Avatar/object fitting, adaptive prop proposals, anatomy/silhouette clearance,
  approval gates, presence lifecycle, cache, save-to-inventory/room persistence,
  and live/balanced/meticulous effort policy.

- `runtime/spine/performers/contact.py`
  Contact object and resolver support used by interaction fitting.

- `runtime/spine/performers/motion_feedback.py`
  Animation/skeleton feedback bridge that can request visual patch help.

- `runtime/spine/event_bus.py`
  Canonical event bus needed by the copied runtime files.

- `runtime/spine/tests/test_spine_runtime.py`
  Regression tests covering the Visual Patch-Up Kit, adaptive props, approval,
  anatomy clearance, presence/cache behavior, and live/meticulous effort.

## Included Docs

- `docs/HANDOFF_VISUAL_PATCH_ADAPTIVE_PROPS_20260610.md`
  Handoff summary for the feature.

- `docs/SPINE_README.md`
  Spine README snapshot with feature notes.

- `reference_packages/handoff_package_visual_patch_adaptive_props_20260610_210745.zip`
  Latest previously generated handoff package.

## Included Voxel Engine Reference Files

The `voxel_engine_reference/` folder contains copy-only source references from
`Downloads\big zip` for future wiring:

- PubWorld/voxel contracts and asset generation:
  `voxel_set_contract.py`, `voxel_asset_manager.py`,
  `voxel_llm_adapter.py`, `voxel_studio_integration.py`,
  `pubworld_blocks.py`, `pubworld_router.py`, `pubworld.py`, and
  `pubworld_hotspots.py`.

- Bridge and integration files:
  `bridge.py`, `bridge_bulletproof.py`, `bridge_raw.py`, `unity_bridge.py`,
  `avatar_studio_bridge.py`, `alex_jeremy_bridge.py`, and
  `stage_compat_routes.py`.

- Frontend/stage references:
  `PubWorld.jsx`, `builder.html`, `pubworld_stage.html`, `stage.html`,
  `stage_3d.html`, and camera/stage architecture docs.

- Renderer references:
  `voxel_renderer.py`, `pubcast_voxel_hollow_patch.py`, Rust renderer source,
  Rust Cargo files, and the existing `bin/ws_renderer` binary.

- Contract tests and handoffs:
  voxel/stage/frontend contract tests plus the available PubCast/PubWorld
  handoff and README files.

## Important Notes

- This is a copy bundle only.
- Original source files remain in the spine work folder.
- The adapter is included but deliberately not hooked up yet.
- The copied voxel files are references for later integration, not live edits.
