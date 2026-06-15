# PubCast Codex Run Handoff

## 2026-05-01 Continuation Status

This is a stronger release-candidate fix-up pass, not a finished or ship-ready product. The app has not been proven by FastAPI boot, full pytest, browser walkthrough, Ollama checks, real camera feed verification, mocap capture verification, export verification, or an end-to-end show simulation.

### This Session Completed

- Strengthened `static/director_switcher.html` as the control-room truth surface instead of a decorative mockup.
- Added visible truth cards for system health, recording, mocap, audio, scene/camera, timeline, AI brains, and export readiness.
- Wired console actions toward existing backend endpoints where they exist:
  - camera preview/program: `POST /api/cameras/switch`
  - cut: `POST /api/cameras/cut`
  - production state: `POST /api/state/production`
  - recording start: `POST /api/recording/start`
  - recording stop: `POST /api/recording/{session_id}/stop`
  - pause/resume: `POST /api/recording/{session_id}/pause`
  - marker: `POST /api/recording/{session_id}/marker`
  - panic: `POST /api/production/panic`
  - status: `/health`, `/api/studio/status`, `/api/cameras`, `/api/recording/storage`, `/api/mocap/status`, `/api/timeline/status`, `/api/voxel/status`, `/api/byok/ollama/status`
- Preserved honest local fallback behavior. If backend/auth/runtime is unavailable, controls still visibly press, update local state where appropriate, and log visible failure messages.
- Added `PAUSE` and `EXPORT` controls. They respond visibly and attempt backend routes only when the required backend session exists.
- Made local recording language honest: local-only recording is labeled `Local REC`; no fake live video feed is claimed.
- Added navigation links to `/director-switcher` from:
  - `static/control.html`
  - `static/control_room.html`
  - `static/stage.html`
- Added a minimum recreate/export contract:
  - `modules/recreate_bundle.py`
  - `docs/PUBCAST_RECREATE_BUNDLE.md`
  - `tests/test_recreate_bundle_contract.py`
- Copied only the two small principal GLB assets into this isolated workspace:
  - `assets/avatar/manny.glb`
  - `assets/avatar/sheila.glb`
- Added `data/avatars/manifest.json` registering Manny and Sheila as real GLB performer assets. They are explicitly not sprites. Rig/skeleton versions are still marked pending inspection.
- Moved newly generated validation `__pycache__` into `rubish/pycache_20260501_recreate_validation/modules`.

### Files Changed This Session

- `static/director_switcher.html`
- `static/control.html`
- `static/control_room.html`
- `static/stage.html`
- `modules/recreate_bundle.py`
- `docs/PUBCAST_RECREATE_BUNDLE.md`
- `tests/test_recreate_bundle_contract.py`
- `data/avatars/manifest.json`
- `assets/avatar/manny.glb`
- `assets/avatar/sheila.glb`
- `HANDOFF.md`

### Director Switcher Current Behavior

- Program and Preview monitors exist.
- Auxiliary camera surfaces exist; unavailable feeds are labeled `FEED UNAVAILABLE`.
- Preview camera buttons update the Preview monitor locally and attempt `/api/cameras/switch`.
- Program camera buttons update the Program monitor locally and attempt `/api/cameras/switch` plus `/api/state/production`.
- `CUT` swaps Preview into Program locally and attempts `/api/cameras/cut`.
- `FADE` visibly moves Preview to Program, logs the transition, and attempts camera/state backend updates.
- `REC` starts/stops visible local recording state and attempts real recording start/stop.
- `PAUSE` toggles paused state only when recording is active, and attempts backend pause when a backend session is known.
- `MARK` logs locally and attempts backend marker only when a backend session is known.
- `EXPORT` blocks during recording, warns if no backend session exists, and attempts backend export when a session exists.
- `PANIC` resets local program/preview/recording state and attempts `/api/production/panic`.
- `BARS`, presets, audio mutes, knobs, blackout, chat panel toggles, danger panel toggles, refresh, and self-test all have visible behavior.
- Public chat and control-room chat are local notes unless/until WebSocket/server chat is wired; the UI says this.
- Event log timestamps operator actions and visible backend failures.
- No fake live feed is shown without a local/unavailable/backend-unbound label.
- `static/director_switcher.html` contains no external URL, CDN, or remote font reference.

### Director Console Checklist

