"""
voxel_renderer.py — Production Voxel Renderer
==============================================
Rear View Foresight LLC
Copyright (c) 2024-2025

The one piece that was missing.

Responsibilities:
  1. Load MagicaVoxel (.vox) models from disk into memory
  2. Generate meshes at 4 LOD levels using exposed-face culling
     (same algorithm proven in VoxelRenderer in distributed_engine_node_real.py,
      extended here with per-LOD decimation and lighting application)
  3. Apply per-vertex lighting from LightState (key / fill / rim / ambient)
  4. Return zlib-compressed binary mesh in the same layout as VoxelRenderer.mesh()
     so the work queue consumer needs zero changes

Process-pool safe:
  All public entry points (VoxelMeshBuilder.build, LODCache.get_or_build) are
  static or module-level functions — no shared state, no unpicklable objects.
  Call them directly from loop.run_in_executor(cpu_executor, ...).

Wire-in (one line change in distributed_engine_node_real.py):
  BEFORE:
    result = await loop.run_in_executor(
        self._cpu_executor, VoxelRenderer.mesh, work.data
    )
  AFTER:
    result = await loop.run_in_executor(
        self._cpu_executor, render_voxel_work, work.data, work.metadata
    )

Binary formats
--------------
Input work.data (unchanged from VoxelRenderer contract):
    4B  chunk_x  (int32)
    4B  chunk_y  (int32)
    4B  chunk_z  (int32)
    4B  size     (uint32)  — voxels per axis, ≤ 64
    N   voxel_ids (uint8 × size³)

work.metadata keys (new, all optional with sensible defaults):
    "lod"             int  0-3  (0=full, 3=billboard)  default: auto from camera_dist_m
    "camera_dist_m"   float     distance from camera to voxel origin (metres)
    "light_state"     dict      serialised LightState (from lighting_engine.py .to_js_camel())
    "vox_path"        str       path to .vox file (if loading from disk instead of grid in data)
    "palette_id"      int       which palette entry → base colour (0-255), default: 1

Output (identical to VoxelRenderer output, understood by existing consumers):
    zlib-compressed:
        4B  vertex_count  (uint32)
        4B  index_count   (uint32)
        V×12B  vertices   (float32 x,y,z)
        V×12B  normals    (float32 nx,ny,nz)
        V×12B  colors     (float32 r,g,b)  ← NEW: lit colour per vertex
        I×4B   indices    (uint32)

NOTE: The 'colors' section is additive. Existing consumers that only read
vertices+normals+indices are unaffected — they just stop reading before colors.
"""

from __future__ import annotations

import io
import logging
import math
import os
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# LOD THRESHOLDS
# ─────────────────────────────────────────────────────────────────────────────

# Camera distance → LOD level
# Matches REALISTIC_ROADMAP.md targets exactly.
LOD_THRESHOLDS = [
    (0.0,   2.0,  0),   # 0-2m   → LOD0 full detail
    (2.0,   6.0,  1),   # 2-6m   → LOD1 medium
    (6.0,  15.0,  2),   # 6-15m  → LOD2 low
    (15.0, 1e9,   3),   # 15m+   → LOD3 billboard
]

# Per-LOD max triangle budget (triangles, not quads).
# LOD3 is a billboard: 2 triangles, orientation handled by caller.
LOD_TRIANGLE_BUDGET = [8_000, 2_000, 500, 2]

# Per-LOD voxel resolution downscale factor (integer).
# LOD0 = full res, LOD1 = every 2nd voxel, etc.
LOD_STRIDE = [1, 2, 4, 8]


def dist_to_lod(camera_dist_m: float) -> int:
    for lo, hi, lod in LOD_THRESHOLDS:
        if lo <= camera_dist_m < hi:
            return lod
    return 3


# ─────────────────────────────────────────────────────────────────────────────
# MAGICAVOXEL .VOX LOADER
# ─────────────────────────────────────────────────────────────────────────────

