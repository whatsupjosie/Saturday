# 🎭 PubCast AI — Virtual Production Studio with Emotional Intelligence

**Status:** Production-ready, fully functional, ready to run  
**Language:** Python 3.10+  
**Framework:** FastAPI + WebSocket  
**LLM:** OpenAI GPT-4 Turbo (with support for Claude, Gemini via adapters)  
**License:** © 2024-2025 Rear View Foresight LLC

---

## What This Is

PubCast AI is a **complete virtual production studio** that combines:

1. **EQ Adaptor** — Standalone emotional intelligence engine that analyzes user messages and detects emotional state (AMBIENT → ATTENTIVE → CARE → CRISIS)
2. **Multi-Agent System** — Multiple AI characters (Pete, Sheila, Horace) with specialized roles and personality
3. **Care-Aware Routing** — Characters respond differently based on detected emotional state
4. **Real-Time Streaming** — Character responses stream in real-time via WebSocket
5. **Memory System** — Persists user context and facts for stateful conversations

### The Innovation: Emotion-Driven Agent Selection

```
User: "I'm feeling overwhelmed"
           ↓
EQ Adaptor detects: CARE level
           ↓
Agent routing becomes:
- Pete (humor, engagement): Not selected (min AMBIENT, max CARE threshold exceeded)
- Sheila (empathy): ✅ SELECTED (specializes in care)
- Horace (analysis): Not selected (max CARE, user needs support not logic)
           ↓
Sheila responds with full emotional attention, recalls any prior memories
```

---

## Quick Start (5 minutes)

### 1. Install

```bash
# Clone or copy the files
git clone <repo> pubcast-ai
cd pubcast-ai

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure

```bash
# Copy environment template
cp .env.example .env

# Edit .env and add your OpenAI API key
# OPENAI_API_KEY=sk-...
```

### 3. Run

```bash
python3 main.py
```

Server starts at `http://localhost:8000`

### 4. Test

Open browser to `http://localhost:8000` and you'll see:
- System health status
- Available agents (Pete, Sheila, Horace)
- Quick start to create a room and start chatting

Or test via curl:

```bash
# Create a room
curl -X POST http://localhost:8000/api/rooms/create \
  -H "Content-Type: application/json" \
  -d '{"room_id": "test", "room_name": "Test Room"}'

# Check agents
curl http://localhost:8000/api/agents

# Check system health
curl http://localhost:8000/api/health
```

---

## Architecture

### System Components

```
┌─────────────────────────────────────────┐
│        FastAPI HTTP/WebSocket Server    │
│              (main.py)                  │
└────────────┬────────────────────────────┘
             │
             ├─→ REST API
             │   ├─ /api/rooms/* (room management)
             │   ├─ /api/agents/* (agent info)
             │   ├─ /api/users/{id}/emotion (EQ state)
             │   └─ /api/users/{id}/memory (recall)
             │
             └─→ WebSocket /ws/{room_id}
                 ├─ Connect → room
                 ├─ Message → orchestrator
                 └─ Events ← stream to client


         Orchestrator (orchestrator_wired.py)
         ├─ Room management
         ├─ Conversation history
         ├─ Agent selection logic
         └─ Event broadcasting
             │
             ├─→ EQ Adaptor (eq_adaptor.py)
             │   ├─ Input scoring (complexity, velocity)
             │   ├─ State machine (care level transitions)
             │   ├─ Memory system (recall)
             │   └─ Routing guidance
             │
             └─→ GPT Adapter (gpt_adapter.py)
                 ├─ Agent configs (Pete, Sheila, Horace)
                 ├─ OpenAI API calls
                 └─ Stream handling
```

### Data Flow: User Message to Agent Response

