# PubCast AI — pubworld_blocks.py
# Copyright © 2024–2026 Josie Curtsey Cobbley (Joshua Cobbley)
# Rearview Foresight LLC — All Rights Reserved
# Feic Mo Chroí — See My Heart
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError, constr

from .persistence import sanitize_filename, read_json, write_json


# ---------- Data Models ---------- #


class Block(BaseModel):
    x: int
    y: int
    z: int
    kind: constr(strip_whitespace=True, min_length=1) = "cube"  # cube|half|wedge
    color: Optional[str] = None  # e.g., "#ff9b42"
    texture: Optional[str] = None  # path like "/assets/uploads/.../tex.png"
    uv_scale: Optional[Tuple[float, float]] = None  # e.g., (1.0, 1.0)


class Link(BaseModel):
    kind: constr(strip_whitespace=True, min_length=1) = "link"  # link|hinge|brace
    a: Tuple[int, int, int]
    b: Tuple[int, int, int]
    params: Dict[str, Any] = Field(default_factory=dict)


class Prop(BaseModel):
    prop_id: constr(strip_whitespace=True, min_length=6, max_length=48)
    scene_id: constr(strip_whitespace=True, min_length=6, max_length=48)
    label: constr(strip_whitespace=True, min_length=1, max_length=64)
    description: str = ""
    blocks: List[Block] = Field(default_factory=list)
    links: List[Link] = Field(default_factory=list)
    variants: List[Dict[str, Any]] = Field(default_factory=list)
    active_variant: Optional[str] = None
    tracker_ids: List[str] = Field(default_factory=list)
    signature: Dict[str, Any] = Field(default_factory=dict)
    created_at: float
    updated_at: float


class Prototype(BaseModel):
    prototype_id: constr(strip_whitespace=True, min_length=6, max_length=48)
    label: constr(strip_whitespace=True, min_length=1, max_length=64)
    description: str = ""
    blocks: List[Block] = Field(default_factory=list)
    links: List[Link] = Field(default_factory=list)
    signature: Dict[str, Any] = Field(default_factory=dict)
    created_at: float
    updated_at: float


class TrackerSample(BaseModel):
    t: float
    pos: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    rot: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)


class Tracker(BaseModel):
    tracker_id: constr(strip_whitespace=True, min_length=6, max_length=48)
    kind: constr(strip_whitespace=True, min_length=1) = "prop"  # prop|actor_joint
    target_prop: Optional[str] = None
    target_block_index: Optional[int] = None
    baseline: Optional[TrackerSample] = None
    samples: List[TrackerSample] = Field(default_factory=list)
    created_at: float
    updated_at: float


# ---------- Storage helpers ---------- #


def _props_dir(base_dir: Path) -> Path:
    path = base_dir / "pubworld" / "props"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _prototypes_dir(base_dir: Path) -> Path:
    path = base_dir / "pubworld" / "prototypes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _trackers_dir(base_dir: Path) -> Path:
    path = base_dir / "pubworld" / "trackers"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _presets_dir(base_dir: Path) -> Path:
    path = base_dir / "pubworld" / "presets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _prop_path(base_dir: Path, prop_id: str) -> Path:
    safe = sanitize_filename(prop_id)
    return _props_dir(base_dir) / f"{safe}.json"


def _prototype_path(base_dir: Path, prototype_id: str) -> Path:
    safe = sanitize_filename(prototype_id)
    return _prototypes_dir(base_dir) / f"{safe}.json"


def _tracker_path(base_dir: Path, tracker_id: str) -> Path:
    safe = sanitize_filename(tracker_id)
    return _trackers_dir(base_dir) / f"{safe}.json"


def _preset_path(base_dir: Path, preset_id: str) -> Path:
    safe = sanitize_filename(preset_id)
    return _presets_dir(base_dir) / f"{safe}.json"


class BuilderPreset(BaseModel):
    preset_id: constr(strip_whitespace=True, min_length=1, max_length=64)
    name: constr(strip_whitespace=True, min_length=1, max_length=128)
    blocks: List[Block] = Field(default_factory=list)
    links: List[Link] = Field(default_factory=list)
    created_at: float
    updated_at: float


def list_builder_presets(base_dir: Path) -> List[BuilderPreset]:
    rows: List[BuilderPreset] = []
    for file in _presets_dir(base_dir).glob("*.json"):
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
            rows.append(BuilderPreset(**payload))
        except Exception:
            continue
    rows.sort(key=lambda p: p.updated_at, reverse=True)
    return rows