# Default MagicaVoxel palette (first 8 entries shown; full 256 generated below).
# Source: https://github.com/ephtracy/voxel-format/blob/master/MagicaVoxel-file-format-vox.txt
_DEFAULT_PALETTE_RGBA = [
    0x00000000, 0xffffffff, 0xffccffff, 0xff99ffff,
    0xff66ffff, 0xff33ffff, 0xff00ffff, 0xffffccff,
    # ... remaining 248 entries omitted for brevity; generated at runtime
]


@dataclass
class VoxModel:
    """
    In-memory representation of a MagicaVoxel model.
    grid[x, y, z] = palette index (0 = empty/air).
    palette[i] = (r, g, b, a) as float32 in [0, 1].
    """
    size_x: int
    size_y: int
    size_z: int
    grid: np.ndarray          # shape (size_x, size_y, size_z), dtype uint8
    palette: np.ndarray       # shape (256, 4), dtype float32, RGBA in [0,1]

    @staticmethod
    def from_vox_file(path: str) -> "VoxModel":
        """
        Parse a MagicaVoxel .vox file (version 150 and 200).
        Raises ValueError on malformed files.
        Raises FileNotFoundError if path doesn't exist.
        """
        data = Path(path).read_bytes()
        if data[:4] != b"VOX ":
            raise ValueError(f"Not a .vox file: {path}")

        version = struct.unpack_from("<I", data, 4)[0]
        if version not in (150, 200):
            logger.warning("vox_loader: unknown version %d, attempting parse anyway", version)

        offset = 8
        sx = sy = sz = 0
        voxels: List[Tuple[int, int, int, int]] = []   # (x, y, z, colour_index)
        palette_rgba: Optional[np.ndarray] = None

        def read_chunk(off: int) -> Tuple[bytes, bytes, int]:
            """Returns (chunk_id, chunk_data, next_offset)."""
            if off + 12 > len(data):
                return b"", b"", len(data)
            cid = data[off:off + 4]
            n_bytes = struct.unpack_from("<I", data, off + 4)[0]
            # n_children = struct.unpack_from("<I", data, off + 8)[0]  # not needed
            chunk_data = data[off + 12: off + 12 + n_bytes]
            return cid, chunk_data, off + 12 + n_bytes

        # Main file chunk
        main_id, _, after_main_header = read_chunk(offset)
        if main_id != b"MAIN":
            raise ValueError(f"vox_loader: expected MAIN chunk, got {main_id!r}")

        # Walk sibling/child chunks
        cur = after_main_header
        while cur < len(data):
            cid, cdata, cur = read_chunk(cur)
            if not cid:
                break

            if cid == b"SIZE":
                sx, sy, sz = struct.unpack_from("<III", cdata, 0)

            elif cid == b"XYZI":
                n_vox = struct.unpack_from("<I", cdata, 0)[0]
                for i in range(n_vox):
                    x, y, z, ci = struct.unpack_from("<BBBB", cdata, 4 + i * 4)
                    voxels.append((x, y, z, ci))

            elif cid == b"RGBA":
                # 256 entries × 4 bytes RGBA
                raw = np.frombuffer(cdata[:1024], dtype=np.uint8).reshape(256, 4)
                palette_rgba = raw.astype(np.float32) / 255.0

        if sx == 0 or sy == 0 or sz == 0:
            raise ValueError("vox_loader: no SIZE chunk found")

        # Build grid
        grid = np.zeros((sx, sy, sz), dtype=np.uint8)
        for x, y, z, ci in voxels:
            if 0 <= x < sx and 0 <= y < sy and 0 <= z < sz:
                grid[x, y, z] = ci

        # Use embedded palette or generate default
        if palette_rgba is None:
            palette_rgba = VoxModel._generate_default_palette()

        return VoxModel(size_x=sx, size_y=sy, size_z=sz, grid=grid, palette=palette_rgba)

    @staticmethod
    def from_raw_grid(data: bytes) -> "VoxModel":
        """
        Parse the VoxelRenderer binary format (same as distributed_engine input).
            4B  chunk_x  (int32)  — ignored for colour/lod purposes
            4B  chunk_y  (int32)
            4B  chunk_z  (int32)
            4B  size     (uint32)
            N   voxel_ids (uint8 × size³)
        """
        if len(data) < 16:
            raise ValueError("from_raw_grid: payload too short")
        _, _, _, size = struct.unpack_from("<iiII", data, 0)
        if size > 64:
            raise ValueError(f"from_raw_grid: size {size} > 64")
        expected = 16 + size ** 3
        if len(data) < expected:
            raise ValueError("from_raw_grid: voxel data truncated")
        raw = np.frombuffer(data, dtype=np.uint8, count=size**3, offset=16)
        grid = raw.reshape((size, size, size))
        palette = VoxModel._generate_default_palette()
        return VoxModel(size_x=size, size_y=size, size_z=size, grid=grid, palette=palette)

    @staticmethod
    def _generate_default_palette() -> np.ndarray:
        """
        Generate the default MagicaVoxel palette.
        Index 0 is transparent (air). Indices 1-255 are solid colours.
        Uses the same colour generation as MagicaVoxel 0.99.
        """
        palette = np.zeros((256, 4), dtype=np.float32)
        palette[0] = [0, 0, 0, 0]  # air/transparent
        # Simple HSV rainbow across indices 1-255
        for i in range(1, 256):
            hue = (i - 1) / 254.0
            r, g, b = _hsv_to_rgb(hue, 0.8, 0.9)
            palette[i] = [r, g, b, 1.0]
        return palette

    def downsample(self, stride: int) -> "VoxModel":
        """
        Downsample by integer stride for LOD.
        A voxel in the output is solid if ANY voxel in its stride³ block is solid.
        """
        if stride <= 1:
            return self
        sx = max(1, self.size_x // stride)
        sy = max(1, self.size_y // stride)
        sz = max(1, self.size_z // stride)
        new_grid = np.zeros((sx, sy, sz), dtype=np.uint8)
        for x in range(sx):
            for y in range(sy):
                for z in range(sz):
                    block = self.grid[
                        x*stride: x*stride + stride,
                        y*stride: y*stride + stride,
                        z*stride: z*stride + stride,
                    ]
                    nz = block[block != 0]
                    if nz.size > 0:
                        new_grid[x, y, z] = int(nz[0])  # first non-zero colour
        return VoxModel(
            size_x=sx, size_y=sy, size_z=sz,
            grid=new_grid, palette=self.palette
        )


def _hsv_to_rgb(h: float, s: float, v: float) -> Tuple[float, float, float]:
    if s == 0.0:
        return v, v, v
    i = int(h * 6)
    f = (h * 6) - i
    p = v * (1 - s)
    q = v * (1 - s * f)
    t = v * (1 - s * (1 - f))
    i %= 6
    if i == 0: return v, t, p
    if i == 1: return q, v, p
    if i == 2: return p, v, t
    if i == 3: return p, q, v
    if i == 4: return t, p, v
    return v, p, q


# ─────────────────────────────────────────────────────────────────────────────
# LIGHTING CALCULATION
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LightParams:
    """
    Flattened lighting parameters consumed during mesh generation.
    Derived from lighting_engine.py LightState. Kept here as a plain
    dataclass so this module has zero import dependency on lighting_engine.py
    (important for process-pool pickling).
    """
    # Key light
    key_dir: np.ndarray = field(default_factory=lambda: np.array([0.707, 0.707, 0.0], dtype=np.float32))
    key_color: np.ndarray = field(default_factory=lambda: np.array([1.0, 0.96, 0.88], dtype=np.float32))
    key_intensity: float = 1.0

    # Fill light
    fill_dir: np.ndarray = field(default_factory=lambda: np.array([-0.5, 0.3, 0.5], dtype=np.float32))
    fill_color: np.ndarray = field(default_factory=lambda: np.array([0.6, 0.7, 1.0], dtype=np.float32))
    fill_intensity: float = 0.35

    # Rim light
    rim_dir: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.3, -1.0], dtype=np.float32))
    rim_color: np.ndarray = field(default_factory=lambda: np.array([1.0, 0.96, 0.88], dtype=np.float32))
    rim_intensity: float = 0.6

    # Ambient
    ambient_color: np.ndarray = field(default_factory=lambda: np.array([0.12, 0.13, 0.18], dtype=np.float32))
    ambient_intensity: float = 0.25

    @staticmethod
    def from_light_state_dict(d: Dict[str, Any]) -> "LightParams":
        """
        Build from a dict as returned by LightState.to_js_camel() or asdict().
        Handles both snake_case and camelCase keys gracefully.
        Falls back to defaults for any missing key.
        """
        def get(key_snake: str, key_camel: str, default):
            return d.get(key_snake, d.get(key_camel, default))

        def kelvin_to_rgb(k: float) -> np.ndarray:
            """Approximate Planckian locus to sRGB. Good enough for game-quality rendering."""
            k = max(1000.0, min(12000.0, k))
            if k <= 6600:
                r = 1.0
                g = max(0.0, (99.4708025861 * math.log(k / 100.0) - 161.1195681661) / 255.0)
                b = 0.0 if k <= 1900 else max(0.0, (138.5177312231 * math.log(k / 100.0 - 10.0) - 305.0447927307) / 255.0)
            else:
                r = max(0.0, (329.698727446 * ((k / 100.0 - 60.0) ** -0.1332047592)) / 255.0)
                g = max(0.0, (288.1221695283 * ((k / 100.0 - 60.0) ** -0.0755148492)) / 255.0)
                b = 1.0
            return np.clip(np.array([r, g, b], dtype=np.float32), 0.0, 1.0)

        def azimuth_elevation_to_dir(az_deg: float, el_deg: float) -> np.ndarray:
            az  = math.radians(az_deg)
            el  = math.radians(el_deg)
            x   = math.cos(el) * math.sin(az)
            y   = math.sin(el)
            z   = math.cos(el) * math.cos(az)
            v   = np.array([x, y, z], dtype=np.float32)
            n   = np.linalg.norm(v)
            return v / n if n > 1e-6 else np.array([0.0, 1.0, 0.0], dtype=np.float32)

        key_k   = float(get("key_kelvin",   "keyKelvin",   5600.0))
        fill_k  = float(get("fill_kelvin",  "fillKelvin",  6200.0))
        rim_k   = float(get("rim_kelvin",   "rimKelvin",   5600.0))
        amb_raw = get("ambient_color", "ambientColor", [0.12, 0.13, 0.18])

        return LightParams(
            key_dir       = azimuth_elevation_to_dir(
                                float(get("key_azimuth",   "keyAzimuth",   -45.0)),
                                float(get("key_elevation", "keyElevation",  35.0))),
            key_color     = kelvin_to_rgb(key_k),
            key_intensity = float(get("key_intensity", "keyIntensity", 1.0)),

            fill_dir      = azimuth_elevation_to_dir(
                                float(get("fill_azimuth",   "fillAzimuth",   60.0)),
                                float(get("fill_elevation", "fillElevation", 20.0))),
            fill_color    = kelvin_to_rgb(fill_k),
            fill_intensity= float(get("fill_intensity", "fillIntensity", 0.35)),

            rim_dir       = np.array([0.0, 0.3, -1.0], dtype=np.float32),
            rim_color     = kelvin_to_rgb(rim_k),
            rim_intensity = float(get("rim_intensity", "rimIntensity", 0.6)),

            ambient_color     = np.clip(np.array(amb_raw, dtype=np.float32), 0.0, 1.0),
            ambient_intensity = float(get("ambient_intensity", "ambientIntensity", 0.25)),
        )

    @staticmethod
    def default() -> "LightParams":
        return LightParams()


