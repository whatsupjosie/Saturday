import asyncio
import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from runtime.spine import EventBus, PerformerRegistry, StationRegistry
from runtime.spine.visual import (
    PatchRequester,
    PatchWorkflowRequest,
    VisualPatchUpKit,
    VisualPatchWorkflow,
    VisualSelection,
    VisualStylingAide,
    parse_styling_request,
)
from runtime.spine.performers import (
    AnimationAuthority,
    AnimationRuntime,
    ContactObject,
    ContactResolver,
    InteractionFitCoordinator,
    LocomotionSystem,
    MotionFeedbackCoordinator,
    MotionRetargeter,
    RETARGET_MODE_LITERAL_SOURCE,
    RETARGET_MODE_TARGET_EQUIVALENT,
    build_basic_recovery_sequence,
    build_full_animation_rehearsal_sequence,
    build_waltz_duet_sequence,
    create_avatar_skeleton,
    diagnose_mocap_skeleton_boundary,
    export_sequences,
    get_animation_presets,
    measure_skeleton_metrics,
    normalize_animation_name,
    VisualPatchCoordinator,
    parse_visual_patch_request,
    validate_animation_presets,
)
from runtime.spine.stations import BaseStation


class DummyStation(BaseStation):
    def on_activated(self, performer_id: str) -> bool:
        self.local_state["activated_by"] = performer_id
        return True

    def on_deactivated(self, performer_id: str):
        self.local_state["deactivated_by"] = performer_id

    def on_interaction(self, performer_id: str, interaction_data: dict) -> dict:
        self.local_state["last_interaction"] = interaction_data
        return {"ok": True}

    def get_state(self) -> dict:
        return {"active_performer": self.active_performer, "local_state": dict(self.local_state)}


class TinySkeleton:
    def __init__(self, scale: float = 1.0):
        base = {
            "root": [0.0, 0.0, 0.0],
            "hips": [0.0, 0.9, 0.0],
            "spine_01": [0.0, 1.0, 0.0],
            "spine_02": [0.0, 1.15, 0.0],
            "spine_03": [0.0, 1.3, 0.0],
            "chest": [0.0, 1.4, 0.0],
            "head": [0.0, 1.7, 0.0],
            "upperarm_l": [-0.17, 1.4, 0.0],
            "lowerarm_l": [-0.45, 1.4, 0.0],
            "hand_l": [-0.7, 1.4, 0.0],
            "upperarm_r": [0.17, 1.4, 0.0],
            "lowerarm_r": [0.45, 1.4, 0.0],
            "hand_r": [0.7, 1.4, 0.0],
            "thigh_l": [-0.1, 0.85, 0.0],
            "calf_l": [-0.1, 0.45, 0.0],
            "foot_l": [-0.1, 0.08, 0.0],
            "thigh_r": [0.1, 0.85, 0.0],
            "calf_r": [0.1, 0.45, 0.0],
            "foot_r": [0.1, 0.08, 0.0],
        }
        self.joints = {name: SimpleNamespace(position=[axis * scale for axis in position], locked_axes=[]) for name, position in base.items()}
        self.bind_pose = {
            name: {"position": [axis * scale for axis in position], "rotation": [0, 0, 0, 1], "scale": [1, 1, 1]}
            for name, position in base.items()
        }


class BirdSkeleton:
    def __init__(self):
        points = {
            "root": [0.0, 0.0, 0.0],
            "body": [0.0, 0.7, 0.0],
            "head": [0.0, 1.25, 0.2],
            "wing_l": [-0.35, 0.85, 0.0],
            "wing_r": [0.35, 0.85, 0.0],
            "leg_l": [-0.08, 0.35, 0.0],
            "leg_r": [0.08, 0.35, 0.0],
            "knee_l": [-0.08, 0.18, 0.0],
            "knee_r": [0.08, 0.18, 0.0],
            "talon_l": [-0.08, 0.0, 0.04],
            "talon_r": [0.08, 0.0, 0.04],
        }
        self.joints = {
            name: SimpleNamespace(position=position, locked_axes=[], rotation_limits=None)
            for name, position in points.items()
        }
        self.joints["knee_l"].rotation_limits = {"x": [-0.3, 1.2], "y": [-0.15, 0.15], "z": [-0.2, 0.2]}
        self.bind_pose = {
            name: {"position": list(position), "rotation": [0, 0, 0, 1], "scale": [1, 1, 1]}
            for name, position in points.items()
        }


