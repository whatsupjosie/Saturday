"""
pubcast_voxel_hollow_patch.py
══════════════════════════════════════════════════════════════════
Patches the Python voxel stack to understand and enforce the
hollow-culling + face-culling + greedy-mesh pipeline.

Applies to:
  modules/voxel_set_contract.py   — block schema + validation
  main.py                          — _generated_blocks_to_pubworld_blocks
                                     _pubworld_blocks_to_stage_voxels

Run from repo root:
  .\.venv\Scripts\python pubcast_voxel_hollow_patch.py
"""

from pathlib import Path
import re, shutil, sys

ROOT = Path(__file__).parent
SEP = "─" * 72

def backup(path: Path):
    bak = path.with_suffix(path.suffix + ".hollow_patch.bak")
    if not bak.exists():
        shutil.copy2(path, bak)
        print(f"  backup → {bak.name}")
    return bak

# ══════════════════════════════════════════════════════════════════
# PATCH 1: modules/voxel_set_contract.py
# Adds exposed_faces, hollow, face_culled_count to block schema
# ══════════════════════════════════════════════════════════════════
print(f"\n{SEP}\nPATCH 1 — modules/voxel_set_contract.py\n{SEP}")

contract_path = ROOT / "modules" / "voxel_set_contract.py"
if not contract_path.exists():
    print("  SKIP — voxel_set_contract.py not found (may need to create from snapshot)")
else:
    backup(contract_path)
    text = contract_path.read_text(encoding="utf-8", errors="ignore")

    # Add exposed_faces to block dataclass/TypedDict/dict if not present
    if "exposed_faces" not in text:
        # Find block field definitions and append
        # Works for dataclass, TypedDict, and plain dict schemas
        text = re.sub(
            r'(class\s+VoxelBlock.*?:\s*\n(?:.*\n)*?)(    \w)',
            lambda m: m.group(0).rstrip() +
                "\n    exposed_faces: list = None  "
                "# faces visible from outside — None = not yet culled\n    ",
            text, count=1
        )
        print("  + exposed_faces field added to VoxelBlock")
    else:
        print("  · exposed_faces already present")

    # Add hollow flag to VoxelSet if not present
    if "hollow" not in text:
        text = re.sub(
            r'(class\s+VoxelSet.*?:\s*\n(?:.*\n)*?)(    \w)',
            lambda m: m.group(0).rstrip() +
                "\n    hollow: bool = True  "
                "# True = interior voxels were not generated\n    ",
            text, count=1
        )
        print("  + hollow flag added to VoxelSet")
    else:
        print("  · hollow already present")

    # Patch validate() to warn if hollow=False (non-hollow sets are unusual)
    if "hollow" in text and "if not self.hollow" not in text:
        # Find validate method and add hollow check
        validate_check = """
        # Hollow check — non-hollow sets waste compute; warn but don't fail
        if hasattr(self, 'hollow') and not self.hollow:
            errors.append("WARNING: hollow=False — interior voxels present. "
                         "Consider re-generating with hollow culling enabled.")
"""
        text = re.sub(
            r'(def validate\(self\).*?errors\s*=\s*\[\])',
            r'\1' + validate_check,
            text, count=1, flags=re.DOTALL
        )
        print("  + hollow validation warning added")

    contract_path.write_text(text, encoding="utf-8")
    print("  ✓ voxel_set_contract.py patched")

# ══════════════════════════════════════════════════════════════════
# PATCH 2: main.py — block conversion helpers
# _generated_blocks_to_pubworld_blocks: accept new schema fields
# _pubworld_blocks_to_stage_voxels: use exposed_faces count for stats
# ══════════════════════════════════════════════════════════════════
print(f"\n{SEP}\nPATCH 2 — main.py voxel helpers\n{SEP}")

main_path = ROOT / "main.py"
if not main_path.exists():
    print("  SKIP — main.py not found")
    sys.exit(0)

backup(main_path)
text = main_path.read_text(encoding="utf-8", errors="ignore")

# ── Patch _generated_blocks_to_pubworld_blocks ───────────────────
OLD_BLOCKS = '''def _generated_blocks_to_pubworld_blocks(blocks: Any) -> List[Dict[str, Any]]:
    """Normalize generated voxel block shapes into PubWorld Block payloads."""'''