1. Every visible button/control was browser-click tested: **not proven**. Browser tool failed with `Target page, context or browser has been closed`; Node Playwright package is not installed. Static handler audit passed and inline JS parses.
2. Every visible control produces real backend action, local state, visible pressed state, event log entry, or explicit unavailable message: **static proof passed**.
3. Program monitor updates when Cut/Fade happens: **static JS path verified**; browser runtime proof pending.
4. Preview monitor updates when standby camera is selected: **static JS path verified**; browser runtime proof pending.
5. Camera 3/4/alternate monitors show local previews or honest unavailable states: **static proof passed**.
6. Status/danger panel open/close: **handler present**; browser runtime proof pending.
7. Public chat panel open/close: **handler present**; browser runtime proof pending.
8. Control-room chat panel open/close: **handler present**; browser runtime proof pending.
9. Recording state visible: **static proof passed**, local-only state labeled.
10. Mocap/scene/camera/timeline capture state visible: **truth cards present**, backend runtime proof pending.
11. Export readiness visible: **truth card present**, backend runtime proof pending.
12. No fake live feed without simulated/local/unavailable label: **static proof passed**.
13. Console page works offline with no external assets/font links: **static proof passed for `director_switcher.html`**.
14. Console route reachable from app navigation: **route and links present**, FastAPI/browser proof pending.
15. Failures visible to operator: **static proof passed** via event log and danger panel failure rendering.

### Recreate Bundle Contract

`modules/recreate_bundle.py` now defines the minimum JSON-compatible bundle shape for later rerender/recreate:

- `session_id`, `project_id`, `scene_id`, `take_id`
- final program/export reference
- camera timeline events with timestamps
- mocap events/frames
- avatar identity plus rig/skeleton version
- stage/environment version
- lighting events
- audio references
- chat/event log
- AI/bot participation log
- system warnings, dropped-frame markers, failed-feed markers
- export status and recreate readiness

This is a contract and smoke-tested dataclass module. It is not yet wired into recording stop/export.

### Avatar / GLB Findings

- Real Manny and Sheila GLBs were found in `C:\Users\hardc\Downloads`.
- They were copied into this isolated build because they are small and directly relevant to the 3D performer requirement.
- `data/avatars/manifest.json` now points to `/assets/avatar/manny.glb` and `/assets/avatar/sheila.glb`.
- Current dressing-room preview still uses a 2D/canvas glow preview; that must not be mistaken for the final performer experience.
- Next avatar gate: load Manny/Sheila GLBs in a real 3D viewport, inspect skeleton/rig names, confirm scale/origin/floor contact, and only then wire walking/parallax/hotspots.

### Validation Run This Session

- `python_compile_changed=passed` with Blender Python.
- `python_compile_all=passed` for all Python files outside `rubish`.
- `director_switcher_inline_js_parse=passed scripts=1` with Node.
- `changed_html_inline_js_parse=passed` for touched HTML inline scripts.
- `director_switcher_static_audit=passed`.
- `director_button_static_count=28 id_buttons=14`.
- `avatar_manifest_assets=passed ['assets/avatar/manny.glb', 'assets/avatar/sheila.glb']`.
- `recreate_bundle_smoke=passed`.
- `changed_html_local_reference_missing_count=0`.
- Static auth audit: `TOTAL_MUTATING_ROUTES=107`, `MISSING_AUTH_COUNT=0`.

### Validation That Could Not Run

- `pytest` could not run: Blender Python reports `No module named pytest`.
- FastAPI import/boot could not run: Blender Python reports `No module named fastapi`.
- Browser walkthrough could not run: Playwright browser tool failed with `Target page, context or browser has been closed`; Node `import('playwright')` failed because the package is not installed.
- Ollama Studio/Architect model checks were not run.
- Real camera, recording, mocap, export, and end-to-end show simulation were not run.

### Remaining Risks

- Console backend writes likely require auth; if opened without a valid moderator identity, routes will visibly fail and local fallback will continue.
- Export fetch currently calls a route that returns a file response. The UI treats the request as an export check/request, not a completed download workflow.
- Existing older pages still contain external URLs/CDNs/fonts. `director_switcher.html` is clean, but full offline frontend is not clean yet.
- Manny/Sheila GLBs are present but skeleton/rig quality is not inspected.
- Stage/map/avatar walking/parallax/floor contact/hotspots are not validated.
- Recreate bundle is defined but not yet emitted by recording/export.

### Exact Next First Action

Run the app in a Python environment that has FastAPI and pytest installed, then browser-test `/director-switcher` by clicking every visible control and watching Program/Preview, truth cards, panels, event log, and visible failure messages.

### Exact Next Commands

From `C:\Users\hardc\OneDrive\Documents\Playground\Pubcast codex run`:

```powershell
python -m pytest tests/test_recreate_bundle_contract.py tests/test_memory_foundation_codex.py
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Then open:

```text
http://127.0.0.1:8000/director-switcher
```

## Completed
- Seeded the isolated workspace at `C:\Users\hardc\OneDrive\Documents\Playground\Pubcast codex run` from `Pubcast Tuesday GitHub Upload 6.0`.
- Copied core app code, frontend, tests, lightweight root config/docs, and bot JSON files only.
- Excluded private Alex memory, BYOK master keys, emergency saves, large environment/media assets, caches, and Rust sources.
- Mounted the previously invisible memory, character, story, Pete enhanced, and personal memory routers during app lifespan startup.
- Added `UniversalMemorySystem.store()`, `get_recent()`, `search()`, SQLite write-through persistence, and `load_from_db()`.
- Added auth dependencies to mutating memory, character speak, and Pete enhanced control routes using the existing route security helpers.
- Added `ContextRetriever`, `PromptContextBuilder`, and `/api/personal-memory` routes.
- Hardened local two-brain defaults: Studio uses `ministral-pubcast:3b`, Architect uses `gemma4-compute-q5:e2b`, Studio keepalive defaults to `10m`, Architect keepalive defaults to `0`.
- Updated the orchestrator so Architect can use the Gemma Ollama wrapper with the existing timeout, fallback, semaphore, and circuit breaker behavior.
- Removed copied security-adjacent data files from the work folder; only bot config JSON remains under `data`.
- Stripped leading UTF-8 BOMs from copied Python files that blocked syntax validation in Blender Python.
- Completed a deeper mutating-route auth sweep. Static AST audit now reports 107 mutating routes total and only one intentionally public unguarded route: `POST /api/auth/login`.
- Added dependency-level auth guards to timeline controls, recording markers, hotspot triggers, waiting-room requests/approval/denial, avatar studio controls, ethereal avatar mutations, PubWorld hotspot actions, governance consent/request routes, vault mutations, and Alex message/grounding/reset routes.
- Added a first-person `/director-switcher` control surface inspired by the uploaded ornate switcher reference. It has working Program/Preview camera buttons, Cut, Fade, Record, Mark, Panic, Bars, presets, audio mute buttons, knobs, timecode, meters, and an event log. It attempts existing PubCast APIs where available and falls back locally so the buttons still click and respond.
- Updated global CSS tokens toward the requested Art Deco/Gatsby theater plus starship-control visual language: brass, walnut, velvet, cyan instrument glow, tighter radii, system fonts for offline use, richer panel treatments, and control-surface styling.
- Removed external Google Font dependencies from the primary touched surfaces: shared styles, stage, control room, waiting room, and dressing CSS.
- Moved generated `__pycache__` folders into `rubish/pycache_20260430_210541` instead of deleting them.

## Files Created
- `modules/context_retriever.py`: retrieves ranked memories by keyword overlap, importance, and recency.
- `modules/prompt_context_builder.py`: builds compact prompt memory context within a token budget.
- `modules/personal_ai_memory_api.py`: provides user-scoped personal memory write/query/recent routes.
- `tests/test_memory_foundation_codex.py`: focused tests for adapters, persistence, retrieval, prompt trimming, and personal-memory isolation.
- `static/director_switcher.html`: first-person director's switcher UI with responsive, clickable local controls.
- `HANDOFF.md`: this handoff.

## Files Updated
- `main.py`: wires new routers and runtime memory dependencies.
- `modules/universal_memory_system.py`: adds persistence and route API adapters.
- `modules/memory_routes.py`: supports sync or async memory stores and adds route identity dependency.
- `modules/character_routes.py`: adds identity dependency to character speak.
- `modules/pete_enhanced_routes.py`: adds mod-role dependencies to mutating Pete routes.
- `modules/appconfig.py`: adds two-brain model policy settings.
- `modules/llm_orchestrator.py`: adds Gemma Ollama Architect wrapper support and keepalive policy.
- `modules/alex_memory.py`: BOM-only cleanup from copied source.
- `modules/alex_routes.py`: added route-security dependencies to mutating Alex routes.
- `modules/avatar_studio_bridge.py`: added route-security dependencies to avatar studio mutations and architect planning.
- `modules/ethereal_avatars.py`: added identity dependencies to avatar create/edit/export routes.
- `modules/governance_routes.py`: added identity dependencies to consent and waiting-room request routes.
- `modules/governance_waiting_room.py`: added identity/mod-role dependencies to request, approve, and deny routes.
- `modules/hotspot_system.py`: added identity dependency to hotspot trigger route.
- `modules/pubcast_vault.py`: added mod-role dependencies to vault mutations.
- `modules/pubworld_hotspots.py`: added identity dependency to PubWorld hotspot actions.
- `modules/recording_pipeline_routes.py`: added mod-role dependency to recording marker writes.
- `modules/timeline.py` and `modules/timeline_routes.py`: added mod-role dependencies to timeline mutations.
- `static/css/main.css`, `static/styles.css`, `static/dressing.css`, `static/stage.html`, `static/control_room.html`, `static/waiting_room.html`: offline-font cleanup and art-direction polish.

## Validation
- `python` and `py` were unavailable as normal system Python commands.
- Found Blender Python at `C:\Program Files\Blender Foundation\Blender 5.1\5.1\python\bin\python.exe`.
- Targeted syntax check passed for all changed Python files using Blender Python and `compile(...)`.
- Full Python syntax sweep passed for all Python files in the run folder using Blender Python and `compile(...)`.
- Static mutating-route auth audit passed with one expected exception: `POST /api/auth/login`.
- Direct memory smoke test passed using Blender Python for:
  - memory store/recent/search adapters
  - SQLite reload from persisted memory
  - context retrieval ranking
  - prompt context building
- `pytest` could not run because Blender Python does not include the `pytest` package, and no installs were performed.
- `main.py` import could not be validated because Blender Python does not include `fastapi`.
- Frontend offline-resource audit still finds external references in older pages that were not part of this pass, including CDN Three.js on some 3D/avatar pages and Google Font links in builder/BYOK/index/launch/map/pubworld/stage_3d/stage_panoramic/studio_control/world pages.

## Left
- Run the full test suite once a Python environment with project dependencies is available.
- Start the FastAPI app and verify router registration at runtime.
- Vendor or replace remaining CDN dependencies for fully offline frontend operation. Highest priority: `static/avatar_studio_demo.html`, `static/pubworld_stage.html`, and `static/stage_3d.html` because they reference external Three.js.
- Browser-test `/director-switcher`, `/stage`, `/control-room` or `/static/control_room.html`, `/waiting-room`, and `/dressing` in a dependency-capable app run.
- Continue UI polish using the uploaded references as art direction: ornate director switcher, glowing foundry avatars, corkboard/call-sheet utility, Gatsby brass/walnut, and cyan starship instrumentation.
- Decide whether Pete Enhanced should stay mounted as an optional 503 route or be fully initialized behind a feature flag.

## First Action Next Session
Open `main.py` around the lifespan block and run the app with a dependency-capable Python environment. Confirm `[7h] Character/story/memory routes ready` appears in logs, then open `/director-switcher` and verify the switcher controls in-browser before running `pytest tests/test_memory_foundation_codex.py`.

## Notes
- One inaccessible Windows temp folder from the first failed Blender `tempfile` attempt still appears at `C:\Users\hardc\AppData\Local\Temp\tmp5_aaemn_`; cleanup was attempted but Windows denied access. No workspace artifacts remain from that failed attempt.
- No original source folder outside `Pubcast codex run` was modified.
- The newly uploaded March/April art, character, voxel, and asset-spec files were treated as reference material only during this pass; no large assets were copied into the run folder.

## Snapshot Created
- Created: 2026-05-02 16:30 local time.
- Filename: `PubCast_AI_v6_codex_run_snapshot_2026-05-02_1630.zip`
- Full path: `C:\Users\hardc\OneDrive\Documents\Playground\PUBCAST_AI_SNAPSHOTS\PubCast_AI_v6_codex_run_snapshot_2026-05-02_1630.zip`
- Size: 5,413,846 bytes / 5.16 MB.
- Approximate file count: 391 zip entries.
- Contents summary: full current `Pubcast codex run` workspace files, including `main.py`, `modules/`, `static/`, `tests/`, `docs/`, `HANDOFF.md`, `CODEX_RESUME_VOXEL_ENGINE_2026-05-02.md`, `data/avatars/manifest.json`, Manny/Sheila GLBs, `modules/recreate_bundle.py`, `docs/PUBCAST_RECREATE_BUNDLE.md`, `tests/test_recreate_bundle_contract.py`, `static/director_switcher.html`, Rust/voxel files, and workspace-local `rubish/` preserve folders.
- Excluded items: `data/byok/` was excluded as a secure BYOK subtree; literal `.env`, secret/token/secure-master folders, and `.pem`/`.key` files were excluded by snapshot filter. `.env.example` was included because it is a template.
- Validation result: PASS. Required entries confirmed present: `HANDOFF.md`, `static/director_switcher.html`, `modules/recreate_bundle.py`, `docs/PUBCAST_RECREATE_BUNDLE.md`, `tests/test_recreate_bundle_contract.py`, `data/avatars/manifest.json`, `assets/avatar/manny.glb`, `assets/avatar/sheila.glb`, and `CODEX_RESUME_VOXEL_ENGINE_2026-05-02.md`.
- Exact command used: PowerShell with `[System.IO.Compression.ZipFile]::Open(...)`, enumerating files under `C:\Users\hardc\OneDrive\Documents\Playground\Pubcast codex run` and filtering out secure/BYOK/env/key paths before adding entries to the archive.

## What Changed In This Pass
- Completed the required pre-work snapshot zip and verified required entries.
- Patched `modules/voxel_llm_adapter.py` so local deterministic voxel generation is the default and `httpx` is optional for cloud/HTTP providers only.
- Added `modules/voxel_set_contract.py`, a standard-library canonical voxel set contract with validation, save, load, and deterministic theater-platform proof-set construction.
- Added `tests/test_voxel_set_contract.py`.
- Created one deterministic proof set at `data/pubworld/voxel_sets/small_theater_platform_two_walls_doorway_codex_proof_20260502.json`.
- Patched `modules/voxel_asset_manager.py` to discover saved PubWorld voxel-set JSON files as voxel assets, even if the static asset-library JSON has not been generated by `main.py`.
- Removed safe Google Font dependencies from `static/builder.html` and `static/pubworld_stage.html`; fixed `static/builder.html` viewport markup from `<parameter>` to `<meta>`.
- Updated `CODEX_RESUME_VOXEL_ENGINE_2026-05-02.md` with the new voxel continuation status.

## Claim Verification Matrix
| Claim | Evidence | Verified by | Result | Follow-up needed |
| --- | --- | --- | --- | --- |
| Active workspace is `Pubcast codex run` | Required files present: `HANDOFF.md`, `static/director_switcher.html`, Manny/Sheila GLBs, voxel resume | `Test-Path`, directory listing | PASS | None |
| Parent git repo/branch verified | Root `C:/Users/hardc/OneDrive/Documents/Playground`, branch `april-branch-2` | `git rev-parse`, `git branch --show-current` | PASS | New `Wednesday` branch still pending |
| Snapshot exists and contains required files | 391 entries, 5.16 MB, required entries present | Zip entry inspection | PASS | Snapshot is pre-new-work recovery point; it intentionally does not include later code changes |
| Recreate bundle contract works | Manual test functions passed | Blender Python manual call | PASS | Run pytest when dependency-capable Python exists |
| Manny/Sheila are real GLB assets | GLB header valid, one scene, one mesh, one skin each, 90 nodes each | Node GLB JSON chunk parser | PASS | Browser/3D load and movement proof still pending |
| Manny walks | No local Three.js/GLTFLoader; browser runtime not run | Static asset scan | NOT RUN | Need local loader/runtime or dependency-capable browser proof |
| Director switcher route exists | `/director-switcher` route in `main.py`; links in Control, Control Room, Stage | `rg` route/link scan | PASS (static) | Browser click proof pending |
| Director switcher controls are wired locally | 14 ID buttons referenced by JS; Program/Preview/Cut/Fade/Record/Panic/truth panels detected | Node static audit | PASS (static) | Browser click proof pending |
| FastAPI app boots | `fastapi` missing in available Python | Blender Python import attempt | NOT RUN / BLOCKED | Need dependency-capable Python |
| Rust voxel/renderer crate compiles | `cargo check --manifest-path rust_crate\Cargo.toml` completed | Cargo | PASS with warnings | Add `full-animation` feature or remove cfg; clean unused imports |
| Voxel engine defaults are engine-first | `generate_with_cloud` default is `local`; builder/stage use `pubworld_rules`; local generation returned provider `local` | Code search and manual adapter test | PASS | Run FastAPI endpoint test later |
| Deterministic voxel proof set round-trips | 48-block theater platform saved and reloaded | Blender Python manual test | PASS | Browser/stage rendering proof pending |
| Voxel proof set is exposed as asset | Asset manager discovered saved proof set with 48 voxels | Blender Python manual manager test | PASS | Verify `/api/voxel/assets` after FastAPI boot |

## Python/FastAPI/Pytest Environment Results
- Repo-local `.venv`: not present.
- `py -3.11 --version`: `No installed Python found!`
- `py -3.12 --version`: `No installed Python found!`
- `python --version`: command not found.
- `where python`: no Python found.
- `where py`: `C:\Windows\py.exe`, but launcher has no registered Python.
- `where conda`: no conda found.
- Broad conda search timed out before finding a usable environment.
- Blender Python: `Python 3.13.9` at `C:\Program Files\Blender Foundation\Blender 5.1\5.1\python\bin\python.exe`.
- Blender Python missing packages: `pytest`, `fastapi`, `starlette`, `pydantic`, `httpx`.
- `fastapi_import=FAIL ModuleNotFoundError No module named 'fastapi'`.
- `main_import=FAIL ModuleNotFoundError No module named 'fastapi'`.
- `pytest` could not run. Manual stdlib tests were run instead where possible.
- Exact next dependency command for the user/next agent, in a dependency-capable environment:
  `python -m pytest tests/test_recreate_bundle_contract.py tests/test_voxel_set_contract.py tests/test_frontend_startup_contracts.py tests/test_frontend_contract_compat.py`

## Director Switcher Verification
- Static route: PASS. `main.py` defines `GET /director-switcher`.
- Static navigation: PASS. Links found in `static/control.html`, `static/control_room.html`, and `static/stage.html`.
- Inline JS syntax: PASS via Node.
- Static controls detected: Program, Preview, Cut, Fade, Record, Panic/Stop, event log, truth cards, public chat, control chat, and status/danger panel.
- Button reference audit: PASS static. ID buttons are referenced in script.
- Honest labels: PASS static. Monitors and truth cards repeatedly label local/unavailable/backend-unproven states.
- Browser click proof: NOT RUN because FastAPI cannot boot in the available Python environment.

## Manny/Sheila GLB Verification
- `assets/avatar/manny.glb`: PASS. Valid GLB 2.0, declared length matches file length, 1 scene, 1 mesh, 1 skin, 90 nodes.
- Manny skin: `MANNY_armature`.
- Manny first nodes include `MANNY_mesh`, `Root`, `Pelvis`, spine, neck, head, jaw, arm/hand bones.
- Manny embedded animations: 0.
- `assets/avatar/sheila.glb`: PASS. Valid GLB 2.0, declared length matches file length, 1 scene, 1 mesh, 1 skin, 90 nodes.
- Sheila skin: `SHEILA_armature`.
- Sheila embedded animations: 0.
- Manifest policy: PASS. Both assets are `asset_type: glb`, `performer_asset: true`, `production_avatar: true`, and `sprite_replacement_allowed: false`.

## Manny Walk Proof Status
- Status: NOT RUN / BLOCKED.
- Reason: no local Three.js/GLTFLoader/model-viewer runtime was found in `static/` or `assets/`; existing 3D/avatar pages reference CDN loaders.
- No fake walk proof was created.
- Required next proof: load Manny GLB in a real local 3D/browser surface, place him on a visible floor, move him via W/A/S/D or arrows, display/log position, and label animation status honestly.

## Voxel/PubWorld Inventory
| File path | Purpose | Status | Dependencies | Next action |
| --- | --- | --- | --- | --- |
| `modules/voxel_set_contract.py` | Canonical JSON-compatible voxel set contract | Working in manual tests | stdlib only | Integrate into FastAPI endpoints after boot proof |
| `tests/test_voxel_set_contract.py` | Contract tests for deterministic proof set | Working manually; pytest not run | pytest for formal run | Run with pytest later |
| `data/pubworld/voxel_sets/small_theater_platform_two_walls_doorway_codex_proof_20260502.json` | Deterministic proof set | Working; saved/reloaded | stdlib contract | Render/load in stage after browser proof |
| `modules/voxel_llm_adapter.py` | AI-optional prompt-to-block adapter | Local default working manually | httpx only for cloud/HTTP paths | Endpoint test after FastAPI boot |
| `modules/voxel_asset_manager.py` | Voxel asset catalog and bridge sync | Partial; discovers saved voxel sets | stdlib; optional bridge | Verify via `/api/voxel/assets` after boot |
| `modules/pubworld_blocks.py` | PubWorld prop/block/preset manager | Unknown in runtime here | Pydantic missing in Blender Python | Test in real Python env |
| `static/builder.html` | Scene builder UI | Partial; JS parses; prompt route wired | Backend required | Browser-test buttons and asset list |
| `static/pubworld_stage.html` | Voxel stage UI | Partial; JS parses; prompt route wired | CDN Three.js currently required | Replace/vendor Three.js locally, then browser-test |
| `rust_crate/` | Rust animation/renderer bridge crate | `cargo check` PASS with warnings | Rust/Cargo | Address warnings, then run renderer locally |
| `bin/ws_renderer` | Renderer binary/script from 5.6 zip | Present, not runtime-proven | Unknown runtime | Run only after app/bridge path is ready |

## Voxel Set Contract
- Canonical contract implemented in `modules/voxel_set_contract.py`.
- Contract includes: `asset_id`, `name`, `version`, `units`, `dimensions`, `origin`, `blocks`, `materials`, `created_by`, `source_prompt`, and `safety`.
- Safety includes: `ai_authority`, `approved_by_user`, and `sandbox_only`.
- Authority values preserved: `off`, `advisory`, `planning`, `assigned_role`, `supervised_operator`, `recovery`, `freeplay_sandbox`.
- Validation rejects duplicate block coordinates.
- Validation requires dimensions to contain all blocks.
- Validation requires materials to resolve unless material is `default`.
- Empty set is allowed only with `draft: true`.
- Save path is non-destructive: if a voxel set file already exists, `save_voxel_set` raises `FileExistsError` instead of overwriting.

## Voxel End-to-End Test Set Result
- Test set: "small theater platform with two side walls and a doorway".
- Saved path: `data/pubworld/voxel_sets/small_theater_platform_two_walls_doorway_codex_proof_20260502.json`.
- Blocks: 48.
- Dimensions: `{"x": 7, "y": 3, "z": 5}`.
- Created by: `builder`.
- AI authority: `off`.
- Sandbox only: `true`.
- Save/reload: PASS.
- Asset manager exposure: PASS static/manual. The set is discovered as asset `small_theater_platform_two_walls_doorway_codex_proof_20260502` with category `props`.
- Rendering/stage proof: NOT RUN.

## Builder UI/Route Status
- `static/builder.html` inline script parses.
- Prompt-generated set button calls `POST /api/pubworld/props/generate`.
- Builder now sends provider `pubworld_rules`.
- Asset library attempts `/api/voxel/assets` first and falls back locally.
- Google Font dependency removed.
- Invalid viewport tag fixed.
- Runtime route/browser proof: NOT RUN because FastAPI cannot boot here.

## PubWorld Integration Status
- PubWorld props/generate routes exist in `main.py`, but were not runtime-tested due missing FastAPI/Pydantic environment.
- Saved canonical voxel sets are now discoverable through `VoxelAssetManager`.
- Minimum current state: PubWorld-compatible data exists and can be saved/reloaded/exposed to the asset manager locally.
- Remaining gap: prove `/api/voxel/assets`, `/api/pubworld/props/generate`, builder display, stage display, and renderer bridge in a real booted app.

## Recreate Bundle Verification
- `modules/recreate_bundle.py`: manual contract tests PASS.
- JSON serialization/reload: PASS.
- Avatar identity and GLB path survive serialization: PASS.
- Readiness explains incomplete status: PASS.
- Partial end-to-end simulation with camera events, Manny avatar, audio metadata, unavailable mocap marker, and voxel asset reference: PASS.
- Honest simulation readiness result: `not_ready: missing final program video/export reference`.
- Voxel references are represented as system warning payload metadata in the current simulation; no first-class `voxel_asset_refs` field exists yet.

## Route/Auth Audit
- Static mutating route count from broad AST scan: 110.
- Initial crude missing-auth count: 6.
- Inspection result: 1 is expected public login (`modules/auth_routes.py`, `POST /api/auth/login` as mounted under `/api/auth/login`); 5 BYOK routes use `Depends(_get_current_user_id)`.
- Important caveat: `_get_current_user_id` is session identity, not role/access-control. BYOK code comments say write endpoints require authenticated session tokens, but the helper currently creates a cookie identity if absent. This is sensitive and should be reviewed in a dependency-capable security pass before market/shipping.
- No BYOK secrets or secure master keys were read or exposed.

## Offline Dependency Scan
- Safe removals completed:
  - Removed Google Fonts from `static/builder.html`.
  - Removed Google Fonts from `static/pubworld_stage.html`.
- Remaining high-priority external runtime dependency:
  - `static/pubworld_stage.html` still loads `https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js`.
- Other existing external references remain in older/static pages and provider docs. They were scanned but not all changed in this pass because some are intentional provider/API links and some 3D pages need local replacements, not blind removal.

## Error Handling Pass
- `modules/voxel_llm_adapter.py`: `httpx` import is optional now; local generation no longer fails when HTTP/cloud deps are missing.
- Cloud/HTTP provider methods log and skip if `httpx` is unavailable.
- `modules/voxel_asset_manager.py`: saved voxel-set discovery logs invalid files and skips them instead of failing the whole manager.
- `save_voxel_set` refuses to overwrite existing set files.
- No deletion cleanup was performed.

## End-to-End Simulation Status
| Step | Result | Evidence |
| --- | --- | --- |
| Import app | FAIL/BLOCKED | `fastapi` missing |
| Boot app | NOT RUN | blocked by missing FastAPI |
| Open `/director-switcher` | NOT RUN | app not booted |
| Verify director switcher route | PASS static | route/link scan |
| Open Studio/Control Room/Stage | NOT RUN | app/browser not booted |
| Load avatar manifest | PASS | manual JSON validation |
| Verify Manny/Sheila GLB paths | PASS | files exist and GLB headers parse |
| Trigger/simulate Preview camera select | PARTIAL | recreate bundle simulation camera event |
| Trigger/simulate Take/Cut | PARTIAL | recreate bundle simulation cut event |
| Trigger/simulate recording start/stop | NOT RUN | backend/browser not booted |
| Create recreate bundle from simulated events | PASS | manual stdlib simulation |
| Serialize recreate bundle | PASS | JSON reload succeeded |
| Event log includes camera/avatar/recreate/voxel metadata | PARTIAL | simulated bundle has camera/avatar/voxel references; no real UI log |
| Create deterministic voxel theater platform set | PASS | contract builder |
| Validate/save/reload voxel set | PASS | manual contract proof |
| Register/expose voxel set to asset manager | PASS | asset manager discovered proof set |
| Load Manny GLB in avatar test surface | NOT RUN | no local GLTF loader |
| Walk Manny on floor | NOT RUN | no local GLTF loader/browser proof |

## Remaining Ship Blockers
- Dependency-capable Python environment is still missing; FastAPI boot and pytest are not proven.
- Browser proof of `/director-switcher` is not run.
- Manny walk proof is not run and must not be claimed.
- Local Three.js/GLTFLoader or equivalent GLB runtime is missing; CDN dependency remains on 3D pages.
- PubWorld/voxel routes are statically wired but not runtime-tested.
- Recording/export/mocap remain local/simulated/unavailable unless backend proof is run.
- BYOK route identity/auth semantics need a careful review before shipping.
- Rust crate passes `cargo check` but has warnings about undeclared `full-animation` cfg and unused imports.
- Snapshot zip is a pre-work safety snapshot; it does not include later code changes unless another snapshot is created.

## Exact Next Commands
```powershell
cd "C:\Users\hardc\OneDrive\Documents\Playground\Pubcast codex run"
python -m pytest tests/test_recreate_bundle_contract.py tests/test_voxel_set_contract.py tests/test_frontend_startup_contracts.py tests/test_frontend_contract_compat.py
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Then open:
```text
http://127.0.0.1:8000/director-switcher
http://127.0.0.1:8000/static/builder.html
http://127.0.0.1:8000/static/pubworld_stage.html
```

