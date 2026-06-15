# PubCast AI — voxel_llm_adapter.py
# Copyright © 2024–2026 Josie Curtsey Cobbley (Joshua Cobbley)
# Rearview Foresight LLC — All Rights Reserved
# Feic Mo Chroí — See My Heart
"""
modules/voxel_llm_adapter.py
-----------------------------
Cloud AI adapter for PubWorld voxel generation.

Tries providers in priority order: anthropic → openai → gemini → local fallback.
Returns (label, blocks_list) on success, or (None, None) on failure.

Usage in main.py:
    result, provider_used = await vla.generate_with_cloud(prompt, provider="auto")
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

try:
    import httpx
except ImportError:  # Cloud/HTTP providers stay optional; local generation must still work.
    httpx = None

logger = logging.getLogger("pubcast.voxel_llm")

# ── Colour palette shared with the local generator ───────────────────────────
_PALETTE = {
    "stone":   "#888888", "wood":    "#8B4513", "glass":   "#87CEEB",
    "metal":   "#708090", "sand":    "#F4A460", "grass":   "#228B22",
    "water":   "#1E90FF", "fire":    "#FF4500", "energy":  "#00FFFF",
    "gold":    "#FFD700", "neon":    "#FF00FF", "dark":    "#1A1A2E",
    "light":   "#FFFACD", "red":     "#DC143C", "blue":    "#0047AB",
}

# Default block template
_BLOCK_DEFAULTS: Dict[str, Any] = {
    "type": "cube", "scale": [1, 1, 1], "rotation": [0, 0, 0],
    "material": "stone", "color": "#888888", "emissive": False,
    "tags": [],
}


def _make_block(x: int, y: int, z: int, material: str = "stone", color: str = "#888888",
                emissive: bool = False, tags: Optional[List[str]] = None) -> Dict[str, Any]:
    b = dict(_BLOCK_DEFAULTS)
    b.update({"position": [x, y, z], "material": material, "color": color,
               "emissive": emissive, "tags": tags or []})
    return b


# ── Local fallback generator (no API key needed) ─────────────────────────────

def _local_generate(prompt: str) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Deterministic rule-based generator used as fallback when no cloud AI is available.
    Produces a recognisable structure based on keywords in the prompt.
    """
    lower = prompt.lower()
    blocks: List[Dict[str, Any]] = []

    if any(k in lower for k in ("bar", "pub", "tavern", "drink")):
        label = "Pub Interior"
        for x in range(-3, 4): blocks.append(_make_block(x, 0, 0, "wood",   "#5C3317"))
        for y in range(1, 4):   blocks.append(_make_block(-3, y, 0, "stone", "#888888"))
        for y in range(1, 4):   blocks.append(_make_block(3, y, 0, "stone",  "#888888"))
        blocks.append(_make_block(0, 1, 0, "gold", "#FFD700", emissive=True, tags=["light"]))

    elif any(k in lower for k in ("stage", "theater", "theatre", "perform")):
        label = "Stage"
        for x in range(-4, 5): blocks.append(_make_block(x, 0, 0, "wood", "#3D1C02"))
        for x in range(-2, 3): blocks.append(_make_block(x, 0, 2, "wood", "#3D1C02"))
        blocks.append(_make_block(0, 2, 2, "energy", "#00FFFF", emissive=True, tags=["spotlight"]))

    elif any(k in lower for k in ("control", "studio", "broadcast", "camera")):
        label = "Control Room"
        for x in range(-4, 5): blocks.append(_make_block(x, 0, 0, "dark", "#1A1A2E"))
        for i in range(-3, 4):  blocks.append(_make_block(i, 1, -2, "glass", "#87CEEB",
                                                            emissive=True, tags=["monitor"]))
        blocks.append(_make_block(0, 2, -2, "neon", "#FF00FF", emissive=True))

    elif any(k in lower for k in ("forest", "tree", "nature", "garden")):
        label = "Forest Clearing"
        for x in range(-4, 5):
            for z in range(-4, 5): blocks.append(_make_block(x, 0, z, "grass", "#228B22"))
        for pos in [(-2, 1, -2), (2, 1, 2), (-3, 1, 3)]:
            for h in range(1, 4): blocks.append(_make_block(pos[0], h, pos[2], "wood", "#8B4513"))
            blocks.append(_make_block(pos[0], 4, pos[2], "grass", "#006400", tags=["foliage"]))

    else:
        label = "Open Space"
        for x in range(-3, 4):
            for z in range(-3, 4): blocks.append(_make_block(x, 0, z, "stone", "#888888"))
        blocks.append(_make_block(0, 1, 0, "energy", "#00FFFF", emissive=True))

    return label, blocks


# ── Prompt engineering ────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a 3D voxel scene generator for PubCast AI, a broadcast studio platform. "
    "Given a text description, generate a JSON array of voxel blocks. "
    "Each block must have: position [x,y,z], material (string), color (hex), "
    "emissive (bool), tags (array of strings). "
    "Keep scenes under 100 blocks. Use y=0 for floor level. "
    "Return ONLY valid JSON — no markdown, no explanation."
)


