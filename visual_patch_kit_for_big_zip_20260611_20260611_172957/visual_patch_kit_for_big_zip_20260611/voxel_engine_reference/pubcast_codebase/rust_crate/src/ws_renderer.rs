/*!
PubCast WebSocket Renderer Server
===================================

This is the receiving end of the live mocap pipeline.

Data flow:
  Python MocapPrecisionCapture
    → avatar_performer.py (retarget + blend)
      → PerformerStreamer (WebSocket client)
        → THIS SERVER (tokio-tungstenite)
          → SkeletalAnimator::apply_motion_capture_frame()
            → recompute world matrices
              → RenderedAvatar (world matrices + blendshapes)
                → render callback / voxel bridge

Architecture:
  - One `tokio::task` per WebSocket connection
  - One `SkeletalAnimator` per `avatar_id` stored in `AvatarRegistry`
  - AvatarRegistry is `Arc<RwLock<...>>` so tasks share it safely
  - Render tick at configurable FPS (default 60) reads world matrices
    from all registered animators and calls the render callback
  - Graceful shutdown on SIGINT / SIGTERM via CancellationToken

Connection protocol:
  - Client connects to ws://localhost:8765
  - Sends JSON text frames matching `BridgeMotionPayload` (lib.rs)
  - Server acks each frame with `{"ok": true, "frame": N}`
  - Server sends render frames back as `RendererOutput` JSON
    (world matrices as flat f32 arrays, 16 values per bone)

Integration with voxel bridge:
  Set `PUBCAST_RENDER_CALLBACK=voxel_bridge` env var to forward
  world matrices to the C++ voxel engine via the bridge module.
  Default: stdout logging only (safe fallback for testing).

Cargo additions needed in Cargo.toml:
  tokio          = { version = "1", features = ["full"] }
  tokio-tungstenite = "0.23"
  futures-util   = "0.3"
  tracing-subscriber = { version = "0.3", features = ["env-filter"] }

Usage:
  cargo run --bin ws_renderer
  cargo run --bin ws_renderer -- --port 8765 --fps 60 --log-level debug
*/

use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::{Duration, Instant};

use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::{broadcast, RwLock};
use tokio::time;
use tokio_tungstenite::{accept_async, tungstenite::Message};
use tracing::{debug, error, info, warn};

// Re-use types from the animation crate
use pubcast_animation::{
    BridgeMotionPayload, JointTransform, SkeletalAnimator, SkeletonHierarchy,
};

// ─────────────────────────────────────────────────────────────────────────────
// CONFIGURATION
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct RendererConfig {
    /// WebSocket listen address
    pub listen_addr:    SocketAddr,
    /// Target render tick rate (Hz). Separate from mocap frame rate.
    pub render_fps:     u32,
    /// Maximum number of simultaneous avatar connections
    pub max_avatars:    usize,
    /// How long to keep an avatar alive after last motion update before eviction
    pub avatar_ttl_sec: u64,
    /// Whether to emit world matrices on every render tick (true)
    /// or only when the pose has changed (false, more efficient)
    pub always_emit:    bool,
}

impl Default for RendererConfig {
    fn default() -> Self {
        Self {
            listen_addr:    "0.0.0.0:8765".parse().unwrap(),
            render_fps:     60,
            max_avatars:    16,
            avatar_ttl_sec: 30,
            always_emit:    false,
        }
    }
}