## Exact Next Files To Open
- `main.py`
- `static/director_switcher.html`
- `static/builder.html`
- `static/pubworld_stage.html`
- `modules/voxel_set_contract.py`
- `modules/voxel_asset_manager.py`
- `modules/voxel_llm_adapter.py`
- `modules/recreate_bundle.py`
- `data/avatars/manifest.json`
- `data/pubworld/voxel_sets/small_theater_platform_two_walls_doorway_codex_proof_20260502.json`

## Recommended Next 3 Tasks
1. Establish a dependency-capable local Python environment without overwriting globals, then run pytest and boot FastAPI.
2. Vendor or otherwise provide a safe local Three.js/GLTFLoader runtime, then prove Manny loads and moves in a real browser surface.
3. Browser-test the boring voxel loop: `/api/voxel/assets` exposes the proof set, builder lists/places it, PubWorld/stage can display it, and failures are visible.

## Git Branch / Push Status
- Repo path: `C:\Users\hardc\OneDrive\Documents\Playground`
- Active workspace path: `C:\Users\hardc\OneDrive\Documents\Playground\Pubcast codex run`
- Remote: `origin https://github.com/whatsupjosie/dreams.git`
- Original branch: `april-branch-2`
- Requested new branch: `Wednesday`
- Branch creation result: FAIL / BLOCKED.
- Commit result: NOT RUN because branch creation and index writes are blocked.
- Push result: NOT RUN because no branch/commit could be created.
- Commit hash: none.
- Staged/committed files: none from this pass.
- Forbidden files included: none; no commit was created and no push occurred.
- Current workspace state reviewed: yes. Parent repo is very dirty with many unrelated untracked files outside `Pubcast codex run`; there is also a pre-existing staged file outside the approved workspace: `_archive_review_batch2/avatar_loook_hoplogram/hologram_avatar_generator.py`.
- Safety action attempted: tried to unstage the unrelated staged file non-destructively with:
  `git restore --staged -- "_archive_review_batch2/avatar_loook_hoplogram/hologram_avatar_generator.py"`