```
1. User sends message via WebSocket
   {"type": "chat", "text": "I'm struggling..."}
   
2. Server receives in /ws/{room_id} handler
   
3. Orchestrator.handle_message() is called
   - Gets room conversation history
   - Passes to EQ Adaptor
   
4. EQ Adaptor analyzes:
   - Message complexity (0-1)
   - Emotional velocity (0-1)
   - Transitions care state
   - Recalls relevant memories
   - Returns routing guidance
   
5. Orchestrator selects agents
   - Filters by min/max care level
   - Selects up to max_agents_per_turn
   
6. For each selected agent:
   - Builds character-specific system prompt
   - Injects EQ context + memories
   - Calls OpenAI GPT
   - Streams response back to client
   
7. Client receives events:
   - agent_start: Which agent is responding
   - stream_chunk: Each text token
   - agent_done: Agent finished
   - Each event broadcasts to all room participants
   
8. Final response stored in room history
```

---

## Configuration

Edit `.env` to configure:

```bash
# OpenAI (required)
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4-turbo

# Server
HOST=0.0.0.0
PORT=8000
LOG_LEVEL=INFO

# Storage
DATA_DIR=data
```

### Tuning EQ Behavior

Edit `eq_adaptor.py` constants:

```python
# In JeremyCricket and CharacterEngine:

care_threshold = 0.65  # Higher = more escalation
state_names = ["AMBIENT", "ATTENTIVE", "CARE", "TOTAL_CARE_MANDATE"]

# In RoutingEngine:
care_routing = {
    0: {...},  # AMBIENT - light, humorous
    1: {...},  # ATTENTIVE - show attention
    2: {...},  # CARE - deep support
    3: {...},  # TOTAL_CARE - crisis mode
}
```

### Adding Agents

Edit `gpt_adapter.py`, `DEFAULT_AGENTS` dict:

```python
"marcus": AgentConfig(
    agent_id="marcus",
    name="Marcus",
    role="Technical expert and troubleshooter",
    personality="Sharp, technical, problem-solving focused",
    system_prompt_template="""...""",
    min_care_level=0,
    max_care_level=1,  # Doesn't engage in crisis
    specialties=["technical", "analysis"],
),
```

---

## API Reference

### Rooms

**Create room:**
```bash
POST /api/rooms/create
{"room_id": "studio", "room_name": "Main Studio"}
→ {"ok": true, "room_id": "studio", ...}
```

**List rooms:**
```bash
GET /api/rooms
→ {"rooms": [...]}
```

**Get room details:**
```bash
GET /api/rooms/{room_id}
→ {"room_id": "studio", "participants": [...], ...}
```

### Agents

**List agents:**
```bash
GET /api/agents
→ {"agents": [{"agent_id": "pete", "name": "Pete", ...}, ...]}
```

**Get agent info:**
```bash
GET /api/agents/{agent_id}
→ {"agent_id": "pete", "name": "Pete", ...}
```

### User Emotion & Memory

**Get emotional state:**
```bash
GET /api/users/{user_id}/emotion
→ {"care_level": 2, "care_name": "CARE", "complexity": 0.7, ...}
```

**Store memory:**
```bash
POST /api/users/{user_id}/memory
{"content": "User loves hiking", "type": "episodic", "tags": ["hobby"]}
→ {"ok": true}
```

**Recall memories:**
```bash
GET /api/users/{user_id}/memories?query=hiking
→ {"memory_context": "## User Memory Context\n- [EPISODIC] User loves hiking\n..."}
```

### WebSocket

**Connect:**
```javascript
const ws = new WebSocket("ws://localhost:8000/ws/studio?user_id=user123");
```

**Send message:**
```javascript
ws.send(JSON.stringify({
  type: "chat",
  text: "Hello everyone, I need help with something"
}));
```

**Receive events:**
```javascript
ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  
  if (msg.type === "agent_start") {
    console.log(`${msg.payload.agent_name} is responding...`);
  } else if (msg.type === "stream_chunk") {
    console.log(msg.payload.chunk);  // Text token
  } else if (msg.type === "agent_done") {
    console.log("Agent finished");
  } else if (msg.type === "user_joined") {
    console.log(`${msg.user_id} joined`);
  }
};
```

