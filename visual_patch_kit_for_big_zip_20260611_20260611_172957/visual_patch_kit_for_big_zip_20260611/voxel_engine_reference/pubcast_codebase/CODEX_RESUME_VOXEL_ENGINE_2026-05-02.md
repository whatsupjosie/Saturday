# Codex Resume Marker - Voxel Engine Wiring

Marked: 2026-05-02 14:02:17 -07:00

## Active Workspace

- Primary build folder: `C:\Users\hardc\OneDrive\Documents\Playground\Pubcast codex run`
- Parent git root: `C:\Users\hardc\OneDrive\Documents\Playground`
- Parent branch: `april-branch-2`
- Parent remote: `https://github.com/whatsupjosie/dreams.git`
- `Pubcast codex run` is currently an untracked playground folder inside the parent repo.
- Local `dinner` branch was not found during inspection. Nearby PubCast repo copies were on `Brunch` or the parent was on `april-branch-2`.

## User Architecture Direction

- AI should be creative, advisory, instructional, and used sparingly.
- Twin/voxel engines should do the heavy computational work.
- E-Pete should act as dispatcher/governor and ask AI only when instruction or judgment is needed.
- Cameras may also act like voxel-engine-capable components.
- Avoid unnecessary AI dependence in hot paths.

## Files Restored From 5.6 Zip

The following missing renderer/engine files were extracted from:
`C:\Users\hardc\Downloads\PubCast_5.6_session_memory_characters.zip`

- `bin/ws_renderer`
- `rust_crate/Cargo.lock`
- `rust_crate/Cargo.toml`
- `rust_crate/src/animation_data_library.rs`
- `rust_crate/src/avatar_animation_system.rs`
- `rust_crate/src/avatar_fitting_system.rs`
- `rust_crate/src/complete_animation_controller.rs`
- `rust_crate/src/lib.rs`
- `rust_crate/src/skeleton.rs`
- `rust_crate/src/ws_renderer.rs`

No existing files were overwritten during that extraction.

## Backup / Preserve Points

- Main pre-wiring backup:
  `C:\Users\hardc\OneDrive\Documents\Playground\Pubcast codex run\rubish\pre_voxel_wiring_20260502_115540`
- Added missing reference copy for:
  `modules/voxel_asset_manager.py`
- Additional pre-AI-dependency-audit copy:
  `C:\Users\hardc\OneDrive\Documents\Playground\Pubcast codex run\rubish\pre_ai_dependency_audit_20260502_135433\modules\voxel_llm_adapter.py`

## Current Implemented Wiring

### `main.py`

- PubWorld block imports expanded to include:
  `create_prop`, `generate_from_prompt`, `list_builder_presets`, `save_builder_preset`.
- Added PubWorld prop helpers:
  `_generated_blocks_to_pubworld_blocks`
  `_pubworld_blocks_to_stage_voxels`
- Added endpoints:
  `POST /api/pubworld/props`
  `POST /api/pubworld/props/generate`
  `GET /api/pubworld/builder/presets`
  `POST /api/pubworld/builder/presets`
- Fixed `/api/voxel/generate` return unpacking bug.
- `/api/voxel/generate` now returns:
  `ok`, `label`, `provider`, `blocks`, `voxels`, `count`.
- Lifespan now attempts `voxel_bridge.connect()` and injects the bridge into `voxel_asset_manager.config["bridge"]`.
- PubWorld prop generation default was changed from `auto` to `pubworld_rules`.
- `/api/voxel/generate` default provider was changed from `auto` to `local`.

### `modules/voxel_asset_manager.py`

- `sync_with_rust_engine` now accepts either Pete or an injected bridge.
- Replaced TODO with bridge dispatch:
  `bridge.load_scene(scene.scene_id, scene.to_dict())`
  or `bridge.send_command("LOAD_SCENE", ...)`.
- If bridge refuses the scene, the method logs and returns `False`.

### `static/builder.html`

- Added text prompt UI for voxel set creation.
- Added `SceneBuilder.generatePromptSet()`.
- Builder now calls:
  `POST /api/pubworld/props/generate`
- Builder now sends provider:
  `pubworld_rules`
- Builder scene data now tracks:
  `voxel_sets`
- Asset library now tries `/api/voxel/assets` first and falls back to local hardcoded assets.
- Scene loading now supports wrapped save files via `data.content || data`.

### `static/pubworld_stage.html`

- Added prompt input/button/status to the Voxel Library panel.
- Added `generateVoxelPrompt()`.
- Stage now calls:
  `POST /api/pubworld/props/generate`
- Stage now sends provider:
  `pubworld_rules`
