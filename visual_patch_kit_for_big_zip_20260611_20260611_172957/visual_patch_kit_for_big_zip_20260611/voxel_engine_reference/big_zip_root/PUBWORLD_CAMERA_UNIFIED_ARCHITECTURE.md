# PubWorld + Camera Engine — Unified Broadcast Studio Architecture
**Date:** 2026-05-11  
**Integration:** Complete  
**Status:** Ready to Build

---

## ARCHITECTURE

```
┌─────────────────────────────────────────────────────────┐
│           PubWorld (React/Three.js Frontend)             │
├─────────────────────────────────────────────────────────┤
│ · Voxel scene building (unlimited colors)               │
│ · Virtual camera placement & control                    │
│ · Real-time performer/actor with mocap markers         │
│ · Set baking (voxels → solid meshes)                   │
│ · Multiple modes: BUILD | CAMERA | CAPTURE | REVIEW    │
│ · Status bar + scene outliner                           │
│                                                          │
└─────────────────────────────────────────────────────────┘
              ↓ WebSocket / Rest API ↓
┌─────────────────────────────────────────────────────────┐
│       Camera Engine (Rust Backend - Thread-Safe)         │
├─────────────────────────────────────────────────────────┤
│ · FrameBuffer (ring queue, drop-oldest on full)        │
│ · EngineMode: Program | Preview | Donor | Transition   │
│ · PreheatState (60s min, FPS > 55)                     │
│ · DonationState (min 300ms hold before switch)         │
│ · ComputeBudget (CPU/GPU resource management)          │
│                                                          │
│ Guarantees:                                             │
│ · No frame drops during mode changes                    │
│ · Smooth transitions (no black frames)                  │
│ · FPS monitoring and auto-recovery                     │
│                                                          │
└─────────────────────────────────────────────────────────┘
              ↓ HTTP Stream / NDI ↓
┌─────────────────────────────────────────────────────────┐
│              Output (Live Broadcast)                     │
├─────────────────────────────────────────────────────────┤
│ · RTMP/HLS to streaming service                        │
│ · NDI for local network distribution                   │
│ · Recording to disk (session-based)                    │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

---

## INTEGRATION POINTS

### 1. Scene → Engine
**PubWorld sends to Camera Engine:**
```json
{
  "scene": {
    "voxel_sets": [...],
    "virtual_camera": { "position": [...], "rotation": [...] },
    "performer": { "position": [...], "animation": "..." },
    "lighting": { "key": {...}, "fill": {...}, "rim": {...} }
  },
  "camera_mode": "program" | "preview" | "donor" | "transition",
  "record_state": "idle" | "recording" | "paused"
}
```

### 2. Engine → PubWorld
**Camera Engine sends to PubWorld:**
```json
{
  "status": "ready" | "preheating" | "recording" | "error",
  "fps": 59.8,
  "mode": "program",
  "buffer_health": 0.85,
  "can_switch": true,
  "frame_count": 14250,
  "timestamp": 1715431200.123
}
```

### 3. Recording Pipeline
**Both systems feed into unified recording:**
- Scene metadata → session recording
- Frame data → video file
- Timestamps → sync point

---

## KEY FEATURES

### PubWorld Capabilities
- **Unlimited voxel building** (color palette + custom picker)
- **Multi-layer building** (Y from 0-25)
- **Virtual camera** with frustum visualization
- **Actor/performer** with 11 mocap markers
- **Real-time baking** (voxels → solid meshes by color)
- **Scene outliner** showing all objects
- **Multiple recording modes** within scene

### Camera Engine Capabilities
- **Frame buffering** (Ring queue, latest-frame priority)
- **Mode switching** (Program ↔ Preview ↔ Donor ↔ Transition)
- **Preheat protocol** (60s minimum, FPS > 55)
- **Smooth transitions** (300ms min hold)
- **Resource budgeting** (CPU/GPU shares)
- **FPS monitoring** (continuous health check)

---

## WORKFLOW

### 1. Build Phase (PubWorld)
1. Start in BUILD mode
2. Select voxel colors from palette
3. Click to place, right-click to remove
4. Use Y slider to build in layers
5. Real-time 3D viewport with orbit/pan/zoom

### 2. Camera Setup (PubWorld)
1. Switch to CAMERA mode
2. Position virtual camera in scene
3. See frustum lines showing viewing cone
4. Orbit/pan to frame shot

### 3. Capture Phase (Both systems)
1. Switch to CAPTURE mode in PubWorld
2. PubWorld sends scene + camera data to Engine
3. Camera Engine preheats (60s, FPS > 55)
4. Press RECORD
5. Engine buffers frames, Engine streams output
6. PubWorld shows recording indicator

### 4. Review Phase (PubWorld)
1. Switch to REVIEW mode
2. Playback captured footage
3. Option to rebake with new geometry
4. Export recording

---

## IMPLEMENTATION CHECKLIST

### Immediate (This Sprint)
- [ ] Connect PubWorld to Camera Engine via WebSocket
- [ ] Serialize scene data (voxel sets, camera, performer)
- [ ] Implement frame buffering in engine
- [ ] Add FPS monitoring to PubWorld status bar
- [ ] Test preheating logic (60s, FPS threshold)

### Next Sprint
- [ ] Implement mode switching (Program ↔ Preview)
- [ ] Add donation state with min hold time
- [ ] Smooth transition effects (fade/cross-dissolve)
- [ ] Recording pipeline integration

### Following Sprint
- [ ] Multiple camera support
- [ ] Effect chains (filters, color correction)
- [ ] Auto-recovery from FPS dips
- [ ] Export/archive workflows

---

## DATA CONTRACTS

### Scene Data Structure
```python
@dataclass
class PubWorldScene:
    voxel_sets: List[VoxelSet]  # Color + geometry
    virtual_camera: Transform   # Position + rotation
    performer: Transform        # Actor position
    lights: Dict[str, Light]   # Key, fill, rim
    recording_meta: Dict       # Session info
```

### Frame Data
```python
@dataclass
class FrameData:
    buffer: bytes              # Raw frame (YUV420)
    timestamp: float           # Unix timestamp
    mode: str                  # program | preview
    fps: float                 # Current FPS
    metadata: Dict            # Scene ref
```

---

## PERFORMANCE TARGETS

- **Frame rate:** 60 FPS stable
- **Latency:** <50ms camera→output
- **Buffer:** 3-5 frame depth (120-200ms)
- **Transitions:** Seamless (no black frames)
- **CPU/GPU:** Balanced across workloads

---

## RISKS & MITIGATIONS

| Risk | Mitigation |
|------|------------|
| Frame buffer overflow | Drop oldest on full (latest-frame priority) |
| Preheating timeout | Min 60s + FPS > 55, auto-skip if impossible |
| Mode switch glitches | 300ms min hold + buffer continuity check |
| Performance drops | IRM system auto-reduces quality |
| Sync issues | Timestamp-based reconciliation |

---

## NEXT STEPS

1. **Download both files:** PubWorld.jsx + camera_engine.rs
2. **Setup bridge:** WebSocket connection between React frontend and Rust backend
3. **Test integration:** Scene → Engine → Frame output
4. **Run deployment checklist:** FPS monitoring, mode switching, recording

---

*Integration complete. Ready to build.*