### System Health

**Check health:**
```bash
GET /api/health
→ {
  "status": "healthy",
  "rooms_active": 2,
  "total_participants": 5,
  "agents_streaming": 1,
  "available_agents": ["pete", "sheila", "horace"]
}
```

---

## EQ Adaptor (Standalone Product)

The EQ Adaptor is designed to be **decoupled and reusable**. You can use it independently:

```python
from eq_adaptor import EQAdaptor

adaptor = EQAdaptor()

# Process a message
result = adaptor.process(
    user_id="user123",
    message="I'm feeling overwhelmed",
    history=[...]  # prior messages
)

# Result contains:
print(result['state'])           # care_level, complexity, velocity
print(result['routing'])         # Recommended agents
print(result['memory_context'])  # Recalled memories
print(result['prompt_injection']) # Context to inject into LLM
```

### Standalone Usage

Use the EQ Adaptor with **any LLM backend** (Claude, Gemini, Llama, etc.):

```python
# Attach to your own LLM
care_level = result['state']['care_level']
memory = result['memory_context']
tone = result['routing']['care_name']

# Build your own prompt
prompt = f"""
You are a helpful assistant. 

User emotional state: {tone}
{memory}

User: {user_message}
Assistant:
"""

# Call your LLM with the prompt
response = your_llm.generate(prompt)
```

---

## Testing

### Self-Test Scripts

Run the self-tests included in each module:

```bash
# Test EQ Adaptor
python3 eq_adaptor.py

# Test GPT Adapter (requires OPENAI_API_KEY)
export OPENAI_API_KEY=sk-...
python3 gpt_adapter.py
```

### Full Integration Test

Create `test_integration.py`:

```python
import asyncio
from eq_adaptor import EQAdaptor
from gpt_adapter import GPTAdapter
from orchestrator_wired import WiredOrchestrator

async def test():
    eq = EQAdaptor()
    gpt = GPTAdapter(api_key="sk-...")
    orch = WiredOrchestrator(eq, gpt)
    
    # Create room
    room = await orch.create_room("test", "Test")
    await orch.join_room("test", "user1")
    
    # Send message
    async for event in orch.handle_message("test", "user1", "Hi, how are you?"):
        print(f"{event.event_type}: {event.payload}")

asyncio.run(test())
```

---

## Deployment

### Local Development

```bash
python3 main.py
# Server at http://localhost:8000
```

### Production (Docker)

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

ENV OPENAI_API_KEY=${OPENAI_API_KEY}
ENV HOST=0.0.0.0
ENV PORT=8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build and run:
```bash
docker build -t pubcast-ai .
docker run -e OPENAI_API_KEY=sk-... -p 8000:8000 pubcast-ai
```

### Production (Systemd)

Create `/etc/systemd/system/pubcast.service`:

```ini
[Unit]
Description=PubCast AI Service
After=network.target

[Service]
Type=simple
User=pubcast
WorkingDirectory=/opt/pubcast-ai
Environment=OPENAI_API_KEY=sk-...
Environment=HOST=0.0.0.0
Environment=PORT=8000
ExecStart=/opt/pubcast-ai/venv/bin/python3 main.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl enable pubcast
sudo systemctl start pubcast
sudo systemctl status pubcast
```

---

## Extending the System

### Add a New Agent

1. Edit `gpt_adapter.py` and add to `DEFAULT_AGENTS`:

```python
"your_agent": AgentConfig(
    agent_id="your_agent",
    name="Your Agent Name",
    role="What they do",
    personality="Their personality",
    system_prompt_template="""...""",
    min_care_level=0,
    max_care_level=3,
    specialties=["tag1", "tag2"],
),
```

2. The agent is now available automatically.

### Use Different LLM Backend

Create a new adapter (e.g., `claude_adapter.py`):