def _parse_llm_blocks(raw: str, label_hint: str) -> Optional[Tuple[str, List[Dict[str, Any]]]]:
    """Parse raw LLM JSON response into (label, blocks). Returns None on failure."""
    raw = raw.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = "\n".join(line for line in raw.split("\n")
                        if not line.strip().startswith("```"))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("voxel_llm: could not parse JSON from LLM response")
        return None

    if isinstance(data, dict):
        blocks = data.get("blocks", data.get("voxels", []))
        label  = data.get("label", data.get("name", label_hint))
    elif isinstance(data, list):
        blocks = data
        label  = label_hint
    else:
        return None

    if not isinstance(blocks, list) or len(blocks) == 0:
        return None

    # Normalise each block
    normalised: List[Dict[str, Any]] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        nb = dict(_BLOCK_DEFAULTS)
        nb.update({k: v for k, v in b.items() if k in (
            "position", "type", "scale", "rotation", "material",
            "color", "emissive", "tags"
        )})
        if "position" not in nb or not isinstance(nb["position"], list):
            continue
        normalised.append(nb)

    return (label, normalised) if normalised else None


# ── Provider implementations ──────────────────────────────────────────────────

async def _try_anthropic(prompt: str) -> Optional[Tuple[str, List[Dict[str, Any]]]]:
    if httpx is None:
        logger.info("voxel_llm anthropic skipped: httpx not installed")
        return None
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 1024,
                    "system": _SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            text = resp.json()["content"][0]["text"]
            return _parse_llm_blocks(text, prompt[:40])
    except Exception as exc:
        logger.warning("voxel_llm anthropic error: %s", exc)
        return None


async def _try_openai(prompt: str) -> Optional[Tuple[str, List[Dict[str, Any]]]]:
    if httpx is None:
        logger.info("voxel_llm openai skipped: httpx not installed")
        return None
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "max_tokens": 1024,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user",   "content": prompt},
                    ],
                },
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
            return _parse_llm_blocks(text, prompt[:40])
    except Exception as exc:
        logger.warning("voxel_llm openai error: %s", exc)
        return None


async def _try_gemini(prompt: str) -> Optional[Tuple[str, List[Dict[str, Any]]]]:
    if httpx is None:
        logger.info("voxel_llm gemini skipped: httpx not installed")
        return None
    api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-2.0-flash:generateContent?key={api_key}",
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": f"{_SYSTEM_PROMPT}\n\n{prompt}"}]}],
                    "generationConfig": {"maxOutputTokens": 1024},
                },
            )
            resp.raise_for_status()
            text = (resp.json()["candidates"][0]["content"]["parts"][0]["text"])
            return _parse_llm_blocks(text, prompt[:40])
    except Exception as exc:
        logger.warning("voxel_llm gemini error: %s", exc)
        return None


async def _try_ollama(prompt: str) -> Optional[Tuple[str, List[Dict[str, Any]]]]:
    if httpx is None:
        logger.info("voxel_llm ollama skipped: httpx not installed")
        return None
    host  = os.getenv("OLLAMA_HOST",  "http://localhost:11434")
    model = os.getenv("OLLAMA_MODEL", "mistral")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{host}/api/generate",
                json={
                    "model":  model,
                    "prompt": f"{_SYSTEM_PROMPT}\n\nGenerate voxels for: {prompt}",
                    "stream": False,
                    "options": {"temperature": 0.2, "num_predict": 1024},
                },
            )
            resp.raise_for_status()
            text = resp.json().get("response", "")
            return _parse_llm_blocks(text, prompt[:40])
    except Exception as exc:
        logger.debug("voxel_llm ollama unavailable: %s", exc)
        return None


# ── Public API ────────────────────────────────────────────────────────────────

async def generate_with_cloud(
    prompt: str,
    provider: str = "local",
) -> Tuple[Optional[Tuple[str, List[Dict[str, Any]]]], str]:
    """
    Generate voxel blocks from a text prompt. Local generation is the default;
    cloud/local-model AI is used only when explicitly requested.

    Returns:
        (result, provider_used)
        result is (label, blocks) on success, None on failure.
        provider_used is a string naming the provider ("anthropic", "openai", etc.)
    """
    provider = (provider or "local").lower()

    # Explicit provider selection
    if provider == "anthropic":
        r = await _try_anthropic(prompt)
        return (r, "anthropic") if r else (None, "anthropic_failed")
    if provider == "openai":
        r = await _try_openai(prompt)
        return (r, "openai") if r else (None, "openai_failed")
    if provider == "gemini":
        r = await _try_gemini(prompt)
        return (r, "gemini") if r else (None, "gemini_failed")
    if provider == "ollama":
        r = await _try_ollama(prompt)
        return (r, "ollama") if r else (None, "ollama_failed")
    if provider == "local":
        label, blocks = _local_generate(prompt)
        return ((label, blocks), "local")

    # Auto: try cloud providers in priority order, fall back to Ollama, then local
    for try_fn, name in [
        (_try_anthropic, "anthropic"),
        (_try_openai,    "openai"),
        (_try_gemini,    "gemini"),
        (_try_ollama,    "ollama"),
    ]:
        result = await try_fn(prompt)
        if result:
            logger.info("voxel_llm: used provider=%s", name)
            return (result, name)

    # Final fallback: deterministic local generation
    label, blocks = _local_generate(prompt)
    logger.info("voxel_llm: fell back to local generator")
    return ((label, blocks), "local")


__all__ = ["generate_with_cloud"]