def save_builder_preset(base_dir: Path, name: str, blocks_data: List[Dict[str, Any]], links_data: Optional[List[Dict[str, Any]]] = None) -> BuilderPreset:
    ts = time.time()
    preset_id = sanitize_filename(name.lower().replace(" ", "_")[:64]) or f"preset_{uuid.uuid4().hex[:8]}"
    blks = [Block(**b) for b in (blocks_data or [])]
    lnks = [Link(**l) for l in (links_data or [])]
    preset = BuilderPreset(preset_id=preset_id, name=name, blocks=blks, links=lnks, created_at=ts, updated_at=ts)
    write_json(_preset_path(base_dir, preset_id), preset.model_dump())
    return preset


def get_builder_preset(base_dir: Path, preset_id: str) -> BuilderPreset:
    path = _preset_path(base_dir, preset_id)
    payload = read_json(path)
    return BuilderPreset(**payload)


# ---------- Signatures & utilities ---------- #


def _normalized_coords(blocks: List[Block]) -> List[Tuple[int, int, int]]:
    if not blocks:
        return []
    xs = [b.x for b in blocks]
    ys = [b.y for b in blocks]
    zs = [b.z for b in blocks]
    minx, miny, minz = min(xs), min(ys), min(zs)
    coords = [(b.x - minx, b.y - miny, b.z - minz) for b in blocks]
    coords.sort()
    return coords


def compute_signature(blocks: List[Block], links: Optional[List[Link]] = None) -> Dict[str, Any]:
    """Compute a simple, rotation-aware signature.

    Includes:
    - count: number of blocks
    - dims: axis-aligned bounding box dimensions (x,y,z)
    - coords: normalized integer coordinates
    - edges: adjacency edges between 6-neighbour cells (undirected, normalized)
    """
    if not blocks:
        sig = {"count": 0, "dims": (0, 0, 0), "coords": [], "edges": []}
        if links:
            sig["links"] = []
        return sig
    xs = [b.x for b in blocks]
    ys = [b.y for b in blocks]
    zs = [b.z for b in blocks]
    dims = (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1, max(zs) - min(zs) + 1)
    coords = _normalized_coords(blocks)
    # Build adjacency on normalized coords
    coord_set = set(coords)
    edges: List[Tuple[Tuple[int, int, int], Tuple[int, int, int]]] = []
    neighbours = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
    for (x, y, z) in coord_set:
        for dx, dy, dz in neighbours:
            nb = (x + dx, y + dy, z + dz)
            if nb in coord_set:
                a, b = (x, y, z), nb
                # undirected normalization: sort tuple pair
                edge = (a, b) if a <= b else (b, a)
                edges.append(edge)
    # dedupe and sort edges for stable repr
    edges = sorted(set(edges))
    # Represent edges as flat tuples for JSON safety
    edge_rows = [(*e[0], *e[1]) for e in edges]
    result = {"count": len(blocks), "dims": dims, "coords": coords, "edges": edge_rows}
    # If links provided, normalize to origin as well and include
    if links:
        minx = min(b.x for b in blocks)
        miny = min(b.y for b in blocks)
        minz = min(b.z for b in blocks)
        norm_links = []
        for link in links:
            ax, ay, az = link.a
            bx, by, bz = link.b
            a = (ax - minx, ay - miny, az - minz)
            b = (bx - minx, by - miny, bz - minz)
            # undirected normalization
            aa, bb = (a, b) if a <= b else (b, a)
            norm_links.append((*(aa), *(bb)))
        result["links"] = sorted(set(norm_links))
    return result


def _jaccard_coords(a: List[Tuple[int, int, int]], b: List[Tuple[int, int, int]]) -> float:
    sa = set(a)
    sb = set(b)
    if not sa and not sb:
        return 1.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def _rotate_y(point: Tuple[int, int, int], turns: int) -> Tuple[int, int, int]:
    """Rotate (x,y,z) around Y axis by 90Â° increments (turns = 0..3)."""
    x, y, z = point
    t = turns % 4
    if t == 0:
        return (x, y, z)
    if t == 1:
        return (z, y, -x)
    if t == 2:
        return (-x, y, -z)
    # t == 3
    return (-z, y, x)