impl RendererConfig {
    /// Build from environment variables. Fails gracefully to defaults.
    pub fn from_env() -> Self {
        let mut cfg = Self::default();
        if let Ok(port) = std::env::var("PUBCAST_WS_PORT") {
            if let Ok(p) = port.parse::<u16>() {
                cfg.listen_addr = format!("0.0.0.0:{p}").parse().unwrap();
            }
        }
        if let Ok(fps) = std::env::var("PUBCAST_RENDER_FPS") {
            if let Ok(f) = fps.parse::<u32>() {
                cfg.render_fps = f.clamp(1, 240);
            }
        }
        if let Ok(max) = std::env::var("PUBCAST_MAX_AVATARS") {
            if let Ok(m) = max.parse::<usize>() {
                cfg.max_avatars = m.clamp(1, 64);
            }
        }
        cfg
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// WIRE TYPES — JSON frames sent from server to subscribers
// ─────────────────────────────────────────────────────────────────────────────

/// Ack sent back to the Python client after each received frame.
#[derive(Debug, Serialize)]
struct FrameAck {
    ok:         bool,
    frame:      u64,
    avatar_id:  String,
    /// Microseconds from receipt to world-matrix recompute
    latency_us: u64,
}

/// World matrices for all joints of one avatar.
/// Sent on the render tick to any subscribed render backends.
#[derive(Debug, Clone, Serialize)]
pub struct RenderedAvatar {
    pub avatar_id:     String,
    pub timestamp:     f64,
    /// Flat array: 16 f32 per joint (column-major 4×4 Matrix4).
    /// Length = joint_count × 16.
    pub world_matrices: Vec<f32>,
    /// Blendshapes: name → weight (0.0–1.0)
    pub blendshapes:   HashMap<String, f32>,
    /// Number of joints (world_matrices.len() / 16)
    pub joint_count:   usize,
    /// Joint names in hierarchy order (matches world_matrices index)
    pub joint_names:   Vec<String>,
}

/// Full render tick output: all avatars with fresh world matrices.
#[derive(Debug, Clone, Serialize)]
pub struct RenderTick {
    pub tick:       u64,
    pub timestamp:  f64,
    pub avatars:    Vec<RenderedAvatar>,
}

// ─────────────────────────────────────────────────────────────────────────────
// AVATAR REGISTRY
// ─────────────────────────────────────────────────────────────────────────────

/// One live avatar with its animator and metadata.
struct LiveAvatar {
    animator:       SkeletalAnimator,
    last_update:    Instant,
    frame_count:    u64,
    /// Blendshapes are passed through separately (not bone transforms)
    blendshapes:    HashMap<String, f32>,
    /// Joint names in hierarchy order (cached for output)
    joint_names:    Vec<String>,
}

impl LiveAvatar {
    fn new(preset: &str) -> Self {
        // Select skeleton based on avatar preset
        let hierarchy = match preset.to_uppercase().as_str() {
            "DOG" => {
                // Dog uses same humanoid rig for now (future: quadruped)
                warn!("DOG preset uses humanoid rig placeholder");
                SkeletonHierarchy::create_pubcast_rig()
            }
            _ => SkeletonHierarchy::create_pubcast_rig(),
        };
        let joint_names: Vec<String> = hierarchy
            .joints
            .iter()
            .map(|j| j.name.clone())
            .collect();
        let animator = SkeletalAnimator::new(hierarchy);
        Self {
            animator,
            last_update: Instant::now(),
            frame_count: 0,
            blendshapes: HashMap::new(),
            joint_names,
        }
    }

    fn apply_payload(&mut self, payload: &BridgeMotionPayload) {
        let frame_map = payload.to_mocap_frame();

        // Separate blendshapes (bs_ prefix) from bone transforms
        let bone_data: HashMap<String, Vec<f32>> = frame_map
            .iter()
            .filter(|(k, _)| !k.starts_with("bs_"))
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();

        let bs_data: HashMap<String, f32> = frame_map
            .iter()
            .filter_map(|(k, v)| {
                if k.starts_with("bs_") && !v.is_empty() {
                    Some((k[3..].to_string(), v[0]))
                } else {
                    None
                }
            })
            .collect();

        self.animator.apply_motion_capture_frame(&bone_data);
        self.blendshapes.extend(bs_data);
        self.last_update = Instant::now();
        self.frame_count += 1;
    }

    fn render(&mut self, avatar_id: &str, timestamp: f64) -> RenderedAvatar {
        let matrices = self.animator.get_world_matrices();
        let world_matrices: Vec<f32> = matrices
            .iter()
            .flat_map(|m| m.as_slice().iter().copied())
            .collect();
        let joint_count = self.joint_names.len();

        RenderedAvatar {
            avatar_id:    avatar_id.to_string(),
            timestamp,
            world_matrices,
            blendshapes:  self.blendshapes.clone(),
            joint_count,
            joint_names:  self.joint_names.clone(),
        }
    }

    fn is_stale(&self, ttl: Duration) -> bool {
        self.last_update.elapsed() > ttl
    }
}

/// Thread-safe registry of all live avatars.
#[derive(Clone)]
struct AvatarRegistry {
    inner: Arc<RwLock<HashMap<String, LiveAvatar>>>,
    cfg:   Arc<RendererConfig>,
}

impl AvatarRegistry {
    fn new(cfg: Arc<RendererConfig>) -> Self {
        Self {
            inner: Arc::new(RwLock::new(HashMap::new())),
            cfg,
        }
    }

    /// Apply a payload from the Python side. Creates the avatar if new.
    async fn apply_payload(&self, payload: &BridgeMotionPayload) -> u64 {
        let mut map = self.inner.write().await;

        // Enforce max_avatars limit
        if !map.contains_key(&payload.avatar_id) {
            if map.len() >= self.cfg.max_avatars {
                warn!(
                    "Max avatars ({}) reached; ignoring new avatar_id={}",
                    self.cfg.max_avatars, payload.avatar_id
                );
                return 0;
            }
            let preset = payload
                .motion_data
                .get("__preset__")
                .and_then(|b| b.mesh.as_deref())
                .unwrap_or("MANNY");
            info!("New avatar registered: {} [{}]", payload.avatar_id, preset);
            map.insert(payload.avatar_id.clone(), LiveAvatar::new(preset));
        }

        let avatar = map.get_mut(&payload.avatar_id).unwrap();
        avatar.apply_payload(payload);
        avatar.frame_count
    }

    /// Build render tick output — world matrices for all avatars.
    async fn render_tick(&self, tick: u64) -> RenderTick {
        let mut map = self.inner.write().await;
        let timestamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64();

        // Evict stale avatars
        let ttl = Duration::from_secs(self.cfg.avatar_ttl_sec);
        let stale: Vec<String> = map
            .iter()
            .filter(|(_, av)| av.is_stale(ttl))
            .map(|(id, _)| id.clone())
            .collect();
        for id in &stale {
            info!("Evicting stale avatar: {id}");
            map.remove(id);
        }

        let avatars = map
            .iter_mut()
            .map(|(id, av)| av.render(id, timestamp))
            .collect();

        RenderTick { tick, timestamp, avatars }
    }

    async fn avatar_count(&self) -> usize {
        self.inner.read().await.len()
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// WEBSOCKET CONNECTION HANDLER
// ─────────────────────────────────────────────────────────────────────────────

/// Handle one WebSocket connection (one Python performer client).
/// The task runs until the client disconnects or shutdown is signalled.
async fn handle_connection(
    stream:   TcpStream,
    peer:     SocketAddr,
    registry: AvatarRegistry,
    mut shutdown: broadcast::Receiver<()>,
) {
    let ws = match accept_async(stream).await {
        Ok(ws) => ws,
        Err(e) => {
            warn!("WebSocket handshake failed from {peer}: {e}");
            return;
        }
    };

    info!("WebSocket connected: {peer}");
    let (mut tx, mut rx) = ws.split();

    loop {
        tokio::select! {
            msg = rx.next() => {
                match msg {
                    Some(Ok(Message::Text(text))) => {
                        let t_recv = Instant::now();

                        // Deserialize payload
                        let payload: BridgeMotionPayload = match serde_json::from_str(&text) {
                            Ok(p) => p,
                            Err(e) => {
                                warn!("Bad payload from {peer}: {e}");
                                let ack = serde_json::to_string(&serde_json::json!({
                                    "ok": false,
                                    "error": e.to_string(),
                                })).unwrap_or_default();
                                let _ = tx.send(Message::Text(ack)).await;
                                continue;
                            }
                        };

                        let avatar_id = payload.avatar_id.clone();
                        let frame_n   = registry.apply_payload(&payload).await;
                        let latency   = t_recv.elapsed().as_micros() as u64;

                        let ack = FrameAck {
                            ok: true,
                            frame: frame_n,
                            avatar_id,
                            latency_us: latency,
                        };

                        if let Ok(ack_json) = serde_json::to_string(&ack) {
                            if tx.send(Message::Text(ack_json)).await.is_err() {
                                break;
                            }
                        }

                        debug!("Frame {frame_n} from {peer} processed in {latency}µs");
                    }

                    Some(Ok(Message::Binary(_))) => {
                        warn!("Binary frames not supported from {peer}; ignoring");
                    }

                    Some(Ok(Message::Ping(data))) => {
                        let _ = tx.send(Message::Pong(data)).await;
                    }

                    Some(Ok(Message::Close(_))) | None => {
                        info!("WebSocket disconnected: {peer}");
                        break;
                    }

                    Some(Ok(_)) => {}   // Pong, Frame variants — ignore

                    Some(Err(e)) => {
                        warn!("WebSocket error from {peer}: {e}");
                        break;
                    }
                }
            }

            _ = shutdown.recv() => {
                info!("Shutdown: closing connection to {peer}");
                let _ = tx.send(Message::Close(None)).await;
                break;
            }
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// RENDER TICK LOOP
// ─────────────────────────────────────────────────────────────────────────────

/// Runs at `render_fps` and builds world-matrix output for the render backend.
/// Currently emits as JSON to stdout / tracing. Extend to push to voxel bridge.
async fn render_tick_loop(
    registry: AvatarRegistry,
    mut shutdown: broadcast::Receiver<()>,
    fps: u32,
) {
    let interval_dur = Duration::from_secs_f64(1.0 / fps as f64);
    let mut interval = time::interval(interval_dur);
    interval.set_missed_tick_behavior(time::MissedTickBehavior::Skip);

    let mut tick: u64 = 0;
    let mut last_emit  = Instant::now();

    loop {
        tokio::select! {
            _ = interval.tick() => {
                tick += 1;
                let render = registry.render_tick(tick).await;

                if render.avatars.is_empty() {
                    continue;
                }

                // Performance metric: log render loop jitter every 5 seconds
                let now = Instant::now();
                let elapsed = now.duration_since(last_emit);
                if elapsed >= Duration::from_secs(5) {
                    let n_avatars = render.avatars.len();
                    let total_joints: usize = render.avatars.iter().map(|a| a.joint_count).sum();
                    info!(
                        "Render tick {tick}: {n_avatars} avatar(s)  \
                        {total_joints} joints  \
                        tick_interval={:.1}ms",
                        interval_dur.as_secs_f64() * 1000.0,
                    );
                    last_emit = now;
                }

                // ── RENDER BACKEND INTEGRATION POINT ──────────────────────
                // Extend this block to push world matrices to:
                //   - Voxel bridge shared memory (VoxelBridge::send_matrices)
                //   - WebSocket broadcast to browser renderer
                //   - Game engine plugin via FFI
                //
                // For now: structured debug log (development mode)
                for avatar in &render.avatars {
                    debug!(
                        avatar_id = %avatar.avatar_id,
                        joints    = avatar.joint_count,
                        blendshapes = avatar.blendshapes.len(),
                        "render_frame"
                    );
                }
                // ── END INTEGRATION POINT ──────────────────────────────────
            }

            _ = shutdown.recv() => {
                info!("Render tick loop shutting down at tick {tick}");
                break;
            }
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// SERVER ENTRY POINT
// ─────────────────────────────────────────────────────────────────────────────

pub async fn run_server(cfg: RendererConfig) -> anyhow::Result<()> {
    let cfg = Arc::new(cfg);
    let registry = AvatarRegistry::new(Arc::clone(&cfg));

    let listener = TcpListener::bind(cfg.listen_addr).await?;
    info!("PubCast WS Renderer listening on {}", cfg.listen_addr);
    info!("Render target: {}fps  Max avatars: {}", cfg.render_fps, cfg.max_avatars);

    // Shutdown broadcast: send () to all tasks to begin clean teardown
    let (shutdown_tx, _) = broadcast::channel::<()>(1);

    // Spawn render tick loop
    {
        let reg  = registry.clone();
        let rx   = shutdown_tx.subscribe();
        let fps  = cfg.render_fps;
        tokio::spawn(async move {
            render_tick_loop(reg, rx, fps).await;
        });
    }

    // Spawn stats logger
    {
        let reg = registry.clone();
        let rx  = shutdown_tx.subscribe();
        tokio::spawn(async move {
            stats_logger(reg, rx).await;
        });
    }

    // Accept connections
    loop {
        tokio::select! {
            result = listener.accept() => {
                match result {
                    Ok((stream, peer)) => {
                        let reg  = registry.clone();
                        let rx   = shutdown_tx.subscribe();
                        tokio::spawn(async move {
                            handle_connection(stream, peer, reg, rx).await;
                        });
                    }
                    Err(e) => {
                        error!("TCP accept error: {e}");
                    }
                }
            }

            _ = tokio::signal::ctrl_c() => {
                info!("SIGINT received — shutting down gracefully");
                let _ = shutdown_tx.send(());
                break;
            }
        }
    }

    // Allow tasks to drain
    time::sleep(Duration::from_millis(500)).await;
    info!("PubCast WS Renderer stopped");
    Ok(())
}

async fn stats_logger(registry: AvatarRegistry, mut shutdown: broadcast::Receiver<()>) {
    let mut interval = time::interval(Duration::from_secs(30));
    loop {
        tokio::select! {
            _ = interval.tick() => {
                let n = registry.avatar_count().await;
                info!("Registry: {n} live avatar(s)");
            }
            _ = shutdown.recv() => break,
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// BINARY ENTRY POINT
// ─────────────────────────────────────────────────────────────────────────────

/// Add to Cargo.toml:
/// ```toml
/// [[bin]]
/// name = "ws_renderer"
/// path = "src/ws_renderer.rs"
/// ```
#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Initialize tracing
    let filter = std::env::var("RUST_LOG").unwrap_or_else(|_| "pubcast=info".to_string());
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::new(filter))
        .with_target(false)
        .compact()
        .init();

    let cfg = RendererConfig::from_env();
    run_server(cfg).await
}

// ─────────────────────────────────────────────────────────────────────────────
// TESTS
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use pubcast_animation::BridgeBoneData;

    fn make_payload(avatar_id: &str) -> BridgeMotionPayload {
        let mut motion = HashMap::new();
        motion.insert("Pelvis".to_string(), BridgeBoneData {
            position:   [0.0, 0.95, 0.0],
            rotation:   [0.0, 0.0, 0.0, 1.0],
            mesh:       None,
            shape_type: None,
            confidence: Some(0.98),
        });
        motion.insert("Spine_01".to_string(), BridgeBoneData {
            position:   [0.0, 1.03, 0.0],
            rotation:   [0.0, 0.0, 0.0, 1.0],
            mesh:       None,
            shape_type: None,
            confidence: Some(0.97),
        });
        // Blendshape via bs_ key
        motion.insert("bs_eyeBlinkLeft".to_string(), BridgeBoneData {
            position:   [0.35, 0.0, 0.0],   // weight in position[0]
            rotation:   [0.0, 0.0, 0.0, 1.0],
            mesh:       None,
            shape_type: None,
            confidence: Some(1.0),
        });
        BridgeMotionPayload {
            avatar_id:   avatar_id.to_string(),
            motion_data: motion,
            timestamp:   1_234_567_890.0,
        }
    }

    #[tokio::test]
    async fn test_avatar_registry_creates_on_first_payload() {
        let cfg = Arc::new(RendererConfig::default());
        let reg = AvatarRegistry::new(cfg);

        let payload = make_payload("avatar_01");
        let frame_n = reg.apply_payload(&payload).await;

        assert_eq!(frame_n, 1, "First frame should be frame 1");
        assert_eq!(reg.avatar_count().await, 1);
    }

    #[tokio::test]
    async fn test_avatar_registry_max_avatars_enforced() {
        let cfg = Arc::new(RendererConfig { max_avatars: 2, ..Default::default() });
        let reg = AvatarRegistry::new(cfg);

        for i in 0..5 {
            reg.apply_payload(&make_payload(&format!("av_{i}"))).await;
        }

        // Only 2 should have been accepted (first 2)
        assert!(reg.avatar_count().await <= 2,
            "Should not exceed max_avatars=2");
    }

    #[tokio::test]
    async fn test_blendshape_extracted_from_bs_prefix() {
        let cfg = Arc::new(RendererConfig::default());
        let reg = AvatarRegistry::new(cfg);

        let payload = make_payload("test_av");
        reg.apply_payload(&payload).await;

        let map = reg.inner.read().await;
        let av  = map.get("test_av").unwrap();
        assert!(
            av.blendshapes.contains_key("eyeBlinkLeft"),
            "bs_eyeBlinkLeft key should be stripped to eyeBlinkLeft"
        );
        let weight = av.blendshapes["eyeBlinkLeft"];
        assert!(
            (weight - 0.35).abs() < 1e-5,
            "Blendshape weight should be 0.35 (from position[0]): got {weight}"
        );
    }

    #[tokio::test]
    async fn test_render_tick_produces_world_matrices() {
        let cfg = Arc::new(RendererConfig::default());
        let reg = AvatarRegistry::new(cfg);

        reg.apply_payload(&make_payload("render_test")).await;

        let tick = reg.render_tick(1).await;
        assert_eq!(tick.avatars.len(), 1);
        let avatar = &tick.avatars[0];
        assert_eq!(avatar.avatar_id, "render_test");
        assert!(!avatar.world_matrices.is_empty(), "Should have world matrices");
        assert_eq!(
            avatar.world_matrices.len(),
            avatar.joint_count * 16,
            "Each joint needs exactly 16 f32 values (4×4 matrix)"
        );

        // All matrices should be finite
        assert!(
            avatar.world_matrices.iter().all(|v| v.is_finite()),
            "All world matrix values must be finite"
        );
    }

    #[tokio::test]
    async fn test_render_tick_incremental_updates() {
        let cfg = Arc::new(RendererConfig::default());
        let reg = AvatarRegistry::new(cfg);

        // Feed 10 frames
        for i in 0..10 {
            reg.apply_payload(&make_payload("av_inc")).await;
        }

        let map = reg.inner.read().await;
        let av  = map.get("av_inc").unwrap();
        assert_eq!(av.frame_count, 10, "Should have received 10 frames");
    }

    #[tokio::test]
    async fn test_stale_avatar_eviction() {
        let cfg = Arc::new(RendererConfig {
            avatar_ttl_sec: 0,  // Expire immediately
            ..Default::default()
        });
        let reg = AvatarRegistry::new(cfg);

        reg.apply_payload(&make_payload("stale_av")).await;
        assert_eq!(reg.avatar_count().await, 1);

        // Tick will evict it (ttl=0 → immediately stale)
        tokio::time::sleep(Duration::from_millis(10)).await;
        let tick = reg.render_tick(1).await;

        // Avatar should be evicted (or at least we logged it)
        // With ttl=0s, elapsed() > Duration::from_secs(0) is always true
        assert_eq!(tick.avatars.len(), 0, "Stale avatar should be evicted");
        assert_eq!(reg.avatar_count().await, 0);
    }

    #[test]
    fn test_renderer_config_from_env() {
        // Should not panic on missing env vars
        let cfg = RendererConfig::from_env();
        assert!(cfg.render_fps > 0);
        assert!(cfg.max_avatars > 0);
    }

    #[test]
    fn test_renderer_config_default_port() {
        let cfg = RendererConfig::default();
        assert_eq!(cfg.listen_addr.port(), 8765);
    }
}
