#!/usr/bin/env python3
# PubCast AI — voxel_asset_manager.py
# Copyright © 2024–2026 Josie Curtsey Cobbley (Joshua Cobbley)
# Rearview Foresight LLC — All Rights Reserved
# Feic Mo Chroí — See My Heart
"""
VOXEL ASSET MANAGER
===================
Manages the voxel asset library for PubCast AI's virtual production environments.

Features:
- Asset catalog management (furniture, structures, props, vehicles)
- Scene composition and validation
- Integration with Studio Control Room (preflight scene verification)
- Asset streaming coordination with Pete Enhanced
- Performance optimization (LOD selection, culling)

Architecture:
    Asset Library (JSON) → VoxelAssetManager → Studio Control → Pete → Rust Engine
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set, Any, TYPE_CHECKING
from dataclasses import dataclass, field, asdict
from enum import Enum

if TYPE_CHECKING:
    from pete_enhanced import PeteEnhanced

logger = logging.getLogger("pubcast.voxel_assets")

try:
    from .voxel_set_contract import load_voxel_set
except Exception:  # pragma: no cover - optional during partial imports
    load_voxel_set = None


class AssetCategory(Enum):
    """Asset categories in the voxel library"""
    FURNITURE = "furniture"
    HOME_STRUCTURE = "home_structure"
    ROOMS = "rooms"
    OUTDOOR = "outdoor"
    VEHICLES = "vehicles"
    PROPS = "props"


class AssetQuality(Enum):
    """Asset quality tiers"""
    STANDARD = "standard"
    FANCY = "fancy"


class AssetLoadState(Enum):
    """Asset loading state"""
    UNLOADED = "unloaded"
    LOADING = "loading"
    LOADED = "loaded"
    ERROR = "error"


@dataclass
class VoxelAsset:
    """Individual voxel asset"""
    asset_id: str
    name: str
    category: AssetCategory
    quality: AssetQuality = AssetQuality.STANDARD
    
    # Metadata
    voxel_count: Optional[int] = None
    memory_mb: Optional[float] = None
    file_path: Optional[str] = None
    
    # Runtime state
    load_state: AssetLoadState = AssetLoadState.UNLOADED
    instances: int = 0  # Number of times placed in scene
    
    # Performance
    lod_levels: int = 3  # Level of Detail levels available
    culling_enabled: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "asset_id": self.asset_id,
            "name": self.name,
            "category": self.category.value,
            "quality": self.quality.value,
            "voxel_count": self.voxel_count,
            "memory_mb": self.memory_mb,
            "file_path": self.file_path,
            "load_state": self.load_state.value,
            "instances": self.instances
        }


@dataclass
class SceneAssetInstance:
    """Placed asset instance in a scene"""
    instance_id: str
    asset_id: str
    
    # Transform
    position: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    rotation: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    scale: List[float] = field(default_factory=lambda: [1.0, 1.0, 1.0])
    
    # Rendering
    visible: bool = True
    cast_shadows: bool = True
    receive_shadows: bool = True
    
    # Interaction
    interactive: bool = False
    collision_enabled: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return asdict(self)


@dataclass
class VoxelScene:
    """Complete voxel scene composition"""
    scene_id: str
    name: str
    description: str = ""
    
    # Assets
    instances: List[SceneAssetInstance] = field(default_factory=list)
    required_assets: Set[str] = field(default_factory=set)
    
    # Environment
    ambient_light: float = 0.3
    directional_light: List[float] = field(default_factory=lambda: [0.5, -1.0, 0.3])
    fog_enabled: bool = False
    fog_distance: float = 100.0
    
    # Camera
    camera_position: List[float] = field(default_factory=lambda: [0.0, 1.6, 5.0])
    camera_target: List[float] = field(default_factory=lambda: [0.0, 1.0, 0.0])
    
    # Performance
    max_draw_distance: float = 50.0
    lod_bias: float = 1.0
    
    def add_instance(self, instance: SceneAssetInstance) -> None:
        """Add asset instance to scene"""
        self.instances.append(instance)
        self.required_assets.add(instance.asset_id)
    
    def remove_instance(self, instance_id: str) -> bool:
        """Remove asset instance from scene"""
        for i, inst in enumerate(self.instances):
            if inst.instance_id == instance_id:
                removed_asset_id = inst.asset_id
                self.instances.pop(i)
                
                # Update required assets (check if still needed)
                if not any(inst.asset_id == removed_asset_id for inst in self.instances):
                    self.required_assets.discard(removed_asset_id)
                
                return True
        return False
    
    def get_instance(self, instance_id: str) -> Optional[SceneAssetInstance]:
        """Get instance by ID"""
        for inst in self.instances:
            if inst.instance_id == instance_id:
                return inst
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "scene_id": self.scene_id,
            "name": self.name,
            "description": self.description,
            "instances": [inst.to_dict() for inst in self.instances],
            "required_assets": list(self.required_assets),
            "environment": {
                "ambient_light": self.ambient_light,
                "directional_light": self.directional_light,
                "fog_enabled": self.fog_enabled,
                "fog_distance": self.fog_distance
            },
            "camera": {
                "position": self.camera_position,
                "target": self.camera_target
            },
            "performance": {
                "max_draw_distance": self.max_draw_distance,
                "lod_bias": self.lod_bias
            }
        }


class VoxelAssetManager:
    """
    Voxel Asset Library Manager
    
    Manages the complete asset catalog, scene composition,
    and integration with Studio Control Room and Pete Enhanced.
    
    Responsibilities:
    - Load and parse asset library JSON
    - Build asset catalog with metadata
    - Compose and validate scenes
    - Coordinate asset loading with Pete/Rust engine
    - Preflight scene verification
    - Performance optimization (LOD, culling)
    """
    
    def __init__(
        self,
        library_path: Path,
        pete: Optional['PeteEnhanced'] = None,
        config: Optional[Dict[str, Any]] = None
    ):
        self.library_path = library_path
        self.pete = pete
        self.config = config or {}
        
        # Asset catalog
        self._assets: Dict[str, VoxelAsset] = {}
        self._asset_library: Dict[str, Any] = {}
        
        # Scenes
        self._scenes: Dict[str, VoxelScene] = {}
        self._active_scene: Optional[str] = None
        
        # Performance tracking
        self._loaded_assets: Set[str] = set()
        self._total_voxels_loaded = 0
        self._memory_used_mb = 0.0
        
        # Load library
        self._load_asset_library()
        
        logger.info(f"🎨 Voxel Asset Manager initialized: {len(self._assets)} assets")
    
    def _load_asset_library(self) -> None:
        """Load and parse the asset library JSON"""
        if not self.library_path.exists():
            logger.error(f"Asset library not found: {self.library_path}")
            self._load_saved_voxel_sets()
            return
        
        try:
            with open(self.library_path, 'r') as f:
                self._asset_library = json.load(f)
            
            # Build asset catalog
            self._build_asset_catalog()
            self._load_saved_voxel_sets()
            
            logger.info(f"✅ Loaded asset library: {self.library_path}")
        except Exception as e:
            logger.error(f"Failed to load asset library: {e}", exc_info=True)
    
    def _build_asset_catalog(self) -> None:
        """Build asset catalog from library JSON"""
        # Furniture
        if "furniture" in self._asset_library:
            for quality_tier, items in self._asset_library["furniture"].items():
                quality = AssetQuality.STANDARD if quality_tier == "standard" else AssetQuality.FANCY
                for item in items:
                    asset_id = f"furniture_{quality_tier}_{item}"
                    self._assets[asset_id] = VoxelAsset(
                        asset_id=asset_id,
                        name=item.replace("_", " ").title(),
                        category=AssetCategory.FURNITURE,
                        quality=quality
                    )
        
        # Home Structure
        if "home_structure" in self._asset_library:
            for quality_tier, items in self._asset_library["home_structure"].items():
                quality = AssetQuality.STANDARD if quality_tier == "standard" else AssetQuality.FANCY
                for item in items:
                    asset_id = f"structure_{quality_tier}_{item}"
                    self._assets[asset_id] = VoxelAsset(
                        asset_id=asset_id,
                        name=item.replace("_", " ").title(),
                        category=AssetCategory.HOME_STRUCTURE,
                        quality=quality
                    )
        
        # Rooms (treated as complete prefabs)
        if "rooms" in self._asset_library:
            for room in self._asset_library["rooms"]:
                asset_id = f"room_{room}"
                self._assets[asset_id] = VoxelAsset(
                    asset_id=asset_id,
                    name=room.replace("_", " ").title(),
                    category=AssetCategory.ROOMS,
                    quality=AssetQuality.STANDARD
                )
        
        # Outdoor
        if "outdoor" in self._asset_library:
            for quality_tier, items in self._asset_library["outdoor"].items():
                quality = AssetQuality.STANDARD if quality_tier == "standard" else AssetQuality.FANCY
                for item in items:
                    asset_id = f"outdoor_{quality_tier}_{item}"
                    self._assets[asset_id] = VoxelAsset(
                        asset_id=asset_id,
                        name=item.replace("_", " ").title(),
                        category=AssetCategory.OUTDOOR,
                        quality=quality
                    )
        
        # Vehicles
        if "vehicles" in self._asset_library:
            for quality_tier, items in self._asset_library["vehicles"].items():
                quality = AssetQuality.STANDARD if quality_tier == "standard" else AssetQuality.FANCY
                for item in items:
                    asset_id = f"vehicle_{quality_tier}_{item}"
                    self._assets[asset_id] = VoxelAsset(
                        asset_id=asset_id,
                        name=item.replace("_", " ").title(),
                        category=AssetCategory.VEHICLES,
                        quality=quality
                    )
        
        # Props
        if "props" in self._asset_library:
            for prop in self._asset_library["props"]:
                asset_id = f"prop_{prop}"
                self._assets[asset_id] = VoxelAsset(
                    asset_id=asset_id,
                    name=prop.replace("_", " ").title(),
                    category=AssetCategory.PROPS,
                    quality=AssetQuality.STANDARD
                )
        
        logger.info(f"📦 Built asset catalog: {len(self._assets)} total assets")
    
    # ─────────────────────────────────────────────
    # ASSET QUERIES
    # ─────────────────────────────────────────────
    
    def _load_saved_voxel_sets(self) -> None:
        """Expose saved PubWorld voxel-set JSON files through the asset catalog."""
        if load_voxel_set is None:
            return
        data_dir = self.library_path.parent
        voxel_sets_dir = data_dir / "pubworld" / "voxel_sets"
        if not voxel_sets_dir.exists():
            voxel_sets_dir = data_dir.parent / "pubworld" / "voxel_sets"
        if not voxel_sets_dir.exists():
            return
        loaded = 0
        for path in voxel_sets_dir.glob("*.json"):
            try:
                payload = load_voxel_set(path)
            except Exception as exc:
                logger.warning("Skipping invalid voxel set %s: %s", path, exc)
                continue
            asset_id = payload["asset_id"]
            self._assets[asset_id] = VoxelAsset(
                asset_id=asset_id,
                name=payload["name"],
                category=AssetCategory.PROPS,
                quality=AssetQuality.STANDARD,
                voxel_count=len(payload.get("blocks") or []),
                file_path=str(path),
            )
            loaded += 1
        if loaded:
            logger.info("Loaded %d saved PubWorld voxel sets", loaded)

    def get_asset(self, asset_id: str) -> Optional[VoxelAsset]:
        """Get asset by ID"""
        return self._assets.get(asset_id)
    
    def get_assets_by_category(
        self,
        category: AssetCategory,
        quality: Optional[AssetQuality] = None
    ) -> List[VoxelAsset]:
        """Get all assets in a category"""
        assets = [
            asset for asset in self._assets.values()
            if asset.category == category
        ]
        
        if quality:
            assets = [a for a in assets if a.quality == quality]
        
        return assets
    
    def search_assets(self, query: str) -> List[VoxelAsset]:
        """Search assets by name"""
        query_lower = query.lower()
        return [
            asset for asset in self._assets.values()
            if query_lower in asset.name.lower() or query_lower in asset.asset_id.lower()
        ]
    
    def get_all_assets(self) -> List[VoxelAsset]:
        """Get complete asset list"""
        return list(self._assets.values())
    
    # ─────────────────────────────────────────────
    # SCENE MANAGEMENT
    # ─────────────────────────────────────────────
    
    def create_scene(
        self,
        scene_id: str,
        name: str,
        description: str = ""
    ) -> VoxelScene:
        """Create new scene"""
        scene = VoxelScene(
            scene_id=scene_id,
            name=name,
            description=description
        )
        
        self._scenes[scene_id] = scene
        logger.info(f"🎬 Created scene: {name} ({scene_id})")
        
        return scene
    
    def get_scene(self, scene_id: str) -> Optional[VoxelScene]:
        """Get scene by ID"""
        return self._scenes.get(scene_id)
    
    def set_active_scene(self, scene_id: str) -> bool:
        """Set the active scene"""
        if scene_id not in self._scenes:
            logger.error(f"Scene not found: {scene_id}")
            return False
        
        self._active_scene = scene_id
        logger.info(f"🎬 Active scene: {scene_id}")
        return True
    
    def get_active_scene(self) -> Optional[VoxelScene]:
        """Get the currently active scene"""
        if not self._active_scene:
            return None
        return self._scenes.get(self._active_scene)
    
    def load_scene_from_file(self, file_path: Path) -> Optional[VoxelScene]:
        """Load scene from JSON file"""
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
            
            scene = VoxelScene(
                scene_id=data["scene_id"],
                name=data["name"],
                description=data.get("description", "")
            )
            
            # Load instances
            for inst_data in data.get("instances", []):
                instance = SceneAssetInstance(**inst_data)
                scene.add_instance(instance)
            
            # Load environment
            if "environment" in data:
                env = data["environment"]
                scene.ambient_light = env.get("ambient_light", 0.3)
                scene.directional_light = env.get("directional_light", [0.5, -1.0, 0.3])
                scene.fog_enabled = env.get("fog_enabled", False)
                scene.fog_distance = env.get("fog_distance", 100.0)
            
            # Load camera
            if "camera" in data:
                cam = data["camera"]
                scene.camera_position = cam.get("position", [0.0, 1.6, 5.0])
                scene.camera_target = cam.get("target", [0.0, 1.0, 0.0])
            
            # Load performance settings
            if "performance" in data:
                perf = data["performance"]
                scene.max_draw_distance = perf.get("max_draw_distance", 50.0)
                scene.lod_bias = perf.get("lod_bias", 1.0)
            
            self._scenes[scene.scene_id] = scene
            logger.info(f"✅ Loaded scene from file: {file_path}")
            
            return scene
            
        except Exception as e:
            logger.error(f"Failed to load scene from {file_path}: {e}", exc_info=True)
            return None
    
    def save_scene_to_file(self, scene_id: str, file_path: Path) -> bool:
        """Save scene to JSON file"""
        scene = self.get_scene(scene_id)
        if not scene:
            logger.error(f"Scene not found: {scene_id}")
            return False
        
        try:
            with open(file_path, 'w') as f:
                json.dump(scene.to_dict(), f, indent=2)
            
            logger.info(f"💾 Saved scene to file: {file_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save scene: {e}", exc_info=True)
            return False
    
    # ─────────────────────────────────────────────
    # PREFLIGHT INTEGRATION
    # ─────────────────────────────────────────────
    
    async def validate_scene_for_preflight(
        self,
        scene_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Validate scene for Studio Control Room preflight
        
        Called during the 5-second countdown to verify:
        - All required assets are available
        - Scene complexity is within limits
        - Estimated memory usage is acceptable
        - No missing dependencies
        
        Returns validation report for preflight check system
        """
        if scene_id is None:
            scene_id = self._active_scene
        
        if not scene_id:
            return {
                "passed": False,
                "errors": ["No active scene set"],
                "warnings": [],
                "info": {}
            }
        
        scene = self.get_scene(scene_id)
        if not scene:
            return {
                "passed": False,
                "errors": [f"Scene not found: {scene_id}"],
                "warnings": [],
                "info": {}
            }
        
        errors = []
        warnings = []
        info = {}
        
        # Check required assets exist
        missing_assets = []
        for asset_id in scene.required_assets:
            if asset_id not in self._assets:
                missing_assets.append(asset_id)
        
        if missing_assets:
            errors.append(f"Missing assets: {', '.join(missing_assets)}")
        
        # Check scene complexity
        instance_count = len(scene.instances)
        info["instance_count"] = instance_count
        
        if instance_count > 1000:
            errors.append(f"Scene too complex: {instance_count} instances (max 1000)")
        elif instance_count > 500:
            warnings.append(f"High instance count: {instance_count} (may impact performance)")
        
        # Estimate memory usage
        estimated_memory_mb = len(scene.required_assets) * 2.5  # Rough estimate
        info["estimated_memory_mb"] = estimated_memory_mb
        
        if estimated_memory_mb > 512:
            errors.append(f"Memory usage too high: {estimated_memory_mb}MB (max 512MB)")
        elif estimated_memory_mb > 256:
            warnings.append(f"High memory usage: {estimated_memory_mb}MB")
        
        # Check camera setup
        if not scene.camera_position or not scene.camera_target:
            warnings.append("Camera not configured")
        
        info["scene_name"] = scene.name
        info["required_assets"] = len(scene.required_assets)
        
        return {
            "passed": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "info": info
        }
    
    async def preload_scene_assets(self, scene_id: Optional[str] = None) -> bool:
        """
        Preload all assets required for a scene
        
        Called during preflight to ensure assets are loaded
        before recording starts.
        """
        if scene_id is None:
            scene_id = self._active_scene
        
        if not scene_id:
            logger.error("No active scene to preload")
            return False
        
        scene = self.get_scene(scene_id)
        if not scene:
            logger.error(f"Scene not found: {scene_id}")
            return False
        
        logger.info(f"🔄 Preloading {len(scene.required_assets)} assets for scene: {scene.name}")
        
        # TODO: Integrate with Pete Enhanced to actually load assets
        # For now, simulate loading
        for asset_id in scene.required_assets:
            asset = self.get_asset(asset_id)
            if asset:
                asset.load_state = AssetLoadState.LOADED
                self._loaded_assets.add(asset_id)
        
        logger.info(f"✅ Scene assets preloaded: {scene.name}")
        return True
    
    # ─────────────────────────────────────────────
    # PETE ENHANCED INTEGRATION
    # ─────────────────────────────────────────────
    
    async def sync_with_rust_engine(self, scene: VoxelScene) -> bool:
        """
        Sync scene to Rust voxel engine via Pete Enhanced
        
        This sends the complete scene composition to the Rust engine
        for rendering.
        """
        if not self.pete and not self.config.get("bridge"):
            logger.warning("Pete Enhanced not connected - cannot sync with Rust engine")
            return False
        
        logger.info(f"🎨 Syncing scene to Rust engine: {scene.name}")
        
        bridge = self.config.get("bridge")
        if bridge is None and self.pete is not None:
            bridge = getattr(self.pete, "bridge", None)

        if bridge is not None:
            if hasattr(bridge, "load_scene"):
                success = bool(bridge.load_scene(scene.scene_id, scene.to_dict()))
            elif hasattr(bridge, "send_command"):
                success = bool(bridge.send_command("LOAD_SCENE", {"scene": scene.scene_id, "data": scene.to_dict()}))
            else:
                success = False
            if not success:
                logger.warning(f"Voxel bridge refused scene sync: {scene.name}")
                return False
        
        logger.info(f"✅ Scene synced to Rust engine: {scene.name}")
        return True
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """Get current performance statistics"""
        return {
            "loaded_assets": len(self._loaded_assets),
            "total_assets": len(self._assets),
            "memory_used_mb": self._memory_used_mb,
            "active_scene": self._active_scene,
            "scene_count": len(self._scenes)
        }
    
    # ─────────────────────────────────────────────
    # PRESET SCENES
    # ─────────────────────────────────────────────
    
    def create_preset_scene_detective_office(self) -> VoxelScene:
        """Create Detective Humphrey's noir office scene"""
        scene = self.create_scene(
            "detective_office_noir",
            "Detective's Office",
            "Noir-style detective office for Detective Humphrey"
        )
        
        # Furniture
        scene.add_instance(SceneAssetInstance(
            instance_id="desk_1",
            asset_id="furniture_standard_desk",
            position=[0.0, 0.0, 0.0]
        ))
        
        scene.add_instance(SceneAssetInstance(
            instance_id="chair_1",
            asset_id="furniture_standard_chair",
            position=[0.0, 0.0, 1.5]
        ))
        
        scene.add_instance(SceneAssetInstance(
            instance_id="bookshelf_1",
            asset_id="furniture_standard_bookshelf",
            position=[-2.0, 0.0, 0.0]
        ))
        
        # Props
        scene.add_instance(SceneAssetInstance(
            instance_id="lamp_1",
            asset_id="prop_lamp",
            position=[0.0, 0.8, 0.0]
        ))
        
        # Lighting (noir style - low ambient, strong directional)
        scene.ambient_light = 0.15
        scene.directional_light = [0.8, -0.5, 0.3]
        
        # Camera
        scene.camera_position = [3.0, 1.6, 4.0]
        scene.camera_target = [0.0, 1.0, 0.0]
        
        logger.info("🕵️ Created Detective Office scene")
        return scene
    
    def create_preset_scene_living_room(self) -> VoxelScene:
        """Create standard living room scene"""
        scene = self.create_scene(
            "living_room_standard",
            "Living Room",
            "Standard family living room"
        )
        
        # Furniture
        scene.add_instance(SceneAssetInstance(
            instance_id="couch_1",
            asset_id="furniture_standard_couch",
            position=[0.0, 0.0, 0.0]
        ))
        
        scene.add_instance(SceneAssetInstance(
            instance_id="coffee_table_1",
            asset_id="furniture_standard_coffee_table",
            position=[0.0, 0.0, 1.5]
        ))
        
        scene.add_instance(SceneAssetInstance(
            instance_id="tv_stand_1",
            asset_id="furniture_standard_tv_stand",
            position=[0.0, 0.0, -2.5]
        ))
        
        # Standard lighting
        scene.ambient_light = 0.4
        scene.directional_light = [0.5, -1.0, 0.3]
        
        logger.info("🏠 Created Living Room scene")
        return scene