def _rotate_signature(sig: Dict[str, Any], turns: int) -> Dict[str, Any]:
    coords = [tuple(c) for c in sig.get("coords", [])]
    rcoords = [_rotate_y(c, turns) for c in coords]
    # renormalize to origin
    if not rcoords:
        dims = (0, 0, 0)
        edges = []
        links = []
    else:
        xs = [c[0] for c in rcoords]
        ys = [c[1] for c in rcoords]
        zs = [c[2] for c in rcoords]
        minx, miny, minz = min(xs), min(ys), min(zs)
        rcoords = [(x - minx, y - miny, z - minz) for (x, y, z) in rcoords]
        rcoords.sort()
        dims = (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1, max(zs) - min(zs) + 1)
        # reconstruct edges from rotated coords
        cset = set(rcoords)
        neighbours = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
        edges: List[Tuple[Tuple[int, int, int], Tuple[int, int, int]]] = []
        for (x, y, z) in cset:
            for dx, dy, dz in neighbours:
                nb = (x + dx, y + dy, z + dz)
                if nb in cset:
                    a, b = (x, y, z), nb
                    edge = (a, b) if a <= b else (b, a)
                    edges.append(edge)
        edges = sorted(set(edges))
        edges = [(*e[0], *e[1]) for e in edges]
        # rotate links if any
        links = []
        for row in sig.get("links", []) or []:
            ax, ay, az, bx, by, bz = row
            ra = _rotate_y((ax, ay, az), turns)
            rb = _rotate_y((bx, by, bz), turns)
            # renormalize
            ra = (ra[0] - minx, ra[1] - miny, ra[2] - minz)
            rb = (rb[0] - minx, rb[1] - miny, rb[2] - minz)
            aa, bb = (ra, rb) if ra <= rb else (rb, ra)
            links.append((*(aa), *(bb)))
        links = sorted(set(links))
    return {
        "count": int(sig.get("count") or 0),
        "dims": dims,
        "coords": rcoords,
        "edges": edges,
        "links": links,
    }


def _jaccard_edges(a_edges: List[Tuple[int, int, int, int, int, int]], b_edges: List[Tuple[int, int, int, int, int, int]]) -> float:
    sa = set(map(tuple, a_edges))
    sb = set(map(tuple, b_edges))
    if not sa and not sb:
        return 1.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def _jaccard_links(a_links: List[Tuple[int, int, int, int, int, int]], b_links: List[Tuple[int, int, int, int, int, int]]) -> float:
    sa = set(map(tuple, a_links))
    sb = set(map(tuple, b_links))
    if not sa and not sb:
        return 1.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


# ---------- Props CRUD ---------- #


def list_props(base_dir: Path, scene_id: Optional[str] = None) -> List[Prop]:
    rows: List[Prop] = []
    for file in _props_dir(base_dir).glob("*.json"):
        payload = json.loads(file.read_text(encoding="utf-8"))
        try:
            p = Prop(**payload)
        except ValidationError:
            continue
        if scene_id and p.scene_id != scene_id:
            continue
        rows.append(p)
    rows.sort(key=lambda p: p.updated_at, reverse=True)
    return rows


def get_prop(base_dir: Path, prop_id: str) -> Prop:
    payload = json.loads(_prop_path(base_dir, prop_id).read_text(encoding="utf-8"))
    return Prop(**payload)


def save_prop(base_dir: Path, prop: Prop) -> Prop:
    write_json(_prop_path(base_dir, prop.prop_id), prop.model_dump())
    return prop


def create_prop(base_dir: Path, scene_id: str, label: str, description: str, blocks: List[Dict[str, Any]], variants: Optional[List[Dict[str, Any]]] = None) -> Prop:
    prop_id = f"prp_{uuid.uuid4().hex[:10]}"
    ts = time.time()
    blks = [Block(**b) for b in (blocks or [])]
    prop = Prop(
        prop_id=prop_id,
        scene_id=scene_id,
        label=label,
        description=description or "",
        blocks=blks,
        variants=variants or [],
        signature=compute_signature(blks),
        created_at=ts,
        updated_at=ts,
    )
    return save_prop(base_dir, prop)


def update_prop_variants(base_dir: Path, prop_id: str, variants: List[Dict[str, Any]]) -> Prop:
    path = _prop_path(base_dir, prop_id)
    if not path.exists():
        raise FileNotFoundError("Prop not found")
    payload = read_json(path)
    p = Prop(**payload)
    p.variants = variants or []
    p.updated_at = time.time()
    return save_prop(base_dir, p)


def set_active_variant(base_dir: Path, prop_id: str, variant_id: Optional[str]) -> Prop:
    path = _prop_path(base_dir, prop_id)
    if not path.exists():
        raise FileNotFoundError("Prop not found")
    payload = read_json(path)
    p = Prop(**payload)
    p.active_variant = variant_id
    p.updated_at = time.time()
    return save_prop(base_dir, p)