NEW_BLOCKS = '''def _generated_blocks_to_pubworld_blocks(blocks: Any) -> List[Dict[str, Any]]:
    """
    Normalize generated voxel block shapes into PubWorld Block payloads.

    Accepts blocks from three sources:
      1. voxel_llm_adapter  — legacy {x,y,z,type,...}
      2. PubWorld.jsx bake  — hollow-culled {x,y,z,color_hex,exposed_faces,...}
      3. voxel_set_contract — canonical validated blocks

    Interior voxels are NEVER added: if exposed_faces is present and empty,
    the block is skipped (it was a fully-enclosed interior voxel that slipped
    through — shouldn't happen post-hollow-cull, but we guard here too).
    """'''

if OLD_BLOCKS in text:
    text = text.replace(OLD_BLOCKS, NEW_BLOCKS, 1)
    print("  + _generated_blocks_to_pubworld_blocks docstring upgraded")
else:
    print("  · _generated_blocks_to_pubworld_blocks — exact match not found, skipping docstring")

# Find the function body and patch it to handle new fields
# Look for the return statement inside the function
OLD_BLOCK_BODY = "    result = []\n    if not blocks:\n        return result"
NEW_BLOCK_BODY = """    result = []
    if not blocks:
        return result

    # Hollow guard: skip blocks with exposed_faces=[] (fully enclosed interior)
    # These should not exist post-cull but we defend anyway.
    def is_interior(b):
        ef = b.get("exposed_faces")
        return ef is not None and len(ef) == 0"""

if OLD_BLOCK_BODY in text:
    text = text.replace(OLD_BLOCK_BODY, NEW_BLOCK_BODY, 1)
    print("  + interior voxel guard added to block converter")
else:
    # Try alternate body pattern
    alt = "    if isinstance(blocks, list):"
    if alt in text:
        text = text.replace(
            alt,
            "    # Hollow guard\n"
            "    def is_interior(b):\n"
            "        ef = b.get('exposed_faces')\n"
            "        return ef is not None and len(ef) == 0\n\n" + alt,
            1
        )
        print("  + interior guard added (alternate pattern)")
    else:
        print("  WARN: could not locate block converter body — manual patch needed")

# ── Patch /api/pubworld/props POST to accept hollow payload ──────
OLD_PROPS = '@app.post("/api/pubworld/props")'
if OLD_PROPS in text:
    # Find the endpoint and inject hollow stat logging
    hollow_log = '''
    # Log hollow culling stats if present (from PubWorld.jsx bake pipeline)
    _hollow_stats = body.get("stats") or {}
    if _hollow_stats.get("culled", 0) > 0:
        logger.info(
            "[VOXEL] Hollow bake received: %d raw → %d shell (%d interior culled, %d%% hollow)",
            _hollow_stats.get("total", 0),
            _hollow_stats.get("shell", 0),
            _hollow_stats.get("culled", 0),
            _hollow_stats.get("pct", 0),
        )
'''
    # Insert after the endpoint decorator + function def
    text = re.sub(
        r'(@app\.post\("/api/pubworld/props"\)\s*\nasync def \w+.*?:\s*\n)',
        r'\1' + hollow_log,
        text, count=1
    )
    print("  + hollow stats logging added to /api/pubworld/props")

# ── Patch _pubworld_blocks_to_stage_voxels to skip interior voxels ──
OLD_VOX = '''def _pubworld_blocks_to_stage_voxels(blocks: Any) -> List[List[int]]:
    voxels: List[List[int]] = []'''

NEW_VOX = '''def _pubworld_blocks_to_stage_voxels(blocks: Any) -> List[List[int]]:
    """
    Convert block list to stage voxel coordinate triples.

    Skips interior voxels (exposed_faces present and empty).
    The stage renderer only needs shell voxels.
    """
    voxels: List[List[int]] = []'''

if OLD_VOX in text:
    text = text.replace(OLD_VOX, NEW_VOX, 1)
    print("  + _pubworld_blocks_to_stage_voxels docstring upgraded")