class SpineRuntimeTests(unittest.TestCase):
    def test_event_bus_emits_immutable_events_and_unsubscribes(self):
        bus = EventBus()
        received = []
        token = bus.subscribe("performer:spawned", received.append)

        event = bus.emit("performer:spawned", {"performer_id": "p1"}, source="test")

        self.assertEqual(received[0]["event_type"], "performer:spawned")
        self.assertEqual(event["data"]["performer_id"], "p1")
        with self.assertRaises(TypeError):
            event["data"]["performer_id"] = "p2"
        bus.unsubscribe(token)
        bus.emit("performer:spawned", {"performer_id": "p3"}, source="test")
        self.assertEqual(len(received), 1)

    def test_event_bus_async_emit(self):
        bus = EventBus()
        received = []

        async def handler(event):
            received.append(event["data"]["value"])

        async def run():
            bus.subscribe("pub:message", handler)
            await bus.emit_async("pub:message", {"value": 42}, source="test")

        asyncio.run(run())
        self.assertEqual(received, [42])

    def test_event_bus_isolates_handler_failures(self):
        bus = EventBus()
        received = []

        def bad_handler(event):
            raise RuntimeError("boom")

        def good_handler(event):
            received.append(event["data"]["value"])

        bus.subscribe("pub:message", bad_handler)
        bus.subscribe("pub:message", good_handler)

        bus.emit("pub:message", {"value": 7}, source="test")

        self.assertEqual(received, [7])
        self.assertEqual(bus.errors()[-1]["data"]["source_event_type"], "pub:message")
        self.assertEqual(bus.errors()[-1]["data"]["error_type"], "RuntimeError")

    def test_performer_registry_owns_state_and_emits_events(self):
        bus = EventBus()
        events = []
        bus.subscribe("*", events.append)
        registry = PerformerRegistry(bus, bind_default_skeleton=False)

        performer = registry.spawn_performer("p1", "Pete", [0, 0, 0], "studio")
        registry.update_performer_transform("p1", [1, 0, 0], [0, 0, 0, 1])
        registry.set_locomotion_state("p1", "walking")
        registry.apply_animation_frame("p1", {"head": {"rotation": [0, 0, 0, 1]}})

        self.assertEqual(performer.position, [1.0, 0.0, 0.0])
        self.assertTrue(performer.dirty)
        json.dumps(performer.to_dict())
        self.assertEqual(
            [event["event_type"] for event in events],
            ["performer:spawned", "performer:moved", "performer:state_change", "performer:animated"],
        )

    def test_performer_station_activation_emits_locomotion_state_changes(self):
        bus = EventBus()
        events = []
        bus.subscribe("*", events.append)
        registry = PerformerRegistry(bus, bind_default_skeleton=False)
        performer = registry.spawn_performer("p1", "Pete", [0, 0, 0], "studio")

        registry.activate_station("p1", "desk")
        registry.deactivate_station("p1")

        event_types = [event["event_type"] for event in events]
        self.assertEqual(performer.active_station, None)
        self.assertEqual(performer.locomotion_state, "idle")
        self.assertIn("station:activated", event_types)
        self.assertIn("station:deactivated", event_types)
        self.assertEqual(
            [
                event["data"]["after"]
                for event in events
                if event["event_type"] == "performer:state_change"
            ],
            ["interacting", "idle"],
        )

    def test_performer_spawn_uses_pubcast_skeleton_binding(self):
        bus = EventBus()
        registry = PerformerRegistry(bus)

        performer = registry.spawn_performer("p1", "Pete", [0, 0, 0], "studio")
        data = performer.to_dict()

        self.assertIsNotNone(performer.avatar_skeleton)
        self.assertGreater(data["avatar_skeleton"]["joint_count"], 0)
        self.assertIn("head", data["avatar_skeleton"]["joints"])
        self.assertIn("head", performer.skeleton_pose)
        json.dumps(data)

    def test_spine_can_create_pubcast_skeleton_directly(self):
        skeleton = create_avatar_skeleton()

        self.assertIsNotNone(skeleton)
        self.assertIn("hips", skeleton.joints)
        self.assertIn("head", skeleton.get_retargeting_map())

    def test_animation_presets_match_pubcast_skeleton(self):
        skeleton = create_avatar_skeleton()
        issues = validate_animation_presets(skeleton)

        self.assertEqual([issue for issue in issues if issue.severity == "error"], [])
        self.assertEqual(normalize_animation_name("sit down and watch tv on couch"), "sit")
        for required in (
            "walk",
            "sit",
            "fall_down",
            "get_back_up",
            "sit_to_stand",
            "stand",
            "waltz_box_step_male",
            "waltz_box_step_female",
            "feminine_high_heel_walk",
        ):
            self.assertIn(required, get_animation_presets())

    def test_animation_runtime_runs_required_basics_and_sheila_waltz(self):
        bus = EventBus()
        registry = PerformerRegistry(bus)
        authority = AnimationAuthority()
        runtime = AnimationRuntime(registry, authority, bus)
        registry.spawn_performer("male_part_1", "Male Part 1", [0, 0, 0], "studio")
        registry.spawn_performer("sheila", "Sheila", [0.8, 0, 0], "studio", avatar_model_id="SHELA")

        full = build_full_animation_rehearsal_sequence("male_part_1", "sheila")
        sample_times = [0.5, 3.4, 5.8, 8.8, 11.6, 15.2, 18.4, 21.0, 27.5, 34.0]
        applied_counts = []
        for elapsed in sample_times:
            applied = runtime.apply_sequence(full, elapsed)
            applied_counts.append(len(applied))

        self.assertGreater(full.duration, 30.0)
        self.assertIn("hips", registry.get_performer("male_part_1").skeleton_pose)
        self.assertIn("hips", registry.get_performer("sheila").skeleton_pose)
        self.assertGreaterEqual(max(applied_counts), 2)

    def test_motion_retargeter_scales_root_motion_to_avatar_size(self):
        retargeter = MotionRetargeter()
        pose = {"root": {"position": [0, 0, -2.0]}}

        retargeted = retargeter.retarget_pose(pose, TinySkeleton(scale=2.0), motion_intent="walk")
        metrics = measure_skeleton_metrics(TinySkeleton(scale=2.0))

        self.assertAlmostEqual(metrics.ratio("height"), 2.0, places=2)
        self.assertAlmostEqual(retargeted["root"]["position"][2], -4.0, places=2)

    def test_motion_retargeter_translates_dog_sit_without_forcing_paw_hands(self):
        retargeter = MotionRetargeter()
        skeleton = create_avatar_skeleton()
        dog_sit_pose = {
            "hind_leg_l": {"rotation": [2.2, 0.0, 0.0]},
            "hind_knee_l": {"rotation": [-2.0, 0.0, 0.0]},
            "front_paw_l": {"rotation": [2.0, 0.0, 0.0]},
            "tail": {"rotation": [0.0, 0.0, 1.6]},
            "mystery_whisker": {"rotation": [1.0, 1.0, 1.0]},
        }

        retargeted, report = retargeter.retarget_pose_with_report(dog_sit_pose, skeleton, motion_intent="dog sit")

        self.assertIn("thigh_l", retargeted)
        self.assertIn("calf_l", retargeted)
        self.assertIn("hand_l", retargeted)
        self.assertIn("hips", retargeted)
        self.assertAlmostEqual(retargeted["hand_l"]["rotation"][0], 0.36, places=2)
        self.assertAlmostEqual(retargeted["hips"]["rotation"][2], 0.4, places=2)
        self.assertIn("mystery_whisker", report.dropped_joints)

    def test_motion_retargeter_maps_human_motion_to_bird_like_rig_limits(self):
        retargeter = MotionRetargeter()
        human_walk_pose = {
            "upperarm_l": {"rotation": [-0.8, 0.0, 0.0]},
            "upperarm_r": {"rotation": [0.8, 0.0, 0.0]},
            "calf_l": {"rotation": [-2.0, 0.4, 0.0]},
            "foot_l": {"rotation": [-0.6, 0.0, 0.0]},
            "index_01_l": {"rotation": [0.8, 0.0, 0.0]},
        }

        retargeted, report = retargeter.retarget_pose_with_report(human_walk_pose, BirdSkeleton(), motion_intent="human walk")

        self.assertIn("wing_l", retargeted)
        self.assertIn("wing_r", retargeted)
        self.assertIn("knee_l", retargeted)
        self.assertIn("talon_l", retargeted)
        self.assertAlmostEqual(retargeted["knee_l"]["rotation"][0], -0.3, places=2)
        self.assertIn("index_01_l", report.dropped_joints)
        self.assertIn("knee_l.rotation", report.clamped_channels)

    def test_motion_retargeter_converts_bird_motion_to_human_equivalent_knees(self):
        retargeter = MotionRetargeter()
        bird_walk_pose = {
            "wing_l": {"rotation": [-0.7, 0.0, 0.0]},
            "knee_l": {"rotation": [1.2, 0.0, 0.0]},
            "talon_l": {"rotation": [0.6, 0.0, 0.0]},
        }

        retargeted, report = retargeter.retarget_pose_with_report(
            bird_walk_pose,
            create_avatar_skeleton(),
            motion_intent="bird walk",
        )

        self.assertIn("upperarm_l", retargeted)
        self.assertIn("calf_l", retargeted)
        self.assertIn("foot_l", retargeted)
        self.assertLess(retargeted["calf_l"]["rotation"][0], 0)
        self.assertLess(retargeted["foot_l"]["rotation"][0], 0)
        self.assertEqual(report.dropped_joints, [])

    def test_motion_retargeter_literal_mode_allows_source_shadow_bend(self):
        retargeter = MotionRetargeter()
        bird_walk_pose = {
            "knee_l": {"rotation": [1.2, 0.0, 0.0]},
            "talon_l": {"rotation": [0.6, 0.0, 0.0]},
        }

        natural, natural_report = retargeter.retarget_pose_with_report(
            bird_walk_pose,
            create_avatar_skeleton(),
            motion_intent="bird walk",
            retarget_mode=RETARGET_MODE_TARGET_EQUIVALENT,
        )
        literal, literal_report = retargeter.retarget_pose_with_report(
            bird_walk_pose,
            create_avatar_skeleton(),
            motion_intent="bird walk",
            retarget_mode=RETARGET_MODE_LITERAL_SOURCE,
        )

        self.assertLess(natural["calf_l"]["rotation"][0], 0)
        self.assertGreater(literal["calf_l"]["rotation"][0], 0)
        self.assertEqual(natural_report.to_dict()["retarget_mode"], RETARGET_MODE_TARGET_EQUIVALENT)
        self.assertEqual(literal_report.to_dict()["retarget_mode"], RETARGET_MODE_LITERAL_SOURCE)

    def test_motion_retargeter_scores_extreme_body_proportions_as_risky(self):
        retargeter = MotionRetargeter()
        pose = {"root": {"position": [0, 0, -1.0]}}

        retargeted, report = retargeter.retarget_pose_with_report(
            pose,
            TinySkeleton(scale=0.05),
            motion_intent="walk",
        )

        self.assertAlmostEqual(retargeted["root"]["position"][2], -0.35, places=2)
        self.assertLess(report.compatibility_score, 0.5)
        self.assertEqual(report.risk_level, "likely_bad")
        self.assertIn("extreme_height_ratio", report.warnings)

    def test_motion_retargeter_sanitizes_bad_source_channels(self):
        retargeter = MotionRetargeter()
        bad_pose = {
            "root": {"position": [math.nan, "nope", None]},
            "hips": {"rotation": [math.inf, 9, "bad"]},
            42: {"rotation": [1, 2, 3]},
            "bad_channels": "not a channel dict",
        }

        retargeted, report = retargeter.retarget_pose_with_report(
            bad_pose,
            create_avatar_skeleton(),
            motion_intent="walk",
        )

        self.assertEqual(retargeted["root"]["position"], [0.0, 0.0, 0.0])
        self.assertEqual(retargeted["hips"]["rotation"], [0.0, 0.75, 0.0])
        self.assertIn("root.position", report.invalid_channels)
        self.assertIn("hips.rotation", report.invalid_channels)
        self.assertIn("invalid_source_channels_sanitized", report.warnings)
        json.dumps(report.to_dict())

    def test_motion_retargeter_respects_locked_target_axes(self):
        skeleton = create_avatar_skeleton()
        skeleton.joints["hips"].locked_axes = ["x"]
        retargeter = MotionRetargeter()

        retargeted, report = retargeter.retarget_pose_with_report(
            {"hips": {"rotation": [1.0, 0.5, 0.5]}},
            skeleton,
            motion_intent="dance",
        )

        self.assertEqual(retargeted["hips"]["rotation"][0], 0.0)
        self.assertIn("hips.rotation", report.clamped_channels)

    def test_motion_retargeter_reports_strained_when_no_target_skeleton_exists(self):
        retargeter = MotionRetargeter()

        retargeted, report = retargeter.retarget_pose_with_report(
            {"tail": {"rotation": [0.0, 0.0, 1.0]}},
            None,
            motion_intent="tail wag",
        )

        self.assertIn("tail", retargeted)
        self.assertEqual(report.risk_level, "strained")
        self.assertEqual(report.compatibility_score, 0.5)
        self.assertIn("no_target_skeleton", report.warnings)

    def test_motion_feedback_coordinator_emits_avatar_and_jeremy_hints(self):
        bus = EventBus()
        received = []
        avatar_sink_payloads = []
        jeremy_sink_payloads = []
        bus.subscribe("*", received.append)
        coordinator = MotionFeedbackCoordinator(
            bus,
            jeremy_sink=jeremy_sink_payloads.append,
            avatar_ai_sink=avatar_sink_payloads.append,
            min_risk_level="usable",
        )
        coordinator.start()

        bus.emit(
            "performer:animated",
            {
                "performer_id": "sheila",
                "animation": "bird walk",
                "retarget_report": {
                    "risk_level": "likely_bad",
                    "compatibility_score": 0.31,
                    "warnings": ["extreme_height_ratio", "many_channels_clamped"],
                    "dropped_joints": ["tail", "wing_tip_l", "wing_tip_r"],
                    "clamped_channels": ["calf_l.rotation", "calf_r.rotation", "foot_l.rotation"],
                    "invalid_channels": [],
                },
            },
            source="test",
        )

        event_types = [event["event_type"] for event in received]
        self.assertIn("motion:feedback", event_types)
        self.assertIn("avatar:compensation_hint", event_types)
        self.assertIn("jeremy:stage_direction", event_types)
        self.assertEqual(avatar_sink_payloads[-1]["risk_level"], "likely_bad")
        self.assertEqual(jeremy_sink_payloads[-1]["performer_id"], "sheila")
        self.assertIn("fallback_to_simpler_animation", avatar_sink_payloads[-1]["compensation_hints"])

    def test_motion_feedback_coordinator_ignores_clean_animation_reports(self):
        bus = EventBus()
        received = []
        bus.subscribe("*", received.append)
        coordinator = MotionFeedbackCoordinator(bus)
        coordinator.start()

        bus.emit(
            "performer:animated",
            {
                "performer_id": "p1",
                "animation": "walk",
                "retarget_report": {"risk_level": "good", "compatibility_score": 0.95, "warnings": []},
            },
            source="test",
        )

        self.assertNotIn("motion:feedback", [event["event_type"] for event in received])

    def test_motion_feedback_sink_failure_is_reported_without_breaking_bus(self):
        bus = EventBus()
        received = []
        bus.subscribe("*", received.append)

        def bad_sink(payload):
            raise RuntimeError("sink failed")

        coordinator = MotionFeedbackCoordinator(bus, avatar_ai_sink=bad_sink)
        coordinator.start()

        bus.emit(
            "performer:animated",
            {
                "performer_id": "p1",
                "animation": "walk",
                "retarget_report": {
                    "risk_level": "strained",
                    "compatibility_score": 0.5,
                    "warnings": ["many_source_joints_dropped"],
                    "dropped_joints": ["tail", "ear_l", "ear_r"],
                    "clamped_channels": [],
                    "invalid_channels": [],
                },
            },
            source="test",
        )

        event_types = [event["event_type"] for event in received]
        self.assertIn("motion:feedback", event_types)
        self.assertIn("motion:feedback_sink_error", event_types)

    def test_visual_patch_coordinator_emits_patch_and_bake_request_for_mesh_glitch(self):
        bus = EventBus()
        received = []
        bus.subscribe("*", received.append)
        coordinator = VisualPatchCoordinator(bus)
        coordinator.start()

        bus.emit(
            "avatar:mesh_glitch",
            {
                "performer_id": "sheila",
                "issue": "left shoulder popping out of jacket",
                "anchor_joint": "upperarm_l",
            },
            source="test",
        )

        patch_events = [event for event in received if event["event_type"] == "avatar:visual_patch"]
        bake_events = [event for event in received if event["event_type"] == "avatar:visual_patch_bake_requested"]
        patch = patch_events[-1]["data"]
        bake = bake_events[-1]["data"]
        self.assertEqual(patch["performer_id"], "sheila")
        self.assertEqual(patch["anchor_joint"], "upperarm_l")
        self.assertEqual(patch["reason"], "shoulder_clip")
        self.assertEqual(patch["resolution"], "fine")
        self.assertEqual(patch["stage"], "live_voxel_overlay")
        self.assertEqual(patch["live_update_mode"], "add_subtract_voxels")
        self.assertTrue(patch["bake_to_mesh"])
        self.assertTrue(str(patch["output_mesh_name"]).endswith("_mesh"))
        self.assertEqual(bake["next_stage"], "bake_pending")
        self.assertEqual(bake["swap_strategy"], "keep_live_voxels_until_mesh_ready")

    def test_visual_patch_coordinator_parses_jeremy_patch_request(self):
        bus = EventBus()
        received = []
        bus.subscribe("avatar:visual_patch", received.append)
        coordinator = VisualPatchCoordinator(bus)
        coordinator.start()

        bus.emit(
            "jeremy:visual_patch_request",
            parse_visual_patch_request("Lengthen the left pants cuff and blend it into the shoe", performer_id="pete"),
            source="test",
        )

        patch = received[-1]["data"]
        self.assertEqual(patch["performer_id"], "pete")
        self.assertEqual(patch["kind"], "extend")
        self.assertEqual(patch["reason"], "pants_extension")
        self.assertEqual(patch["anchor_joint"], "calf_l")
        self.assertEqual(patch["resolution"], "medium")

    def test_visual_patch_coordinator_can_cover_motion_feedback_stress(self):
        bus = EventBus()
        received = []
        bus.subscribe("avatar:visual_patch", received.append)
        coordinator = VisualPatchCoordinator(bus)
        coordinator.start()

        bus.emit(
            "motion:feedback",
            {
                "performer_id": "p1",
                "animation": "waltz",
                "risk_level": "likely_bad",
                "compensation_hints": ["avoid_forcing_joint_limits", "fallback_to_simpler_animation"],
            },
            source="test",
        )

        patch = received[-1]["data"]
        self.assertEqual(patch["reason"], "motion_stress_cover")
        self.assertEqual(patch["kind"], "soften")
        self.assertEqual(patch["ttl_seconds"], 4.0)

    def test_visual_patch_coordinator_retires_live_voxels_when_mesh_ready(self):
        bus = EventBus()
        received = []
        bus.subscribe("avatar:visual_patch_retired", received.append)
        coordinator = VisualPatchCoordinator(bus)
        coordinator.start()

        bus.emit(
            "avatar:visual_patch_mesh_ready",
            {
                "performer_id": "p1",
                "patch_id": "patch_123",
                "mesh_id": "patch_123_mesh",
            },
            source="test",
        )

        retired = received[-1]["data"]
        self.assertEqual(retired["patch_id"], "patch_123")
        self.assertEqual(retired["mesh_id"], "patch_123_mesh")
        self.assertEqual(retired["retire_reason"], "baked_mesh_ready")

    def test_visual_patch_up_kit_routes_repair_to_physical_patch_system(self):
        bus = EventBus()
        received = []
        bus.subscribe("*", received.append)
        kit = VisualPatchUpKit(bus)
        kit.start()

        bus.emit(
            "visual:style_request",
            {
                "performer_id": "sheila",
                "description": "cover up the shoulder popping through the jacket",
                "palette": "match_outfit",
            },
            source="test",
        )

        event_types = [event["event_type"] for event in received]
        self.assertIn("visual:style_plan", event_types)
        self.assertIn("avatar:visual_patch", event_types)
        style_event = [event for event in received if event["event_type"] == "visual:style_plan"][-1]
        self.assertEqual(style_event["source"], "visual_patch_up_kit")
        patch = [event for event in received if event["event_type"] == "avatar:visual_patch"][-1]["data"]
        self.assertEqual(patch["reason"], "shoulder_clip")

    def test_visual_patch_up_kit_routes_costume_makeup_and_set_requests(self):
        bus = EventBus()
        received = []
        bus.subscribe("*", received.append)
        kit = VisualPatchUpKit(bus)
        kit.start()

        kit.plan(parse_styling_request("make the lipstick darker but still soft", performer_id="sheila"))
        kit.plan(parse_styling_request("cool the detective office wall lighting", room_id="office"))
        kit.plan(parse_styling_request("adjust the jacket costume palette", performer_id="pete", palette="navy"))

        event_types = [event["event_type"] for event in received]
        self.assertIn("avatar:digital_makeup", event_types)
        self.assertIn("set:style_adjustment", event_types)
        self.assertIn("costume:style_adjustment", event_types)

    def test_visual_styling_aide_name_remains_compatibility_alias(self):
        self.assertIs(VisualStylingAide, VisualPatchUpKit)

    def test_visual_patch_workflow_inspects_measures_and_falls_back_to_live_patch(self):
        bus = EventBus()
        received = []
        bus.subscribe("*", received.append)
        workflow = VisualPatchWorkflow(bus)
        workflow.start()

        bus.emit(
            "visual:patch_workflow_request",
            {
                "requester": {"requester_id": "director", "role": "director"},
                "selection": {
                    "selection_id": "sel_1",
                    "view_id": "stage",
                    "selection_mode": "3d",
                    "bounds": {"x": 10, "y": 20, "width": 80, "height": 40},
                    "target_ids": ["sheila", "jacket_1"],
                    "screenshot_ref": "frame://1",
                    "depth_ref": "depth://1",
                    "world_hint": {"size": [0.2, 0.3, 0.05]},
                },
                "performer_id": "sheila",
                "description": "left shoulder is popping through the jacket",
            },
            source="test",
        )

        event_types = [event["event_type"] for event in received]
        self.assertIn("visual:patch_workflow_report", event_types)
        self.assertIn("visual:style_request", event_types)
        report = [event for event in received if event["event_type"] == "visual:patch_workflow_report"][-1]["data"]
        self.assertTrue(report["patch_needed"])
        self.assertIn("mesh_or_costume_clipping", report["visual_findings"])
        self.assertEqual(report["measurements"]["screen_area"], 3200.0)
        self.assertEqual(report["measurements"]["selection_mode"], "3d")
        self.assertEqual(report["measurements"]["measurement_status"], "estimated_from_3d_selection")
        self.assertEqual(report["measurements"]["world_volume"], 0.003)

    def test_visual_patch_workflow_denies_unpermitted_viewer(self):
        bus = EventBus()
        received = []
        bus.subscribe("visual:patch_workflow_denied", received.append)
        workflow = VisualPatchWorkflow(bus)

        report = workflow.handle_request(
            PatchWorkflowRequest(
                request_id="req_denied",
                requester=PatchRequester("viewer_1", "viewer"),
                selection=VisualSelection("sel", "stage", "2d", {"width": 10, "height": 10}),
                description="fix this",
            )
        )

        self.assertFalse(report.patch_needed)
        self.assertEqual(report.repair_attempt["status"], "denied")
        self.assertEqual(received[-1]["event_type"], "visual:patch_workflow_denied")

    def test_visual_patch_workflow_allows_avatar_with_permission(self):
        workflow = VisualPatchWorkflow(EventBus())

        report = workflow.handle_request(
            PatchWorkflowRequest(
                request_id="req_avatar",
                requester=PatchRequester("avatar_sheila", "avatar", permissions=["visual_patch"], avatar_id="sheila"),
                selection=VisualSelection("sel", "stage", "2d", {"width": 20, "height": 20}),
                description="cover up the jacket gap",
                performer_id="sheila",
            ),
            emit=False,
        )

        self.assertTrue(report.patch_needed)
        self.assertIn("mesh_or_costume_clipping", report.visual_findings)

    def test_visual_patch_workflow_repairs_surface_blend_without_voxels(self):
        workflow = VisualPatchWorkflow(EventBus())

        report = workflow.handle_request(
            PatchWorkflowRequest(
                request_id="req_blend",
                requester=PatchRequester("director", "director"),
                selection=VisualSelection("sel", "stage", "2d", {"width": 30, "height": 20}),
                description="blend the color shade on the cheek makeup",
                performer_id="sheila",
            ),
            emit=False,
        )

        self.assertFalse(report.patch_needed)
        self.assertEqual(report.repair_attempt["status"], "repaired")
        self.assertEqual(report.repair_attempt["method"], "material_or_texture_blend_adjustment")

    def test_visual_patch_workflow_tracks_2d_selection_without_depth(self):
        workflow = VisualPatchWorkflow(EventBus())

        report = workflow.handle_request(
            PatchWorkflowRequest(
                request_id="req_2d",
                requester=PatchRequester("director", "director"),
                selection=VisualSelection("sel", "stage", "2d", {"width": 32, "height": 16}),
                description="phone looks too small in the hand",
                performer_id="sheila",
            ),
            emit=False,
        )

        self.assertEqual(report.measurements["selection_mode"], "2d")
        self.assertEqual(report.measurements["measurement_status"], "estimated_from_2d_selection")
        self.assertNotIn("world_volume", report.measurements)

    def test_visual_patch_workflow_loads_uploaded_avatar_and_object_obj_files(self):
        workflow = VisualPatchWorkflow(EventBus())
        with tempfile.TemporaryDirectory() as temp_dir:
            avatar_path = Path(temp_dir) / "avatar.obj"
            chair_path = Path(temp_dir) / "chair.obj"
            avatar_path.write_text("v 0 0 0\nv 0 1.8 0\nv 0.4 1.8 0.2\nf 1 2 3\n", encoding="utf-8")
            chair_path.write_text("v 0 0 0\nv 0 0.55 0\nv 0.65 0.55 0.75\nf 1 2 3\n", encoding="utf-8")

            report = workflow.handle_request(
                PatchWorkflowRequest(
                    request_id="req_pair",
                    requester=PatchRequester("director", "director"),
                    selection=VisualSelection(
                        selection_id="sel_pair",
                        view_id="stage",
                        selection_mode="3d",
                        bounds={"width": 120, "height": 80},
                        avatar_file_ref=str(avatar_path),
                        object_file_ref=str(chair_path),
                        contact_constraints={
                            "contact": "sit",
                            "avatar_anchor": "pelvis",
                            "object_anchor": "seat",
                        },
                    ),
                    description="make the uploaded avatar sit naturally on the uploaded chair",
                    performer_id="sheila",
                ),
                emit=False,
            )

        assets = report.measurements["asset_files"]
        self.assertEqual(assets["avatar"]["load_status"], "geometry_loaded")
        self.assertEqual(assets["object"]["geometry_status"], "obj_bounds_loaded")
        self.assertEqual(assets["object"]["size"], [0.65, 0.55, 0.75])
        self.assertEqual(report.measurements["fit_problem"]["status"], "ready_for_avatar_object_fit")
        self.assertEqual(report.measurements["fit_problem"]["alignment"]["avatar_anchor"], "pelvis")

    def test_visual_patch_workflow_tolerates_invalid_3d_world_hint(self):
        workflow = VisualPatchWorkflow(EventBus())

        report = workflow.handle_request(
            PatchWorkflowRequest(
                request_id="req_bad_world",
                requester=PatchRequester("director", "director"),
                selection=VisualSelection(
                    selection_id="sel_bad_world",
                    view_id="stage",
                    selection_mode="3d",
                    bounds={"width": 50, "height": 40},
                    world_hint={"size": ["wide", 1.0, float("nan")]},
                ),
                description="table alignment looks wrong",
            ),
            emit=False,
        )

        self.assertIsNone(report.measurements["world_size"])
        self.assertEqual(report.measurements["world_measurement_warning"], "invalid_world_size_hint")

    def test_visual_patch_workflow_skips_invalid_obj_vertices(self):
        workflow = VisualPatchWorkflow(EventBus())
        with tempfile.TemporaryDirectory() as temp_dir:
            obj_path = Path(temp_dir) / "messy_chair.obj"
            obj_path.write_text(
                "v nope 0 0\nv nan 1 0\nv 0 0 0\nv 1 1 1\nf 1 2 3\n",
                encoding="utf-8",
            )

            report = workflow.handle_request(
                PatchWorkflowRequest(
                    request_id="req_messy_obj",
                    requester=PatchRequester("director", "director"),
                    selection=VisualSelection(
                        selection_id="sel_messy_obj",
                        view_id="stage",
                        selection_mode="3d",
                        bounds={"width": 50, "height": 40},
                        object_file_ref=str(obj_path),
                    ),
                    description="chair object fit looks wrong",
                ),
                emit=False,
            )

        obj = report.measurements["asset_files"]["object"]
        self.assertEqual(obj["load_status"], "geometry_loaded")
        self.assertEqual(obj["vertex_count"], 2)
        self.assertEqual(obj["skipped_vertex_count"], 2)
        self.assertEqual(obj["size"], [1.0, 1.0, 1.0])

    def test_visual_patch_workflow_keeps_live_path_for_meticulous_effort(self):
        bus = EventBus()
        received = []
        bus.subscribe("*", received.append)
        workflow = VisualPatchWorkflow(bus)

        report = workflow.handle_request(
            PatchWorkflowRequest(
                request_id="req_meticulous",
                requester=PatchRequester("director", "director"),
                selection=VisualSelection(
                    selection_id="sel_meticulous",
                    view_id="stage",
                    selection_mode="3d",
                    bounds={"width": 80, "height": 60},
                    depth_ref="depth://meticulous",
                    world_hint={"size": [0.4, 0.6, 0.2]},
                ),
                description="the sleeve clips through the jacket but be careful before baking",
                performer_id="sheila",
                repair_effort={"effort": "meticulous"},
            ),
        )

        style_request = [event for event in received if event["event_type"] == "visual:style_request"][-1]["data"]
        self.assertTrue(report.patch_needed)
        self.assertEqual(report.repair_effort["effort"], "meticulous")
        self.assertTrue(report.repair_effort["same_runtime_path"])
        self.assertEqual(report.repair_attempt["method"], "live_voxel_overlay_then_mesh_bake")
        self.assertGreater(report.repair_attempt["quality_passes"], 1)
        self.assertTrue(report.repair_attempt["requires_remeasure_before_bake"])
        self.assertEqual(style_request["urgency"], "live")
        self.assertEqual(style_request["repair_effort"]["effort"], "meticulous")

    def test_waltz_duet_exports_male_and_sheila_parts(self):
        duet = build_waltz_duet_sequence("male_part_1", "sheila")
        payload = export_sequences([build_basic_recovery_sequence("male_part_1"), duet])

        self.assertEqual(duet.duration, 12.0)
        self.assertEqual({clip.performer_id for clip in duet.clips}, {"male_part_1", "sheila"})
        self.assertIn("waltz_box_step_female", [clip.animation for clip in duet.clips])
        json.dumps(payload)

    def test_mocap_boundary_maps_into_pubcast_skeleton(self):
        skeleton = create_avatar_skeleton()
        report = diagnose_mocap_skeleton_boundary(skeleton, ["left_hand", "right_foot", "mystery_joint"])

        self.assertIn("left_hand", report["mapped"])
        self.assertIn("right_foot", report["mapped"])
        self.assertEqual(report["missing_skeleton_targets"], [])
        self.assertEqual(report["unmapped_incoming"], ["mystery_joint"])

    def test_contact_resolver_maps_couch_to_sit_and_emits_event(self):
        bus = EventBus()
        events = []
        bus.subscribe("performer:contact", events.append)
        registry = PerformerRegistry(bus)
        performer = registry.spawn_performer("p1", "Pete", [0, 0, 0], "green_room")
        resolver = ContactResolver(bus)

        result = resolver.resolve(
            performer,
            ContactObject("couch", "couch", [0.4, 0, 0], radius=0.8, animation_hint="sit down and watch tv on couch"),
        )

        self.assertEqual(result.animation, "sit")
        self.assertTrue(result.reachable)
        self.assertIn("hips", result.anchors)
        self.assertIn("foot_l", result.anchors)
        self.assertEqual(events[-1]["event_type"], "performer:contact")

    def test_contact_resolver_maps_typewriter_to_hand_contact(self):
        bus = EventBus()
        registry = PerformerRegistry(bus)
        performer = registry.spawn_performer("p1", "Pete", [0, 0, 0], "dressing_room")
        resolver = ContactResolver()

        result = resolver.resolve(
            performer,
            ContactObject("typewriter", "typewriter", [0.2, 0, 0.2], radius=0.5, animation_hint="typewriter"),
        )

        self.assertEqual(result.animation, "type")
        self.assertIn("hand_l", result.anchors)
        self.assertIn("hand_r", result.anchors)
        self.assertEqual(result.warnings, [])

    def test_interaction_fit_scales_too_small_phone_proxy(self):
        bus = EventBus()
        events = []
        bus.subscribe("*", events.append)
        registry = PerformerRegistry(bus)
        performer = registry.spawn_performer("p1", "Pete", [0, 0, 0], "car")
        coordinator = InteractionFitCoordinator(bus)

        report = coordinator.evaluate(
            performer,
            ContactObject("phone_1", "phone", [0.2, 0, 0.1], radius=0.02, metadata={"size": 0.025}),
        )

        self.assertIn("object_too_small_for_hand", report.warnings)
        self.assertIn("scale_prop_proxy_up", report.compensation_hints)
        self.assertGreater(report.object_adjustments["scale_proxy"], 1.0)
        self.assertIn("avatar:interaction_compensation", [event["event_type"] for event in events])

    def test_interaction_fit_adds_grip_clearance_for_nonstandard_cup_hand(self):
        bus = EventBus()
        registry = PerformerRegistry(bus)
        performer = registry.spawn_performer("claw_guest", "Claw Guest", [0, 0, 0], "studio")
        performer.avatar_skeleton = SimpleNamespace(joints={"claw_hand_r": SimpleNamespace()}, bind_pose={})
        coordinator = InteractionFitCoordinator(bus)

        report = coordinator.evaluate(
            performer,
            ContactObject(
                "cup_1",
                "cup",
                [0.1, 0, 0.1],
                radius=0.04,
                metadata={
                    "size": 0.045,
                    "avatar_profile": {
                        "traits": ["claw"],
                        "silhouette": {"hand_width": 0.18, "grip_diameter": 0.14},
                    },
                },
            ),
            emit=False,
        )

        zones = report.object_adjustments["clearance_zones"]
        self.assertIn("preserve_anatomy_clearance", report.compensation_hints)
        self.assertEqual(zones[0]["kind"], "grip_clearance")
        self.assertEqual(zones[0]["shape"], "adaptive_grip_sleeve")
        self.assertGreaterEqual(zones[0]["dimensions_meters"]["width"], 0.18)

    def test_interaction_fit_adjusts_chair_for_sitting_proportions(self):
        bus = EventBus()
        registry = PerformerRegistry(bus)
        performer = registry.spawn_performer("p1", "Pete", [0, 0, 0], "studio")
        coordinator = InteractionFitCoordinator(bus)

        report = coordinator.evaluate(
            performer,
            ContactObject("chair_1", "chair", [0.1, 0, 0.1], radius=0.6, metadata={"seat_height": 0.9}),
            emit=False,
        )

        self.assertEqual(report.interaction, "sit")
        self.assertIn("seat_height_mismatch", report.warnings)
        self.assertIn("adjust_hips_to_seat", report.compensation_hints)
        self.assertIn("hips", report.anchor_offsets)

    def test_interaction_fit_builds_adaptive_chair_extension_for_large_avatar(self):
        bus = EventBus()
        events = []
        bus.subscribe("*", events.append)
        registry = PerformerRegistry(bus)
        performer = registry.spawn_performer("large_1", "Large Guest", [0, 0, 0], "studio")
        performer.avatar_skeleton = SimpleNamespace(
            joints={},
            bind_pose={
                "root": {"position": [0.0, 0.0, 0.0]},
                "head": {"position": [0.0, 2.295, 0.0]},
                "hips": {"position": [0.0, 1.05, 0.0]},
                "thigh_l": {"position": [-0.24, 0.78, 0.0]},
                "calf_l": {"position": [-0.24, 0.36, 0.0]},
                "foot_l": {"position": [-0.24, 0.0, 0.0]},
                "thigh_r": {"position": [0.24, 0.78, 0.0]},
                "calf_r": {"position": [0.24, 0.36, 0.0]},
                "foot_r": {"position": [0.24, 0.0, 0.0]},
            },
        )
        coordinator = InteractionFitCoordinator(bus)

        report = coordinator.evaluate(
            performer,
            ContactObject(
                "chair_standard",
                "chair",
                [0.1, 0, 0.1],
                radius=0.6,
                metadata={"seat_height": 0.43, "seat_width": 0.48, "seat_depth": 0.48},
            ),
        )

        proposal_events = [event for event in events if event["event_type"] == "object:adaptive_prop_extension_proposed"]
        self.assertIn("build_adaptive_prop_extension", report.compensation_hints)
        self.assertEqual(len(report.adaptive_extensions), 1)
        self.assertEqual(len(proposal_events), 1)
        self.assertEqual(coordinator.active_extensions(), [])
        self.assertEqual(len(coordinator.pending_extensions()), 1)
        extension = proposal_events[-1]["data"]
        self.assertEqual(extension["object_id"], "chair_standard")
        self.assertEqual(extension["approval"]["state"], "pending")
        self.assertTrue(extension["preserve_original_asset"])
        self.assertEqual(extension["voxel_patch"]["kind"], "extend")
        self.assertGreater(extension["dimensions_meters"]["target_seat_height"], 0.43)

        approved = coordinator.approve_extension(extension["extension_id"], now=100.0)
        extension_events = [event for event in events if event["event_type"] == "object:adaptive_prop_extension"]
        self.assertIsNotNone(approved)
        self.assertEqual(coordinator.pending_extensions(), [])
        self.assertEqual(len(coordinator.active_extensions()), 1)
        self.assertEqual(len(extension_events), 1)
        self.assertEqual(extension_events[-1]["data"]["approval"]["state"], "approved")

    def test_interaction_fit_meticulous_effort_stays_live_but_adds_quality_passes(self):
        bus = EventBus()
        events = []
        bus.subscribe("*", events.append)
        registry = PerformerRegistry(bus)
        performer = registry.spawn_performer("large_meticulous_1", "Large Guest", [0, 0, 0], "studio")
        performer.avatar_skeleton = SimpleNamespace(
            joints={},
            bind_pose={
                "root": {"position": [0.0, 0.0, 0.0]},
                "head": {"position": [0.0, 2.295, 0.0]},
                "hips": {"position": [0.0, 1.05, 0.0]},
                "thigh_l": {"position": [-0.24, 0.78, 0.0]},
                "calf_l": {"position": [-0.24, 0.36, 0.0]},
                "foot_l": {"position": [-0.24, 0.0, 0.0]},
                "thigh_r": {"position": [0.24, 0.78, 0.0]},
                "calf_r": {"position": [0.24, 0.36, 0.0]},
                "foot_r": {"position": [0.24, 0.0, 0.0]},
            },
        )
        coordinator = InteractionFitCoordinator(bus)

        report = coordinator.evaluate(
            performer,
            ContactObject(
                "chair_meticulous",
                "chair",
                [0.1, 0, 0.1],
                radius=0.6,
                metadata={
                    "seat_height": 0.43,
                    "seat_width": 0.48,
                    "seat_depth": 0.48,
                    "adaptive_prop_effort": "meticulous",
                },
            ),
        )

        proposal = [event for event in events if event["event_type"] == "object:adaptive_prop_extension_proposed"][-1]["data"]
        self.assertEqual(report.repair_effort["effort"], "meticulous")
        self.assertEqual(proposal["render_mode"], "live_voxel_overlay_then_mesh_bake")
        self.assertEqual(proposal["repair_effort"]["first_response"], "live_safe_overlay_then_meticulous_refine")
        self.assertEqual(proposal["voxel_patch"]["resolution"], "ultra")
        self.assertGreater(proposal["voxel_patch"]["bake_after_seconds"], 0.75)
        self.assertTrue(proposal["voxel_patch"]["requires_remeasure_before_bake"])
        self.assertGreaterEqual(proposal["quality_assurance"]["passes"], 4)
        self.assertIn("visual_blend", proposal["quality_assurance"]["checks"])

    def test_interaction_fit_retires_adaptive_extension_when_interaction_ends(self):
        bus = EventBus()
        events = []
        bus.subscribe("*", events.append)
        registry = PerformerRegistry(bus)
        performer = registry.spawn_performer("large_retire_1", "Large Guest", [0, 0, 0], "studio")
        performer.avatar_skeleton = SimpleNamespace(
            joints={},
            bind_pose={
                "root": {"position": [0.0, 0.0, 0.0]},
                "head": {"position": [0.0, 2.295, 0.0]},
                "hips": {"position": [0.0, 1.05, 0.0]},
                "thigh_l": {"position": [-0.24, 0.78, 0.0]},
                "calf_l": {"position": [-0.24, 0.36, 0.0]},
                "foot_l": {"position": [-0.24, 0.0, 0.0]},
                "thigh_r": {"position": [0.24, 0.78, 0.0]},
                "calf_r": {"position": [0.24, 0.36, 0.0]},
                "foot_r": {"position": [0.24, 0.0, 0.0]},
            },
        )
        coordinator = InteractionFitCoordinator(bus)
        coordinator.evaluate(
            performer,
            ContactObject(
                "chair_retire",
                "chair",
                [0.1, 0, 0.1],
                radius=0.6,
                metadata={"seat_height": 0.43, "adaptive_prop_auto_approve": True},
            ),
        )

        self.assertEqual(len(coordinator.active_extensions()), 1)
        retired = coordinator.retire_extensions_for("large_retire_1", "chair_retire")

        retire_events = [event for event in events if event["event_type"] == "object:adaptive_prop_extension_retired"]
        self.assertEqual(len(retired), 1)
        self.assertEqual(coordinator.active_extensions(), [])
        self.assertEqual(len(retire_events), 1)
        self.assertEqual(retire_events[-1]["data"]["object_id"], "chair_retire")
        self.assertEqual(retire_events[-1]["data"]["retire_reason"], "interaction_ended")
        self.assertTrue(retire_events[-1]["data"]["preserve_original_asset"])

    def test_interaction_fit_adds_tail_clearance_to_large_avatar_chair(self):
        bus = EventBus()
        events = []
        bus.subscribe("*", events.append)
        registry = PerformerRegistry(bus)
        performer = registry.spawn_performer("minotaur_1", "Minotaur Friend", [0, 0, 0], "studio")
        performer.avatar_skeleton = SimpleNamespace(
            joints={"tail_base": SimpleNamespace(), "tail_tip": SimpleNamespace()},
            bind_pose={
                "root": {"position": [0.0, 0.0, 0.0]},
                "head": {"position": [0.0, 2.35, 0.0]},
                "hips": {"position": [0.0, 1.08, 0.0]},
                "thigh_l": {"position": [-0.26, 0.8, 0.0]},
                "calf_l": {"position": [-0.26, 0.38, 0.0]},
                "foot_l": {"position": [-0.26, 0.0, 0.0]},
                "thigh_r": {"position": [0.26, 0.8, 0.0]},
                "calf_r": {"position": [0.26, 0.38, 0.0]},
                "foot_r": {"position": [0.26, 0.0, 0.0]},
                "tail_base": {"position": [0.0, 1.0, -0.16]},
                "tail_tip": {"position": [0.0, 0.75, -0.8]},
            },
        )
        coordinator = InteractionFitCoordinator(bus)

        report = coordinator.evaluate(
            performer,
            ContactObject(
                "chair_tail",
                "chair",
                [0.1, 0, 0.1],
                radius=0.6,
                metadata={
                    "seat_height": 0.43,
                    "seat_width": 0.48,
                    "seat_depth": 0.48,
                    "tail_width": 0.22,
                    "tail_depth": 0.34,
                },
            ),
        )

        proposal = [event for event in events if event["event_type"] == "object:adaptive_prop_extension_proposed"][-1]["data"]
        self.assertIn("preserve_anatomy_clearance", report.compensation_hints)
        self.assertIn("anatomy_clearance_required", report.warnings)
        self.assertEqual(proposal["approval"]["state"], "pending")
        self.assertEqual(proposal["clearance_zones"][0]["kind"], "tail_clearance")
        self.assertEqual(proposal["clearance_zones"][0]["operation"], "subtract")
        self.assertEqual(proposal["voxel_patch"]["subtractive_cutouts"][0]["shape"], "rounded_slot")
        self.assertGreaterEqual(proposal["clearance_zones"][0]["dimensions_meters"]["width"], 0.22)

    def test_interaction_fit_uses_seated_profile_for_chair_shape_allowances(self):
        bus = EventBus()
        events = []
        bus.subscribe("*", events.append)
        registry = PerformerRegistry(bus)
        performer = registry.spawn_performer("winged_guest", "Winged Guest", [0, 0, 0], "studio")
        performer.avatar_skeleton = SimpleNamespace(
            joints={},
            bind_pose={
                "root": {"position": [0.0, 0.0, 0.0]},
                "head": {"position": [0.0, 2.05, 0.0]},
                "hips": {"position": [0.0, 0.96, 0.0]},
                "thigh_l": {"position": [-0.22, 0.72, 0.0]},
                "calf_l": {"position": [-0.22, 0.34, 0.0]},
                "foot_l": {"position": [-0.22, 0.0, 0.0]},
                "thigh_r": {"position": [0.22, 0.72, 0.0]},
                "calf_r": {"position": [0.22, 0.34, 0.0]},
                "foot_r": {"position": [0.22, 0.0, 0.0]},
            },
        )
        coordinator = InteractionFitCoordinator(bus)

        coordinator.evaluate(
            performer,
            ContactObject(
                "chair_profile",
                "chair",
                [0.1, 0, 0.1],
                radius=0.6,
                metadata={
                    "seat_height": 0.43,
                    "seat_width": 0.48,
                    "seat_depth": 0.48,
                    "avatar_profile": {
                        "traits": ["wing"],
                        "silhouette": {"wing_span": 0.8},
                        "pose_profiles": {
                            "seated": {
                                "silhouette": {
                                    "wing_span": 1.75,
                                    "shoulder_width": 0.82,
                                },
                                "traits": ["broad_shoulder"],
                            }
                        },
                    },
                },
            ),
        )

        proposal = [event for event in events if event["event_type"] == "object:adaptive_prop_extension_proposed"][-1]["data"]
        kinds = {zone["kind"] for zone in proposal["clearance_zones"]}
        self.assertIn("wing_clearance_l", kinds)
        self.assertIn("wing_clearance_r", kinds)
        self.assertIn("shoulder_clearance", kinds)
        self.assertTrue(
            any(zone["reason"] == "preserve_upper_body_silhouette_clearance" for zone in proposal["clearance_zones"])
        )

    def test_interaction_fit_regresses_absent_extension_and_expires_cache(self):
        bus = EventBus()
        events = []
        bus.subscribe("*", events.append)
        registry = PerformerRegistry(bus)
        performer = registry.spawn_performer("large_absent_1", "Large Guest", [0, 0, 0], "studio")
        performer.avatar_skeleton = SimpleNamespace(
            joints={},
            bind_pose={
                "root": {"position": [0.0, 0.0, 0.0]},
                "head": {"position": [0.0, 2.295, 0.0]},
                "hips": {"position": [0.0, 1.05, 0.0]},
                "thigh_l": {"position": [-0.24, 0.78, 0.0]},
                "calf_l": {"position": [-0.24, 0.36, 0.0]},
                "foot_l": {"position": [-0.24, 0.0, 0.0]},
                "thigh_r": {"position": [0.24, 0.78, 0.0]},
                "calf_r": {"position": [0.24, 0.36, 0.0]},
                "foot_r": {"position": [0.24, 0.0, 0.0]},
            },
        )
        coordinator = InteractionFitCoordinator(bus)
        coordinator.evaluate(
            performer,
            ContactObject(
                "chair_absent",
                "chair",
                [0.1, 0, 0.1],
                radius=0.6,
                metadata={"seat_height": 0.43, "adaptive_prop_auto_approve": True},
            ),
        )

        self.assertEqual(coordinator.mark_performer_absent("large_absent_1", now=1000.0), 1)
        early = coordinator.sweep_presence(now=1119.0, regress_after_seconds=120.0, cache_ttl_seconds=300.0)
        self.assertEqual(early["retired"], [])
        self.assertEqual(len(coordinator.active_extensions()), 1)

        swept = coordinator.sweep_presence(now=1120.0, regress_after_seconds=120.0, cache_ttl_seconds=300.0)
        self.assertEqual(len(swept["retired"]), 1)
        self.assertEqual(coordinator.active_extensions(), [])
        self.assertEqual(len(coordinator.cached_extensions()), 1)
        self.assertIn("object:adaptive_prop_extension_cached", [event["event_type"] for event in events])

        expired = coordinator.sweep_presence(now=1421.0, regress_after_seconds=120.0, cache_ttl_seconds=300.0)
        self.assertEqual(len(expired["expired"]), 1)
        self.assertEqual(coordinator.cached_extensions(), [])
        self.assertIn("object:adaptive_prop_extension_cache_expired", [event["event_type"] for event in events])

    def test_interaction_fit_can_save_extension_to_inventory_and_keep_room(self):
        bus = EventBus()
        events = []
        bus.subscribe("*", events.append)
        registry = PerformerRegistry(bus)
        performer = registry.spawn_performer("large_keep_1", "Large Guest", [0, 0, 0], "studio")
        performer.avatar_skeleton = SimpleNamespace(
            joints={},
            bind_pose={
                "root": {"position": [0.0, 0.0, 0.0]},
                "head": {"position": [0.0, 2.295, 0.0]},
                "hips": {"position": [0.0, 1.05, 0.0]},
                "thigh_l": {"position": [-0.24, 0.78, 0.0]},
                "calf_l": {"position": [-0.24, 0.36, 0.0]},
                "foot_l": {"position": [-0.24, 0.0, 0.0]},
                "thigh_r": {"position": [0.24, 0.78, 0.0]},
                "calf_r": {"position": [0.24, 0.36, 0.0]},
                "foot_r": {"position": [0.24, 0.0, 0.0]},
            },
        )
        coordinator = InteractionFitCoordinator(bus)
        coordinator.evaluate(
            performer,
            ContactObject(
                "chair_keep",
                "chair",
                [0.1, 0, 0.1],
                radius=0.6,
                metadata={"seat_height": 0.43, "adaptive_prop_auto_approve": True},
            ),
        )
        extension_id = coordinator.active_extensions()[0]["extension_id"]

        saved = coordinator.save_extension(
            extension_id,
            inventory_id="inventory_big_guest_chair",
            keep_in_room=True,
            now=2000.0,
        )
        coordinator.mark_performer_absent("large_keep_1", now=2000.0)
        swept = coordinator.sweep_presence(now=2600.0, regress_after_seconds=120.0)

        event_types = [event["event_type"] for event in events]
        self.assertEqual(saved["inventory_id"], "inventory_big_guest_chair")
        self.assertIn("object:adaptive_prop_extension_saved", event_types)
        self.assertIn("object:adaptive_prop_extension_kept_in_room", event_types)
        self.assertEqual(swept["retired"], [])
        self.assertEqual(len(coordinator.active_extensions()), 1)
        self.assertTrue(coordinator.active_extensions()[0]["persistence"]["keep_in_room"])

    def test_interaction_fit_builds_foot_support_for_small_avatar(self):
        bus = EventBus()
        events = []
        bus.subscribe("*", events.append)
        registry = PerformerRegistry(bus)
        performer = registry.spawn_performer("small_1", "Small Guest", [0, 0, 0], "studio")
        performer.avatar_skeleton = SimpleNamespace(
            joints={},
            bind_pose={
                "root": {"position": [0.0, 0.0, 0.0]},
                "head": {"position": [0.0, 1.1, 0.0]},
                "hips": {"position": [0.0, 0.52, 0.0]},
                "thigh_l": {"position": [-0.1, 0.36, 0.0]},
                "calf_l": {"position": [-0.1, 0.18, 0.0]},
                "foot_l": {"position": [-0.1, 0.0, 0.0]},
                "thigh_r": {"position": [0.1, 0.36, 0.0]},
                "calf_r": {"position": [0.1, 0.18, 0.0]},
                "foot_r": {"position": [0.1, 0.0, 0.0]},
            },
        )
        coordinator = InteractionFitCoordinator(bus)

        report = coordinator.evaluate(
            performer,
            ContactObject("chair_standard", "chair", [0.1, 0, 0.1], radius=0.6, metadata={"seat_height": 0.45}),
        )

        proposal_events = [event for event in events if event["event_type"] == "object:adaptive_prop_extension_proposed"]
        self.assertEqual(len(proposal_events), 1)
        extension = proposal_events[-1]["data"]
        self.assertEqual(extension["kind"], "foot_support")
        self.assertEqual(extension["reason"], "avatar_smaller_than_standard_prop")
        self.assertEqual(extension["voxel_patch"]["kind"], "support")
        self.assertGreater(extension["dimensions_meters"]["foot_support_height"], 0.0)

    def test_interaction_fit_honors_adaptive_extension_disable_string(self):
        bus = EventBus()
        events = []
        bus.subscribe("*", events.append)
        registry = PerformerRegistry(bus)
        performer = registry.spawn_performer("large_2", "Large Guest", [0, 0, 0], "studio")
        performer.avatar_skeleton = SimpleNamespace(
            joints={},
            bind_pose={
                "root": {"position": [0.0, 0.0, 0.0]},
                "head": {"position": [0.0, 2.295, 0.0]},
                "hips": {"position": [0.0, 1.05, 0.0]},
                "thigh_l": {"position": [-0.24, 0.78, 0.0]},
                "calf_l": {"position": [-0.24, 0.36, 0.0]},
                "foot_l": {"position": [-0.24, 0.0, 0.0]},
                "thigh_r": {"position": [0.24, 0.78, 0.0]},
                "calf_r": {"position": [0.24, 0.36, 0.0]},
                "foot_r": {"position": [0.24, 0.0, 0.0]},
            },
        )
        coordinator = InteractionFitCoordinator(bus)

        report = coordinator.evaluate(
            performer,
            ContactObject(
                "chair_no_ext",
                "chair",
                [0.1, 0, 0.1],
                radius=0.6,
                metadata={"seat_height": 0.43, "adaptive_prop_extension": "false"},
            ),
        )

        self.assertEqual(report.adaptive_extensions, [])
        self.assertNotIn("object:adaptive_prop_extension", [event["event_type"] for event in events])

    def test_interaction_fit_caps_adaptive_voxel_budget(self):
        registry = PerformerRegistry(EventBus())
        performer = registry.spawn_performer("huge_1", "Huge Guest", [0, 0, 0], "studio")
        performer.avatar_skeleton = SimpleNamespace(
            joints={},
            bind_pose={
                "root": {"position": [0.0, 0.0, 0.0]},
                "head": {"position": [0.0, 4.2, 0.0]},
                "hips": {"position": [0.0, 2.1, 0.0]},
                "thigh_l": {"position": [-0.5, 1.45, 0.0]},
                "calf_l": {"position": [-0.5, 0.7, 0.0]},
                "foot_l": {"position": [-0.5, 0.0, 0.0]},
                "thigh_r": {"position": [0.5, 1.45, 0.0]},
                "calf_r": {"position": [0.5, 0.7, 0.0]},
                "foot_r": {"position": [0.5, 0.0, 0.0]},
            },
        )
        coordinator = InteractionFitCoordinator(EventBus())

        report = coordinator.evaluate(
            performer,
            ContactObject(
                "chair_tiny",
                "chair",
                [0.1, 0, 0.1],
                radius=0.6,
                metadata={
                    "seat_height": 0.2,
                    "seat_width": 0.2,
                    "seat_depth": 0.2,
                    "adaptive_voxel_size": 0.005,
                    "max_adaptive_voxel_axis": 16,
                    "max_adaptive_voxel_count": 2000,
                },
            ),
            emit=False,
        )

        budget = report.adaptive_extensions[0]["voxel_budget"]
        self.assertTrue(budget["dimensions_capped"])
        self.assertLessEqual(budget["estimated_voxel_count"], 2000)
        self.assertTrue(all(axis <= 16 for axis in report.adaptive_extensions[0]["voxel_patch"]["dimensions"]))

    def test_interaction_fit_adds_virtual_step_targets_for_tall_stairs(self):
        bus = EventBus()
        registry = PerformerRegistry(bus)
        performer = registry.spawn_performer("p1", "Pete", [0, 0, 0], "stairs")
        coordinator = InteractionFitCoordinator(bus)

        report = coordinator.evaluate(
            performer,
            ContactObject("stairs_1", "stairs", [0.2, 0, 0.2], radius=0.6, metadata={"step_height": 0.4}),
            emit=False,
        )

        self.assertIn("step_height_too_large", report.warnings)
        self.assertIn("insert_intermediate_foot_targets", report.compensation_hints)
        self.assertIn("virtual_step_height", report.object_adjustments)
        self.assertIn("foot_l", report.anchor_offsets)

    def test_interaction_fit_aligns_typing_surface_to_hands(self):
        bus = EventBus()
        registry = PerformerRegistry(bus)
        performer = registry.spawn_performer("p1", "Pete", [0, 0, 0], "office")
        coordinator = InteractionFitCoordinator(bus)

        report = coordinator.evaluate(
            performer,
            ContactObject("keyboard_1", "keyboard", [0.1, 0, 0.25], radius=0.5, metadata={"desk_height": 1.2}),
            emit=False,
        )

        self.assertEqual(report.interaction, "type")
        self.assertIn("typing_surface_height_mismatch", report.warnings)
        self.assertIn("adjust_elbow_height", report.compensation_hints)
        self.assertIn("hand_l", report.anchor_offsets)
        self.assertIn("hand_r", report.anchor_offsets)

    def test_interaction_fit_builds_adaptive_table_lift_for_large_avatar(self):
        bus = EventBus()
        events = []
        bus.subscribe("*", events.append)
        registry = PerformerRegistry(bus)
        performer = registry.spawn_performer("large_table_1", "Large Guest", [0, 0, 0], "dining_room")
        performer.avatar_skeleton = SimpleNamespace(
            joints={},
            bind_pose={
                "root": {"position": [0.0, 0.0, 0.0]},
                "head": {"position": [0.0, 2.295, 0.0]},
                "hips": {"position": [0.0, 1.05, 0.0]},
                "chest": {"position": [0.0, 1.74, 0.0]},
                "spine_01": {"position": [0.0, 1.22, 0.0]},
                "spine_02": {"position": [0.0, 1.39, 0.0]},
                "spine_03": {"position": [0.0, 1.56, 0.0]},
                "upperarm_l": {"position": [-0.3, 1.65, 0.0]},
                "lowerarm_l": {"position": [-0.55, 1.35, 0.0]},
                "hand_l": {"position": [-0.75, 1.1, 0.0]},
                "upperarm_r": {"position": [0.3, 1.65, 0.0]},
                "lowerarm_r": {"position": [0.55, 1.35, 0.0]},
                "hand_r": {"position": [0.75, 1.1, 0.0]},
            },
        )
        coordinator = InteractionFitCoordinator(bus)

        report = coordinator.evaluate(
            performer,
            ContactObject(
                "table_standard",
                "table",
                [0.1, 0, 0.3],
                radius=0.8,
                metadata={
                    "desk_height": 0.72,
                    "surface_width": 1.0,
                    "surface_depth": 0.75,
                    "adaptive_prop_auto_approve": True,
                },
            ),
        )

        extension_events = [event for event in events if event["event_type"] == "object:adaptive_prop_extension"]
        self.assertEqual(len(extension_events), 1)
        extension = extension_events[-1]["data"]
        self.assertEqual(extension["kind"], "surface_lift")
        self.assertEqual(extension["approval"]["state"], "auto_approved")
        self.assertEqual(extension["reason"], "avatar_larger_than_standard_surface")
        self.assertGreater(extension["dimensions_meters"]["target_surface_height"], 0.72)
        self.assertEqual(extension["voxel_patch"]["reason"], "adaptive_surface_lift")

    def test_interaction_fit_corrects_steering_wheel_grip_scale(self):
        bus = EventBus()
        registry = PerformerRegistry(bus)
        performer = registry.spawn_performer("p1", "Pete", [0, 0, 0], "car")
        coordinator = InteractionFitCoordinator(bus)

        report = coordinator.evaluate(
            performer,
            ContactObject("wheel_1", "steering_wheel", [0.1, 0, 0.35], radius=0.5, metadata={"wheel_radius": 0.04}),
            emit=False,
        )

        self.assertIn("steering_wheel_scale_mismatch", report.warnings)
        self.assertIn("scale_steering_proxy", report.compensation_hints)
        self.assertIn("wheel_radius_proxy", report.object_adjustments)
        self.assertIn("hand_l", report.anchor_offsets)

    def test_interaction_fit_flags_misaligned_impact_box(self):
        bus = EventBus()
        registry = PerformerRegistry(bus)
        performer = registry.spawn_performer("p1", "Pete", [0, 0, 0], "stage")
        coordinator = InteractionFitCoordinator(bus)

        report = coordinator.evaluate(
            performer,
            ContactObject("button_far", "button", [4.0, 0, 0], radius=0.2),
            emit=False,
        )

        self.assertIn("impact_box_or_contact_radius_misaligned", report.warnings)
        self.assertIn("expand_or_shift_contact_proxy", report.compensation_hints)
        self.assertIn("contact_radius_proxy", report.object_adjustments)

    def test_station_registry_activation_and_state_broadcast(self):
        bus = EventBus()
        events = []
        bus.subscribe("*", events.append)
        registry = StationRegistry(bus)
        station = DummyStation("camera_1", "camera", bus)

        registry.register_station(station)
        self.assertTrue(registry.activate_station("camera_1", "p1"))
        states = registry.broadcast_station_states()
        registry.deactivate_station("camera_1", "p1")

        self.assertEqual(states["camera_1"]["active_performer"], "p1")
        self.assertIn("station:activated", [event["event_type"] for event in events])
        self.assertIn("station:deactivated", [event["event_type"] for event in events])

    def test_locomotion_moves_performer_to_target(self):
        bus = EventBus()
        registry = PerformerRegistry(bus, bind_default_skeleton=False)
        registry.spawn_performer("p1", "Pete", [0, 0, 0], "studio")
        locomotion = LocomotionSystem(registry, bus, walk_speed=1.0)

        locomotion.move_to("p1", [1, 0, 0])
        for _ in range(80):
            locomotion.update(1 / 60)

        performer = registry.get_performer("p1")
        self.assertAlmostEqual(performer.position[0], 1.0, places=2)
        self.assertEqual(performer.locomotion_state, "idle")
        self.assertEqual(performer.velocity, [0.0, 0.0, 0.0])

    def test_sheila_locomotion_uses_high_heel_walk_pose(self):
        bus = EventBus()
        registry = PerformerRegistry(bus, bind_default_skeleton=False)
        registry.spawn_performer("sheila", "Sheila", [0, 0, 0], "studio", avatar_model_id="SHELA")
        locomotion = LocomotionSystem(registry, bus, walk_speed=1.0)

        pose = locomotion.get_walk_animation_frame("sheila", 0.5)

        self.assertIn("foot_l", pose)
        self.assertIn("foot_r", pose)
        self.assertIn("hips", pose)

    def test_animation_authority_uses_highest_priority_layer(self):
        authority = AnimationAuthority()
        authority.register_animation("p1", "locomotion", {"hips": {"source": "walk"}})
        authority.register_animation("p1", "mocap", {"hips": {"source": "mocap"}})

        self.assertEqual(authority.resolve("p1")["hips"]["source"], "mocap")
        authority.clear_layer("p1", "mocap")
        self.assertEqual(authority.resolve("p1")["hips"]["source"], "walk")

    def test_avatar_assets_imports_without_pydantic_and_recovers_from_bad_manifest(self):
        from modules.avatar_assets import load_manifest, refresh_manifest_cache

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            avatars_dir = data_dir / "avatars"
            avatars_dir.mkdir()
            (avatars_dir / "manifest.json").write_text("{bad json", encoding="utf-8")
            refresh_manifest_cache()

            manifest = load_manifest(data_dir)

        self.assertIsNotNone(manifest.pack_by_id("default"))
        self.assertGreater(len(manifest.packs[0].assets), 0)


if __name__ == "__main__":
    unittest.main()