# ---------- Prototypes & Recognition ---------- #


def list_prototypes(base_dir: Path) -> List[Prototype]:
    rows: List[Prototype] = []
    for file in _prototypes_dir(base_dir).glob("*.json"):
        payload = json.loads(file.read_text(encoding="utf-8"))
        try:
            rows.append(Prototype(**payload))
        except ValidationError:
            continue
    rows.sort(key=lambda p: p.updated_at, reverse=True)
    return rows


def save_prototype(base_dir: Path, label: str, description: str, blocks: List[Dict[str, Any]]) -> Prototype:
    prototype_id = f"pt_{uuid.uuid4().hex[:10]}"
    ts = time.time()
    blks = [Block(**b) for b in (blocks or [])]
    proto = Prototype(
        prototype_id=prototype_id,
        label=label,
        description=description or "",
        blocks=blks,
        signature=compute_signature(blks),
        created_at=ts,
        updated_at=ts,
    )
    write_json(_prototype_path(base_dir, prototype_id), proto.model_dump())
    return proto


def recognize_prop(blocks: List[Dict[str, Any]], prototypes: List[Prototype]) -> Optional[Dict[str, Any]]:
    blks = [Block(**b) for b in (blocks or [])]
    # Attempt to accept optional links if present
    cand_links_raw = []
    # When passed from client, links will be in payload; recognition entrypoint takes only blocks, so skip unless provided.
    cand_sig = compute_signature(blks, links=None)
    # Try four 90Â° Y-rotations of the candidate signature
    rotated_cands = [_rotate_signature(cand_sig, t) for t in range(4)]
    best_proto: Optional[Prototype] = None
    best_score = 0.0
    best_detail: Dict[str, Any] = {}

    for proto in prototypes:
        psig = proto.signature or {}
        # If legacy prototype without edges, compute a temporary signature from blocks
        if not psig.get("coords") and getattr(proto, "blocks", None):
            try:
                pblocks = [Block(**b) for b in proto.blocks]  # type: ignore[attr-defined]
                plinks = [Link(**l) for l in getattr(proto, "links", [])] if getattr(proto, "links", None) else None
                psig = compute_signature(pblocks, links=plinks)
            except Exception:
                psig = proto.signature or {}
        # Precompute four rotations of prototype as well for symmetric dims filtering
        rotated_protos = [_rotate_signature(psig, t) for t in range(4)]

        for csig in rotated_cands:
            for rsig in rotated_protos:
                # Scores
                score_edges = _jaccard_edges(csig.get("edges") or [], rsig.get("edges") or [])
                score_coords = _jaccard_coords(csig.get("coords") or [], rsig.get("coords") or [])
                # Links similarity
                score_links = _jaccard_links(csig.get("links") or [], rsig.get("links") or [])
                # Combine; edges weighted higher for topology match, links further refine if present
                score = 0.6 * score_edges + 0.25 * score_coords + 0.15 * score_links
                # Penalize large count mismatch to avoid trivial partials dominating
                ccount = int(csig.get("count") or 0)
                pcount = int(rsig.get("count") or 0)
                if ccount and pcount and ccount != pcount:
                    ratio = min(ccount, pcount) / max(ccount, pcount)
                    score *= 0.5 + 0.5 * ratio  # scale down if counts differ
                if score > best_score:
                    best_score = score
                    best_proto = proto
                    best_detail = {
                        "score_edges": score_edges,
                        "score_coords": score_coords,
                        "score_links": score_links,
                        "candidate_count": ccount,
                        "prototype_count": pcount,
                    }

    if not best_proto:
        return None
    result: Dict[str, Any] = {
        "prototype_id": best_proto.prototype_id,
        "label": best_proto.label,
        "score": best_score,
    }
    result.update(best_detail)
    return result


# ---------- Simple Generators ---------- #


def _make_pyramid(levels: int = 3) -> List[Block]:
    blks: List[Block] = []
    for y in range(levels):
        size = levels - y
        for x in range(size):
            for z in range(size):
                blks.append(Block(x=x, y=y, z=z))
    return blks


def _make_stage(w: int = 4, d: int = 3, h: int = 1) -> List[Block]:
    blks: List[Block] = []
    for y in range(h):
        for x in range(w):
            for z in range(d):
                blks.append(Block(x=x, y=y, z=z))
    return blks


def _make_chair() -> List[Block]:
    blks: List[Block] = []
    # seat 2x2 at y=0
    for x in range(2):
        for z in range(2):
            blks.append(Block(x=x, y=0, z=z))
    # back 2 high at one edge
    blks.append(Block(x=0, y=1, z=0))
    blks.append(Block(x=0, y=2, z=0))
    return blks