```python
class ClaudeAdapter:
    def __init__(self, api_key, agents=None):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.agents = agents or DEFAULT_AGENTS
    
    async def stream_response(self, agent_id, user_message, ...):
        # Implement streaming from Claude
        async with self.client.messages.stream(...) as stream:
            async for text in stream.text_stream:
                yield text
```

Then in `main.py`:
```python
# Use Claude instead
gpt_adapter = ClaudeAdapter(api_key=...)
```

### Customize EQ Behavior

Edit `eq_adaptor.py`:

- **InputScorer** — Change complexity/velocity detection
- **CharacterEngine** — Adjust care level transitions
- **RoutingEngine** — Change agent recommendations per care level
- **MemoryIndex** — Integrate vector embeddings instead of substring search

---

## Monitoring & Debugging

### Logs

Check logs while running:
```bash
# Set LOG_LEVEL=DEBUG in .env
tail -f logs/pubcast.log
```

### API Documentation

Interactive docs at:
- http://localhost:8000/docs (Swagger UI)
- http://localhost:8000/redoc (ReDoc)

### WebSocket Debugging

Use browser DevTools Console:

```javascript
ws = new WebSocket("ws://localhost:8000/ws/test?user_id=debug");
ws.onopen = () => console.log("Connected");
ws.onmessage = (e) => console.log(JSON.parse(e.data));
ws.send(JSON.stringify({type: "chat", text: "test"}));
```

---

## Troubleshooting

### "OPENAI_API_KEY not set"
- Check `.env` file exists
- Verify `OPENAI_API_KEY=sk-...` is set
- Run `source .env` (sometimes needed)

### "Module not found" errors
- Check virtual environment is activated
- Run `pip install -r requirements.txt` again
- Verify all 4 Python files are in same directory

### WebSocket connection refused
- Check server is running (`python3 main.py`)
- Check port 8000 is not in use
- Change port in `.env` if needed

### Agents not responding
- Check OpenAI API key is valid
- Check account has credits
- Check rate limits not exceeded
- Enable `LOG_LEVEL=DEBUG` to see errors

---

## Next Steps

1. **Replace mock with real LLM** ✅ (You're here)
2. **Add authentication** — Implement login/session management
3. **Persistent storage** — Move memory to PostgreSQL + Vector DB
4. **Audio input/output** — Add voice via Whisper + TTS
5. **Avatar rendering** — Add 3D character models
6. **Multi-server scaling** — Use Redis for rooms, Celery for agents
7. **Mobile client** — React Native app

---

## Architecture Decision Log

### Why EQ Adaptor is Standalone
- Useful for any LLM application (not just PubCast)
- Can be packaged/sold independently
- Decouples emotional intelligence from specific agents
- Allows testing/iteration without server

### Why Care Levels Instead of Just Scoring
- Care levels are **qualitative and obvious** to users
- State machine is easier to reason about than continuous scores
- Emergency escalation (TOTAL_CARE_MANDATE) is explicit and trackable
- Agents can be trained for specific care modes

### Why Multiple Adapters (GPT, Claude, Gemini)
- Different LLMs have different strengths:
  - GPT-4: Best for nuance and multi-turn
  - Claude: Best for harmlessness and long context
  - Gemini: Best for multimodal and cost
- Switching backends is easy (just swap adapter)
- You can A/B test which LLM feels best

---

## License & Credits

© 2024-2025 Rear View Foresight LLC  
"Feic Mo Chroí" — See My Heart

This system represents 2+ years of research into emotionally-intelligent AI and brings together:
- Emotional state tracking (EQ layer)
- Multi-agent orchestration (concurrent routing)
- Memory persistence (stateful conversations)
- Real-time streaming (production websockets)

---

## Support

Questions? Issues?
- 📧 Email: support@pubcast.ai
- 💬 Discussions: GitHub Issues
- 📖 Docs: This README + inline code comments
- 🎭 Demo: Run locally and play around

---

**Built with care. Run it. Break it. Make it better. Show me what you build.**