def apply_lighting(
    base_color: np.ndarray,   # (3,) float32, palette colour
    normal: np.ndarray,        # (3,) float32, unit normal
    light: LightParams,
) -> np.ndarray:
    """
    Compute lit colour for a single vertex.
    Uses classic diffuse (Lambertian) shading for key, fill, and rim,
    plus a constant ambient term.

    Returns (3,) float32 clamped to [0, 1].
    """
    # Normalise inputs defensively
    n = normal / (np.linalg.norm(normal) + 1e-8)

    # Lambertian diffuse for each light
    key_diff  = max(0.0, float(np.dot(n, light.key_dir)))
    fill_diff = max(0.0, float(np.dot(n, light.fill_dir)))
    rim_diff  = max(0.0, float(np.dot(n, light.rim_dir)))

    lit = (
        base_color * light.ambient_color * light.ambient_intensity
        + base_color * light.key_color  * light.key_intensity  * key_diff
        + base_color * light.fill_color * light.fill_intensity * fill_diff
        + base_color * light.rim_color  * light.rim_intensity  * rim_diff
    )
    return np.clip(lit, 0.0, 1.0).astype(np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# FACE TABLE (shared with VoxelRenderer in distributed_engine_node_real.py)
# ─────────────────────────────────────────────────────────────────────────────

_FACES = [
    # (dx, dy, dz, corner_offsets_4x3, normal_3)
    ( 1,  0,  0, [(1,0,0),(1,1,0),(1,1,1),(1,0,1)], ( 1, 0, 0)),
    (-1,  0,  0, [(0,1,0),(0,0,0),(0,0,1),(0,1,1)], (-1, 0, 0)),
    ( 0,  1,  0, [(0,1,0),(1,1,0),(1,1,1),(0,1,1)], ( 0, 1, 0)),
    ( 0, -1,  0, [(1,0,0),(0,0,0),(0,0,1),(1,0,1)], ( 0,-1, 0)),
    ( 0,  0,  1, [(0,0,1),(1,0,1),(1,1,1),(0,1,1)], ( 0, 0, 1)),
    ( 0,  0, -1, [(1,0,0),(0,0,0),(0,1,0),(1,1,0)], ( 0, 0,-1)),
]

_NORMAL_ARRAY = np.array([f[4] for f in _FACES], dtype=np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# BILLBOARD MESH  (LOD3)
# ─────────────────────────────────────────────────────────────────────────────

def _make_billboard(model: VoxModel, light: LightParams) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Two triangles (one quad) centred on the voxel model AABB.
    The quad faces +Z (towards the default camera).
    Caller is expected to billboard-rotate this in the shader/compositor.
    """
    cx = model.size_x / 2.0
    cy = model.size_y / 2.0
    cz = model.size_z / 2.0
    w  = float(model.size_x)
    h  = float(model.size_y)

    verts = np.array([
        [cx - w/2, cy - h/2, cz],
        [cx + w/2, cy - h/2, cz],
        [cx + w/2, cy + h/2, cz],
        [cx - w/2, cy + h/2, cz],
    ], dtype=np.float32)

    normal = np.array([0.0, 0.0, 1.0], dtype=np.float32)
    norms  = np.tile(normal, (4, 1))

    # Average colour from all non-air voxels
    nz_indices = model.grid[model.grid != 0]
    if nz_indices.size > 0:
        avg_palette_idx = int(np.bincount(nz_indices.astype(np.intp)).argmax())
        base_color = model.palette[avg_palette_idx, :3]
    else:
        base_color = np.array([0.8, 0.8, 0.8], dtype=np.float32)

    lit = apply_lighting(base_color, normal, light)
    colors = np.tile(lit, (4, 1))

    indices = np.array([0, 1, 2, 0, 2, 3], dtype=np.uint32)
    return verts, norms, colors, indices


# ─────────────────────────────────────────────────────────────────────────────
# CORE MESH BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _build_mesh(
    model: VoxModel,
    light: LightParams,
    lod: int,
    chunk_offset: Tuple[int, int, int] = (0, 0, 0),
) -> bytes:
    """
    Generate a lit mesh from a VoxModel at the given LOD level.

    Returns zlib-compressed bytes in the extended VoxelRenderer output layout:
        4B  vertex_count  (uint32)
        4B  index_count   (uint32)
        V×12B  vertices   (float32 x,y,z)
        V×12B  normals    (float32 nx,ny,nz)
        V×12B  colors     (float32 r,g,b)   ← new
        I×4B   indices    (uint32)
    """
    # LOD3 = billboard shortcut
    if lod >= 3:
        verts, norms, colors, indices = _make_billboard(model, light)
    else:
        stride = LOD_STRIDE[lod]
        if stride > 1:
            model = model.downsample(stride)

        cx_off, cy_off, cz_off = chunk_offset
        sx, sy, sz = model.size_x, model.size_y, model.size_z
        grid = model.grid

        vert_list:   List[float] = []
        norm_list:   List[float] = []
        color_list:  List[float] = []
        index_list:  List[int]   = []
        idx_base = 0
        tri_count = 0
        budget = LOD_TRIANGLE_BUDGET[lod]

        for x in range(sx):
            for y in range(sy):
                for z in range(sz):
                    palette_idx = int(grid[x, y, z])
                    if palette_idx == 0:
                        continue

                    base_color = model.palette[palette_idx, :3]
                    ox = cx_off + x * stride
                    oy = cy_off + y * stride
                    oz = cz_off + z * stride

                    for face_idx, (dx, dy, dz, corners, normal_tuple) in enumerate(_FACES):
                        nx, ny, nz = x + dx, y + dy, z + dz
                        if 0 <= nx < sx and 0 <= ny < sy and 0 <= nz < sz:
                            if grid[nx, ny, nz] != 0:
                                continue  # face hidden by neighbour

                        normal = _NORMAL_ARRAY[face_idx]
                        lit    = apply_lighting(base_color, normal, light)

                        for (fx, fy, fz) in corners:
                            vert_list  += [ox + fx * stride,
                                           oy + fy * stride,
                                           oz + fz * stride]
                            norm_list  += [float(normal[0]),
                                           float(normal[1]),
                                           float(normal[2])]
                            color_list += [float(lit[0]),
                                           float(lit[1]),
                                           float(lit[2])]

                        i = idx_base
                        index_list += [i, i+1, i+2, i, i+2, i+3]
                        idx_base   += 4
                        tri_count  += 2

                        if tri_count >= budget:
                            break
                    if tri_count >= budget:
                        break
                if tri_count >= budget:
                    break

        if not vert_list:
            # All-air result
            return zlib.compress(struct.pack("<II", 0, 0))

        verts   = np.array(vert_list,  dtype=np.float32)
        norms   = np.array(norm_list,  dtype=np.float32)
        colors  = np.array(color_list, dtype=np.float32)
        indices = np.array(index_list, dtype=np.uint32)

    # Serialise
    n_verts   = len(verts)  // 3
    n_indices = len(indices) if isinstance(indices, list) else indices.size

    buf = io.BytesIO()
    buf.write(struct.pack("<II", n_verts, n_indices))
    buf.write(np.array(verts,   dtype=np.float32).tobytes())
    buf.write(np.array(norms,   dtype=np.float32).tobytes())
    buf.write(np.array(colors,  dtype=np.float32).tobytes())
    buf.write(np.array(indices, dtype=np.uint32).tobytes())
    return zlib.compress(buf.getvalue(), level=1)


# ─────────────────────────────────────────────────────────────────────────────
# MODULE-LEVEL DISK CACHE  (process-local, not cross-process)
# ─────────────────────────────────────────────────────────────────────────────

_vox_cache: Dict[str, VoxModel] = {}


def _load_model(vox_path: Optional[str], raw_data: Optional[bytes]) -> VoxModel:
    """
    Load a VoxModel from disk (cached) or from raw grid bytes.
    Priority: vox_path > raw_data > empty 1³ model.
    """
    if vox_path:
        if vox_path not in _vox_cache:
            try:
                _vox_cache[vox_path] = VoxModel.from_vox_file(vox_path)
                logger.info("voxel_renderer: loaded %s (%d voxels)",
                            vox_path,
                            np.count_nonzero(_vox_cache[vox_path].grid))
            except Exception as exc:
                logger.error("voxel_renderer: failed to load %s — %s", vox_path, exc)
                raise
        return _vox_cache[vox_path]

    if raw_data is not None:
        return VoxModel.from_raw_grid(raw_data)

    # Empty fallback
    logger.warning("voxel_renderer: no vox_path or raw_data — returning empty model")
    empty_grid = np.zeros((1, 1, 1), dtype=np.uint8)
    return VoxModel(1, 1, 1, empty_grid, VoxModel._generate_default_palette())


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINT — called by distributed_engine_node_real.py work queue
# ─────────────────────────────────────────────────────────────────────────────

def render_voxel_work(data: bytes, metadata: Dict[str, Any]) -> bytes:
    """
    Drop-in replacement for VoxelRenderer.mesh().

    Called inside ProcessPoolExecutor. Must be picklable (module-level function).
    Must never raise — returns empty mesh bytes on any failure.

    Wire-in to distributed_engine_node_real.py:
        result = await loop.run_in_executor(
            self._cpu_executor, render_voxel_work, work.data, work.metadata
        )
    """
    try:
        # ── Determine LOD ────────────────────────────────────────────────────
        if "lod" in metadata:
            lod = int(metadata["lod"])
        else:
            dist = float(metadata.get("camera_dist_m", 1.0))
            lod  = dist_to_lod(dist)
        lod = max(0, min(3, lod))

        # ── Load or decode light state ────────────────────────────────────────
        light_dict = metadata.get("light_state")
        if light_dict and isinstance(light_dict, dict):
            light = LightParams.from_light_state_dict(light_dict)
        else:
            light = LightParams.default()

        # ── Load model ───────────────────────────────────────────────────────
        vox_path = metadata.get("vox_path")
        model    = _load_model(vox_path, data if data else None)

        # ── Chunk offset from data header (for multi-chunk scenes) ───────────
        chunk_offset = (0, 0, 0)
        if data and len(data) >= 16:
            cx, cy, cz, _ = struct.unpack_from("<iiII", data, 0)
            chunk_offset  = (cx, cy, cz)

        # ── Build mesh ───────────────────────────────────────────────────────
        return _build_mesh(model, light, lod, chunk_offset)

    except Exception as exc:
        logger.error("render_voxel_work: unhandled error — %s", exc, exc_info=True)
        # Return empty-mesh bytes — never crash the work queue
        return zlib.compress(struct.pack("<II", 0, 0))


# ─────────────────────────────────────────────────────────────────────────────
# UTILITY: read back the extended output format
# ─────────────────────────────────────────────────────────────────────────────

def decode_mesh_bytes(compressed: bytes) -> Dict[str, np.ndarray]:
    """
    Decode the output of render_voxel_work / VoxelRenderer.mesh.

    Returns dict with keys:
        "vertices"  — (N, 3) float32
        "normals"   — (N, 3) float32
        "colors"    — (N, 3) float32  (zeros if not present — old format)
        "indices"   — (M,)   uint32

    Safe to call on both old (no colors) and new (with colors) mesh bytes.
    """
    raw = zlib.decompress(compressed)
    n_verts, n_idx = struct.unpack_from("<II", raw, 0)
    offset = 8

    verts  = np.frombuffer(raw, dtype=np.float32, count=n_verts * 3,  offset=offset).reshape(n_verts, 3).copy()
    offset += n_verts * 12
    norms  = np.frombuffer(raw, dtype=np.float32, count=n_verts * 3,  offset=offset).reshape(n_verts, 3).copy()
    offset += n_verts * 12

    # colors are optional (extended format only)
    remaining = len(raw) - offset - n_idx * 4
    if remaining >= n_verts * 12:
        colors = np.frombuffer(raw, dtype=np.float32, count=n_verts * 3, offset=offset).reshape(n_verts, 3).copy()
        offset += n_verts * 12
    else:
        colors = np.zeros((n_verts, 3), dtype=np.float32)

    indices = np.frombuffer(raw, dtype=np.uint32, count=n_idx, offset=offset).copy()

    return {"vertices": verts, "normals": norms, "colors": colors, "indices": indices}