- Generated voxels are pushed into `VOX_LIBRARY`, UI rebuilt, and blocks fly into the scene.

## Verification Completed

- Python syntax check passed with Blender Python:
  `python -m py_compile main.py modules\voxel_asset_manager.py modules\pubworld_blocks.py modules\voxel_llm_adapter.py`
- JavaScript inline script parse check passed with Node:
  `static/builder.html`: 1 inline script block parsed
  `static/pubworld_stage.html`: 2 inline script blocks parsed
- Search confirmed the new PubWorld prompt buttons no longer send `provider: 'auto'`.

## Verification Not Completed

- `py.exe` exists but reports no registered Python install.
- Blender Python exists, but does not have `pytest`.
- Therefore focused pytest tests were not run.

## AI Dependency Audit Findings

Expected / useful AI paths:

- `main.py` explicit `/api/inference/generate`
- `main.py` explicit TTS endpoints
- Character speech adapter using Studio route
- Bot chat replies through configured providers
- `avatar_studio_bridge.architect_plan()` for explicit Architect planning
- E-Pete inference orchestration

Likely avoidable AI dependency found and partly corrected:

- Prompt-to-voxel generation was defaulting to `auto`, which can use cloud/Ollama before local generation.
- Live PubWorld/builder/stage paths now default to rules/local engine use.

Still worth addressing next:

- `modules/voxel_llm_adapter.py` itself still has default `provider="auto"` and docs describing it as cloud-first.
- A patch was started for that file but did not apply because the file contains mojibake/encoding-different arrow text. No change was made to that file yet.
- Recommended next step: edit `modules/voxel_llm_adapter.py` so its function default is `provider="local"` and its docs say AI is opt-in. Keep explicit `provider="auto"` available for creative escalation.
- E-Pete currently routes `TaskType.STRUCTURED_DATA` to Architect by default. This is good for uncertain JSON planning, but should not be used for deterministic voxel/block conversion. Voxel and scene-building code should call engines directly and only escalate creative ambiguity to AI.

## Next Safe Resume Step

1. Patch `modules/voxel_llm_adapter.py` default from `auto` to `local` using a small ASCII-only patch around the function signature and provider normalization.
2. Add or update a focused test for `/api/pubworld/props/generate` proving default provider is `pubworld_rules`.
3. If a full Python environment becomes available, run:
   `python -m pytest tests/test_frontend_startup_contracts.py tests/test_frontend_contract_compat.py`
4. Optionally start the local app and verify:
   `/static/builder.html`
   `/static/pubworld_stage.html`

## Current Stopping Point

The engine has been found and wiring has begun. The builder and stage now have prompt-to-voxel UI connected to PubWorld prop generation, with engine/rules-first defaults. The Rust bridge files are present and the asset manager can send scenes to the bridge when available.

## 2026-05-02 Autonomous Continuation

- Snapshot zip was created and verified before this work:
  `C:\Users\hardc\OneDrive\Documents\Playground\PUBCAST_AI_SNAPSHOTS\PubCast_AI_v6_codex_run_snapshot_2026-05-02_1630.zip`
- `modules/voxel_llm_adapter.py` now defaults `generate_with_cloud(..., provider="local")`, and `provider = (provider or "local").lower()`.
- `httpx` is now optional in `modules/voxel_llm_adapter.py`; local/default voxel generation works even when cloud/HTTP dependencies are absent.
- Added stdlib-only canonical voxel set contract:
  `modules/voxel_set_contract.py`
- Added focused contract tests:
  `tests/test_voxel_set_contract.py`
- Created deterministic proof set:
  `data/pubworld/voxel_sets/small_theater_platform_two_walls_doorway_codex_proof_20260502.json`
- Proof set: 48 blocks, dimensions `{"x": 7, "y": 3, "z": 5}`, AI authority `off`, sandbox-only `true`.
- `modules/voxel_asset_manager.py` now discovers saved `data/pubworld/voxel_sets/*.json` files and exposes them as voxel assets, even when the static asset library JSON is missing.
- Manual proof passed with Blender Python:
  - voxel contract create/validate/save/reload
  - deterministic proof set save/reload
  - asset manager discovers proof set
  - local/default voxel LLM adapter returns provider `local`
- `static/builder.html` and `static/pubworld_stage.html` had Google Font dependencies removed and now use system fonts. `static/builder.html` viewport tag was fixed from invalid `<parameter>` to `<meta>`.
- Remaining voxel blocker: `static/pubworld_stage.html` still depends on CDN Three.js. Do not remove until local Three/GLTFLoader replacement exists.