def _make_ring(size: int = 6) -> List[Block]:
    # Hollow square ring on y=0
    size = max(3, size)
    y = 0
    blks: List[Block] = []
    for x in range(size):
        for z in range(size):
            edge = (x == 0 or x == size - 1 or z == 0 or z == size - 1)
            if edge:
                blks.append(Block(x=x, y=y, z=z))
    return blks


def _make_ladder(height: int = 4, width: int = 3, rung_gap: int = 1) -> List[Block]:
    blks: List[Block] = []
    width = max(2, width)
    for y in range(height):
        # side rails
        blks.append(Block(x=0, y=y, z=0))
        blks.append(Block(x=width - 1, y=y, z=0))
        # rungs every rung_gap
        if y % (rung_gap + 1) == 0:
            for x in range(width):
                blks.append(Block(x=x, y=y, z=0))
    return blks


def _make_arch(span: int = 4, height: int = 3) -> List[Block]:
    blks: List[Block] = []
    span = max(2, span)
    # pillars
    for y in range(height):
        blks.append(Block(x=0, y=y, z=0))
        blks.append(Block(x=span - 1, y=y, z=0))
    # top
    for x in range(span):
        blks.append(Block(x=x, y=height, z=0))
    return blks


def generate_from_prompt(prompt: str) -> Tuple[str, List[Block]]:
    p = (prompt or "").lower()
    if "pyramid" in p:
        levels = 3
        if "small" in p:
            levels = 2
        if "large" in p or "big" in p:
            levels = 4
        return ("pyramid", _make_pyramid(levels))
    if "stage" in p or "platform" in p:
        w, d, h = 4, 3, 1
        if "small" in p:
            w, d = 3, 2
        if "large" in p or "big" in p:
            w, d = 6, 4
        return ("stage", _make_stage(w, d, h))
    if "chair" in p or "stool" in p:
        return ("chair", _make_chair())
    if "ring" in p or "circle" in p:
        size = 6
        if "small" in p:
            size = 4
        if "large" in p or "big" in p:
            size = 8
        return ("ring", _make_ring(size))
    if "ladder" in p:
        height = 5 if "small" in p else 7 if ("large" in p or "big" in p) else 6
        return ("ladder", _make_ladder(height=height))
    if "arch" in p:
        span = 4 if "small" in p else 6 if ("large" in p or "big" in p) else 5
        height = 3 if "small" in p else 5 if ("large" in p or "big" in p) else 4
        return ("arch", _make_arch(span=span, height=height))
    # default cube
    return ("block", [Block(x=0, y=0, z=0)])


# ---------- Trackers ---------- #


def save_tracker(base_dir: Path, tracker: Tracker) -> Tracker:
    write_json(_tracker_path(base_dir, tracker.tracker_id), tracker.model_dump())
    return tracker


def get_tracker(base_dir: Path, tracker_id: str) -> Tracker:
    payload = json.loads(_tracker_path(base_dir, tracker_id).read_text(encoding="utf-8"))
    return Tracker(**payload)


def attach_tracker(base_dir: Path, kind: str, target_prop: Optional[str], target_block_index: Optional[int]) -> Tracker:
    ts = time.time()
    tracker = Tracker(
        tracker_id=f"trk_{uuid.uuid4().hex[:10]}",
        kind=kind or "prop",
        target_prop=target_prop,
        target_block_index=target_block_index,
        baseline=None,
        samples=[],
        created_at=ts,
        updated_at=ts,
    )
    return save_tracker(base_dir, tracker)


def append_samples(base_dir: Path, tracker_id: str, samples: List[Dict[str, Any]], set_baseline: bool = False) -> Tracker:
    tracker = get_tracker(base_dir, tracker_id)
    rows = [TrackerSample(**s) for s in (samples or [])]
    tracker.samples.extend(rows)
    tracker.updated_at = time.time()
    if set_baseline and rows:
        tracker.baseline = rows[0]
    return save_tracker(base_dir, tracker)


def continuity_delta(tracker: Tracker) -> Optional[float]:
    if not tracker.samples:
        return None
    ref = tracker.baseline or tracker.samples[0]
    last = tracker.samples[-1]
    dx = last.pos[0] - ref.pos[0]
    dy = last.pos[1] - ref.pos[1]
    dz = last.pos[2] - ref.pos[2]
    return (dx * dx + dy * dy + dz * dz) ** 0.5