- Exact unstage error:
  `fatal: Unable to create 'C:/Users/hardc/OneDrive/Documents/Playground/.git/index.lock': Permission denied`
- Exact branch command attempted:
  `git checkout -b Wednesday`
- Exact branch error:
  `fatal: cannot lock ref 'refs/heads/Wednesday': Unable to create 'C:/Users/hardc/OneDrive/Documents/Playground/.git/refs/heads/Wednesday.lock': Permission denied`
- Evidence checked:
  - `.git/index.lock` does not exist.
  - `.git/index` is not read-only.
  - `.git` ACL output contains explicit deny/write entries for sandbox-related SIDs, so this session cannot write Git lock files.
- Required next Git command after permissions are fixed:
  ```powershell
  cd "C:\Users\hardc\OneDrive\Documents\Playground"
  git restore --staged -- "_archive_review_batch2/avatar_loook_hoplogram/hologram_avatar_generator.py"
  git checkout -b Wednesday
  git add -- "Pubcast codex run/HANDOFF.md" "Pubcast codex run/CODEX_RESUME_VOXEL_ENGINE_2026-05-02.md" "Pubcast codex run/main.py" "Pubcast codex run/modules/voxel_asset_manager.py" "Pubcast codex run/modules/voxel_llm_adapter.py" "Pubcast codex run/modules/voxel_set_contract.py" "Pubcast codex run/static/builder.html" "Pubcast codex run/static/pubworld_stage.html" "Pubcast codex run/tests/test_voxel_set_contract.py" "Pubcast codex run/data/pubworld/voxel_sets/small_theater_platform_two_walls_doorway_codex_proof_20260502.json"
  git status --short
  git commit -m "PubCast AI Wednesday safety snapshot and proof pass"
  git push -u origin Wednesday
  ```
- Do not use broad `git add "Pubcast codex run"` unless caches, secure directories, and generated pyc files are explicitly excluded.