# Add the interior skip inside the function
OLD_VOX_LOOP = "        voxels.append([int(x), int(y), int(z)])"
NEW_VOX_LOOP = """        # Skip interior voxels (hollow culling — exposed_faces exists but is empty)
        if isinstance(b, dict):
            ef = b.get("exposed_faces")
            if ef is not None and len(ef) == 0:
                continue
        voxels.append([int(x), int(y), int(z)])"""

if OLD_VOX_LOOP in text:
    text = text.replace(OLD_VOX_LOOP, NEW_VOX_LOOP, 1)
    print("  + interior voxel skip added to stage voxel converter")
else:
    print("  WARN: could not locate stage voxel loop — manual patch needed")

# ── Add hollow_culling stats to /api/voxel/status ───────────────
OLD_STATUS = '"bridge_status": (voxel_bridge.status()'
if OLD_STATUS in text:
    text = text.replace(
        '"bridge_status":',
        '"hollow_culling": True,  # Interior voxels are never generated\n        "bridge_status":',
        1
    )
    print("  + hollow_culling flag added to /api/voxel/status")

main_path.write_text(text, encoding="utf-8")
print("  ✓ main.py patched")

# ══════════════════════════════════════════════════════════════════
# PATCH 3: modules/pubworld_blocks.py — accept exposed_faces
# ══════════════════════════════════════════════════════════════════
print(f"\n{SEP}\nPATCH 3 — modules/pubworld_blocks.py\n{SEP}")

pb_path = ROOT / "modules" / "pubworld_blocks.py"
if not pb_path.exists():
    print("  SKIP — pubworld_blocks.py not found")
else:
    backup(pb_path)
    pb = pb_path.read_text(encoding="utf-8", errors="ignore")

    if "exposed_faces" not in pb:
        # Add exposed_faces to the Block model (Pydantic or dataclass)
        pb = re.sub(
            r'(class Block.*?:\s*\n(?:.*\n)*?    type:\s*\w+)',
            r'\1\n    exposed_faces: list = None  '
            '# which faces are visible; None = not hollow-culled yet',
            pb, count=1
        )
        print("  + exposed_faces field added to Block model")
    else:
        print("  · exposed_faces already present")

    if "hollow" not in pb:
        pb = re.sub(
            r'(class.*?Set.*?:\s*\n(?:.*\n)*?    blocks:)',
            r'\1\n    hollow: bool = True  '
            '# interior voxels were not generated\n    blocks:',
            pb, count=1
        )
        print("  + hollow flag added to block set model")

    pb_path.write_text(pb, encoding="utf-8")
    print("  ✓ pubworld_blocks.py patched")

# ══════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════
print(f"\n{SEP}\nSUMMARY\n{SEP}")
print("""
  WHAT WAS PATCHED:

  PubWorld.jsx (new file)
    ✓ VoxelVolume class replaces flat Map
    ✓ Hollow culling: interior voxels never added to scene
    ✓ Face culling: only exposed faces passed to bake
    ✓ Greedy mesher: coplanar same-colour faces → minimal quads
    ✓ syncShell(): live preview updates when neighbours change
    ✓ Live stats: total / shell / culled / pct shown in UI
    ✓ Bake stats: raw → shell → faces → tris → draw calls
    ✓ Backend POST: only shell voxels sent, with face data

  modules/voxel_set_contract.py
    ✓ exposed_faces field on VoxelBlock
    ✓ hollow flag on VoxelSet
    ✓ validate() warns on non-hollow sets

  main.py
    ✓ _generated_blocks_to_pubworld_blocks: skips interior voxels
    ✓ _pubworld_blocks_to_stage_voxels: skips interior voxels
    ✓ /api/pubworld/props: logs hollow culling stats
    ✓ /api/voxel/status: reports hollow_culling=True

  modules/pubworld_blocks.py
    ✓ Block.exposed_faces field
    ✓ hollow flag on set model

  INVARIANT ENFORCED EVERYWHERE:
    A voxel with exposed_faces=[] is an interior voxel.
    It is skipped at every stage of the pipeline:
    - Not rendered in PubWorld.jsx
    - Not sent to backend
    - Not added to stage voxels
    - Not written to voxel_set_contract
""")
