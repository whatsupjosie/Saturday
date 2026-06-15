# PubCast AI — Complete Handoff Package
**Date:** 2026-05-08  
**Status:** Ready for Autonomous Development  
**Next Steps:** Use Codex prompt with your codebase

---

## WHAT YOU HAVE

### Existing Systems (Working)
- WebSocket orchestration
- Room/view switching  
- Lighting systems
- Professional skeleton system (`avatar_skeleton_system.py`)
- Sophisticated mocap pipeline (`mocap_precision.py`)
- Real audio processing (`mic_processor.js`)
- Performer concepts (partial)

### Existing Systems (Fragile)
- Authority is scattered across mic, performer, and UI layers
- Event flow is inconsistent
- Animation arbitration missing
- No runtime supervisor
- UI utilities are duplicated

### What's New
- **Codex prompt:** Autonomous runtime engineer for your project
- **Architecture:** Event bus + performer registry + station system
- **Avatar movement:** Locomotion system design
- **Animation arbitration:** Conflict resolution for animation layers
- **Safety guardrails:** Defined boundaries for autonomous work

---

## YOUR NEXT STEPS

### Immediate (This Week)

1. **Copy this Codex prompt** into your project:
   ```
   CODEX_PUBCAST_AUTONOMOUS_SPINE.md
   ```

2. **Start with Event Bus** (Milestone 1)
   - Create `runtime/spine/event_bus.py`
   - Create `runtime/spine/performer_registry.py`
   - Run tests, ensure no breaking changes

3. **Integrate WebSocket** to event bus
   - All state changes → events
   - All events → WebSocket broadcast

### Next Week (Milestone 2-3)

4. **Build Station System**
   - Stations as operational objects
   - Station registry
   - Activation/deactivation flow

5. **Enable Avatar Movement**
   - Locomotion system
   - Walk animation cycles
   - Movement replication

### Following Week (Milestone 4-5)

6. **Animation Arbitration**
   - Multiple animation sources
   - Priority-based blending
   - Clean conflict resolution

7. **Full Testing & Handoff**
   - All systems tested
   - Documentation complete
   - Ready for next iteration

---

## HOW TO USE THE CODEX PROMPT

### With Claude API (Best)
```python
import anthropic

with open("CODEX_PUBCAST_AUTONOMOUS_SPINE.md") as f:
    system_prompt = f.read()

client = anthropic.Anthropic()

response = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=4096,
    system=system_prompt,
    messages=[
        {
            "role": "user",
            "content": "Create the event bus for the runtime spine, with full tests."
        }
    ]
)

print(response.content[0].text)
```

### With Claude Web Interface
1. Open a new chat
2. Paste the Codex prompt as your first message
3. Ask it to do specific work:
   - "Create the EventBus class with full tests"
   - "Build the PerformerRegistry with event emission"
   - "Create the LocomotionSystem for avatar movement"
   - etc.

### Safety Pattern
- Ask it to do ONE milestone at a time
- Review generated code before committing
- Run tests immediately
- Only move to next milestone if all tests pass

---

## KEY DESIGN DECISIONS

### 1. Event Bus is Canonical
All state changes flow through events. This makes causality traceable and multiplayer-ready.

### 2. Performer Registry Owns Avatar State
No duplicate state. One performer object = one source of truth.

### 3. Stations are Runtime Objects
Not menus. Operational entities that performers interact with.

### 4. Animation Authority Arbitrates
Multiple systems want to animate the skeleton. Animation authority decides who wins.

### 5. Locomotion is Separate from Animation
Movement (where you are) ≠ Animation (how you look). Can be overridden independently.

---

## FILES TO REFERENCE

From your uploaded project:

- `avatar_skeleton_system.py` — Professional skeleton (USE THIS)
- `mocap_precision.py` — Performance capture system (USE THIS)
- `mic_processor.js` — Audio processing (DON'T MODIFY)
- `CODEX_RESUME_VOXEL_ENGINE_2026-05-02.md` — Previous context
- `PubCast_Authority_Spine_Build_Plan.md` — Architecture discussion
- `pubcast_architecture_review.md` — Authority analysis

These files contain context the Codex prompt will reference.

---

## TESTING CHECKLIST

Before considering each milestone complete:

- [ ] All new code has tests
- [ ] All tests pass
- [ ] No console errors or warnings
- [ ] WebSocket still connects
- [ ] Existing UI still works
- [ ] Events are being emitted
- [ ] State is canonical (no duplicates)
- [ ] Code is logged/debuggable

---

## DANGER ZONES (DON'T TOUCH)

- Mic system (mic_processor.js, mic_routes.py)
- Audio routing (unless bugfixing)
- WebSocket contracts (unless with migration)
- Active UI that users depend on
- Mocap pipeline (unless bugfixing)

---

## WHEN TO STOP AND ASK FOR HELP

The Codex prompt knows to stop and ask if:

- Rewriting major systems
- Breaking WebSocket contracts
- Destroying existing workflows
- Something architecturally unclear
- Scope creeping beyond sprint

Trust that boundary.

---

## SUCCESS LOOKS LIKE

After all milestones:

✓ Avatars spawn in rooms  
✓ Avatars move to target positions  
✓ Walk animations cycle correctly  
✓ Stations activate/deactivate cleanly  
✓ Multiple animation sources don't fight  
✓ Everything is event-driven  
✓ WebSocket broadcasts state correctly  
✓ No broken existing functionality  
✓ Next developer understands the system  

---

## RESOURCES

**Your skeleton system:**
```python
from avatar_skeleton_system import PubCastSkeleton, SkeletonAnimator
```

**Your mocap system:**
```python
from mocap_precision import PrecisionMocapCapture
```

**Standard patterns:**
- Event-driven architecture
- Registry pattern for systems
- Dataclass for state objects
- Async/await for operations

---

## FINAL WORD

You've built something real and complex.

The next phase is making it organized.

The Codex prompt knows the scope, the boundaries, and the goal.

Use it incrementally. Test everything. Don't skip milestones.

You've got this.

