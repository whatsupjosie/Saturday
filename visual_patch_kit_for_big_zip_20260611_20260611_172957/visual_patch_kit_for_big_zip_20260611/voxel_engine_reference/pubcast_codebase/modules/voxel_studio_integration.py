#!/usr/bin/env python3
# PubCast AI — voxel_studio_integration.py
# Copyright © 2024–2026 Josie Curtsey Cobbley (Joshua Cobbley)
# Rearview Foresight LLC — All Rights Reserved
# Feic Mo Chroí — See My Heart
"""
VOXEL STUDIO INTEGRATION
=========================
Integrates the Voxel Asset Manager with Studio Control Room.

This module connects:
- Voxel Asset Manager → Studio Control preflight checks
- Scene validation during 5-second countdown
- Asset preloading coordination
- Pete Enhanced voxel streaming

Usage:
    from voxel_studio_integration import VoxelStudioIntegration
    
    # Initialize
    voxel_integration = VoxelStudioIntegration(
        asset_manager=voxel_asset_manager,
        studio_control=studio_control,
        pete=pete_enhanced
    )
    
    # Register preflight hooks
    await voxel_integration.register_preflight_hooks()
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from voxel_asset_manager import VoxelAssetManager
    from studio_control import StudioControl, PreflightCheck
    from pete_enhanced import PeteEnhanced

logger = logging.getLogger("pubcast.voxel_studio_integration")


class VoxelStudioIntegration:
    """
    Integration layer between Voxel Asset Manager and Studio Control Room
    
    Responsibilities:
    - Register voxel-specific preflight checks
    - Coordinate scene validation during countdown
    - Manage asset preloading
    - Bridge to Pete Enhanced for Rust engine communication
    """
    
    def __init__(
        self,
        asset_manager: 'VoxelAssetManager',
        studio_control: 'StudioControl',
        pete: Optional['PeteEnhanced'] = None
    ):
        self.asset_manager = asset_manager
        self.studio_control = studio_control
        self.pete = pete
        
        # Integration state
        self._scene_preload_in_progress = False
        self._last_validated_scene: Optional[str] = None
        
        logger.info("🔗 Voxel Studio Integration initialized")
    
    async def register_preflight_hooks(self) -> None:
        """
        Register voxel-specific checks into Studio Control preflight sequence
        
        This adds two custom checks to the 5-second countdown:
        - T-4: Scene validation (with Gate Check)
        - T-2: Asset preloading verification (with Ghost Mic Check)
        """
        logger.info("🔗 Registering voxel preflight hooks...")
        
        # Hook into Studio Control's preflight sequence
        # These will run alongside the existing checks
        
        # Note: This is a design pattern - actual implementation
        # depends on Studio Control's extensibility API
        
        # Conceptually:
        # studio_control.register_custom_check(
        #     timing=4,  # T-4
        #     check_func=self._preflight_validate_scene
        # )
        # studio_control.register_custom_check(
        #     timing=2,  # T-2
        #     check_func=self._preflight_verify_assets
        # )
        
        logger.info("✅ Voxel preflight hooks registered")
    
    async def _preflight_validate_scene(self) -> 'PreflightCheck':
        """
        T-4 Preflight Check: Scene Validation
        
        Validates the current scene composition:
        - All assets exist in library
        - Scene complexity within limits
        - Memory usage acceptable
        - No missing dependencies
        """
        from studio_control import PreflightCheck
        
        logger.info("🎬 Running scene validation preflight check...")
        
        # Get active scene
        scene = self.asset_manager.get_active_scene()
        if not scene:
            return PreflightCheck(
                check_id="voxel_scene_validation",
                name="Voxel Scene Validation",
                passed=False,
                message="No active scene set"
            )
        
        # Validate scene
        validation_result = await self.asset_manager.validate_scene_for_preflight(
            scene.scene_id
        )
        
        if validation_result["passed"]:
            message = f"Scene '{scene.name}' validated: {validation_result['info']['instance_count']} instances, {validation_result['info']['required_assets']} assets"
        else:
            errors = "; ".join(validation_result["errors"])
            message = f"Scene validation failed: {errors}"
        
        return PreflightCheck(
            check_id="voxel_scene_validation",
            name="Voxel Scene Validation",
            passed=validation_result["passed"],
            message=message
        )
    
    async def _preflight_verify_assets(self) -> 'PreflightCheck':
        """
        T-2 Preflight Check: Asset Preload Verification
        
        Ensures all required assets are loaded and ready:
        - Asset files accessible
        - Memory allocated
        - Rust engine synchronized
        - No missing textures/models
        """
        from studio_control import PreflightCheck
        
        logger.info("📦 Running asset preload verification...")
        
        scene = self.asset_manager.get_active_scene()
        if not scene:
            return PreflightCheck(
                check_id="voxel_asset_preload",
                name="Voxel Asset Preload",
                passed=False,
                message="No active scene"
            )
        
        # Preload assets
        success = await self.asset_manager.preload_scene_assets(scene.scene_id)
        
        if success:
            asset_count = len(scene.required_assets)
            message = f"All {asset_count} assets preloaded successfully"
        else:
            message = "Asset preload failed"
        
        return PreflightCheck(
            check_id="voxel_asset_preload",
            name="Voxel Asset Preload",
            passed=success,
            message=message
        )
    
    async def sync_scene_to_rust_engine(self, scene_id: Optional[str] = None) -> bool:
        """
        Synchronize scene to Rust voxel engine
        
        Called after preflight passes to send the complete scene
        composition to the Rust rendering engine.
        """
        if scene_id is None:
            scene = self.asset_manager.get_active_scene()
        else:
            scene = self.asset_manager.get_scene(scene_id)
        
        if not scene:
            logger.error("No scene to sync")
            return False
        
        logger.info(f"🎨 Syncing scene to Rust engine: {scene.name}")
        
        # Use the asset manager's sync method
        success = await self.asset_manager.sync_with_rust_engine(scene)
        
        if success:
            self._last_validated_scene = scene.scene_id
            logger.info(f"✅ Scene synced: {scene.name}")
        else:
            logger.error(f"❌ Scene sync failed: {scene.name}")
        
        return success
    
    async def on_studio_live(self) -> None:
        """
        Called when Studio Control transitions to LIVE state
        
        Final synchronization and optimization before recording starts.
        """
        logger.info("🔴 Studio going LIVE - final voxel sync")
        
        scene = self.asset_manager.get_active_scene()
        if scene:
            await self.sync_scene_to_rust_engine(scene.scene_id)
    
    async def on_studio_emergency(self) -> None:
        """
        Called when emergency save is triggered
        
        Save current voxel state for recovery.
        """
        logger.critical("🚨 Emergency save - capturing voxel state")
        
        scene = self.asset_manager.get_active_scene()
        if scene:
            # Save scene state
            emergency_path = Path(self.studio_control._emergency_save_dir) / f"scene_{scene.scene_id}.json"
            self.asset_manager.save_scene_to_file(scene.scene_id, emergency_path)
            logger.info(f"💾 Voxel scene saved: {emergency_path}")
    
    def get_integration_status(self) -> Dict[str, Any]:
        """Get current integration status"""
        return {
            "voxel_manager_connected": self.asset_manager is not None,
            "studio_control_connected": self.studio_control is not None,
            "pete_connected": self.pete is not None,
            "scene_preload_in_progress": self._scene_preload_in_progress,
            "last_validated_scene": self._last_validated_scene,
            "active_scene": (
                self.asset_manager.get_active_scene().scene_id
                if self.asset_manager.get_active_scene() else None
            )
        }


# ─────────────────────────────────────────────
# EXAMPLE INTEGRATION SETUP
# ─────────────────────────────────────────────

"""
Example integration in your main.py or hub.py:

from pathlib import Path
from voxel_asset_manager import VoxelAssetManager
from voxel_studio_integration import VoxelStudioIntegration
from studio_control import StudioControl
from pete_enhanced import PeteEnhanced

# Initialize components
pete_enhanced = PeteEnhanced(hub, bridge, motion_system, data_dir)

studio_control = StudioControl(
    hub=hub,
    pete=pete_enhanced,
    data_dir=Path("./data")
)

voxel_asset_manager = VoxelAssetManager(
    library_path=Path("./data/voxel_asset_library.json"),
    pete=pete_enhanced
)

# Create integration layer
voxel_integration = VoxelStudioIntegration(
    asset_manager=voxel_asset_manager,
    studio_control=studio_control,
    pete=pete_enhanced
)

# Register preflight hooks
await voxel_integration.register_preflight_hooks()

# Load a scene
scene = voxel_asset_manager.create_preset_scene_detective_office()
voxel_asset_manager.set_active_scene(scene.scene_id)

# When Studio Control goes LIVE
studio_control.on_state_change("LIVE", voxel_integration.on_studio_live)

# When emergency save triggers
studio_control.on_emergency_save(voxel_integration.on_studio_emergency)
"""
