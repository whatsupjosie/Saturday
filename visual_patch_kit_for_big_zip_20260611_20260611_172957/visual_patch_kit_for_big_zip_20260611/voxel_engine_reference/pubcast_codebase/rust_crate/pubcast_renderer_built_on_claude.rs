/*!
Core Rendering Module for PubCast AI — built on Claude repair 2, hardened further.

Goals of this donor:
- Keep the stronger renderer shape (mesh pool, frame budget, queue-based rendering)
- Remove compile-risk / honesty issues
- Provide a real fallback draw path instead of a logged placeholder
- Keep integration seams narrow and explicit

Still intentionally NOT solved here:
- Full voxel meshing pipeline
- Python ↔ Rust bridge implementation
- Real CPU/GPU/memory telemetry probes
- Scene-driven mesh extraction
*/

use std::collections::HashMap;
use std::error::Error;
use std::sync::{Arc, Mutex};
use std::time::Instant;

use cgmath::SquareMatrix;
use wgpu::util::DeviceExt;
use winit::window::Window;

use crate::camera::{Camera, CameraManager};
use crate::scene::SceneManager;

// ============================================================================
// Performance Budget & Frame Timing
// ============================================================================

#[derive(Debug, Clone, Copy)]
pub struct FrameBudget {
    /// Target frame time in milliseconds (e.g. 16.67 for 60 FPS)
    pub target_frame_ms: f64,
    /// Hard ceiling before quality should degrade aggressively.
    pub max_frame_ms: f64,
    /// If a frame exceeds this threshold, report reduced quality.
    pub quality_reduction_threshold: f64,
}

impl Default for FrameBudget {
    fn default() -> Self {
        Self {
            target_frame_ms: 16.67,
            max_frame_ms: 33.33,
            quality_reduction_threshold: 13.34,
        }
    }
}

// ============================================================================
// Voxel Mesh Management
// ============================================================================

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct MeshHandle(pub u64);

pub struct VoxelMesh {
    pub handle: MeshHandle,
    pub vertex_buffer: wgpu::Buffer,
    pub index_buffer: wgpu::Buffer,
    pub num_indices: u32,
    pub vertex_count: u32,
    pub lod_level: u8,
    pub last_accessed: Instant,
    pub is_dirty: bool,
}

#[derive(Debug)]
pub struct MeshPool {
    meshes: HashMap<MeshHandle, VoxelMesh>,
    next_handle: u64,
    max_meshes: usize,
}

impl MeshPool {
    pub fn new(max_meshes: usize) -> Self {
        Self {
            meshes: HashMap::new(),
            next_handle: 1,
            max_meshes,
        }
    }

    pub fn allocate_mesh(
        &mut self,
        device: &wgpu::Device,
        vertices: &[Vertex],
        indices: &[u16],
        lod_level: u8,
    ) -> Result<MeshHandle, Box<dyn Error>> {
        if vertices.is_empty() || indices.is_empty() {
            return Err("Cannot allocate empty mesh".into());
        }

        if self.meshes.len() >= self.max_meshes {
            self.evict_lru_mesh()?;
        }

        let vertex_buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some(&format!("Voxel Mesh Vertex Buffer {}", self.next_handle)),
            contents: bytemuck::cast_slice(vertices),
            usage: wgpu::BufferUsages::VERTEX | wgpu::BufferUsages::COPY_DST,
        });

        let index_buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some(&format!("Voxel Mesh Index Buffer {}", self.next_handle)),
            contents: bytemuck::cast_slice(indices),
            usage: wgpu::BufferUsages::INDEX | wgpu::BufferUsages::COPY_DST,
        });

        let handle = MeshHandle(self.next_handle);
        self.next_handle += 1;

        let mesh = VoxelMesh {
            handle,
            vertex_buffer,
            index_buffer,
            num_indices: indices.len() as u32,
            vertex_count: vertices.len() as u32,
            lod_level,
            last_accessed: Instant::now(),
            is_dirty: false,
        };

        self.meshes.insert(handle, mesh);
        Ok(handle)
    }

    pub fn get_mesh(&mut self, handle: MeshHandle) -> Option<&VoxelMesh> {
        let mesh = self.meshes.get_mut(&handle)?;
        mesh.last_accessed = Instant::now();
        Some(mesh)
    }

    pub fn mark_dirty(&mut self, handle: MeshHandle) {
        if let Some(mesh) = self.meshes.get_mut(&handle) {
            mesh.is_dirty = true;
        }
    }

    pub fn mesh_count(&self) -> usize {
        self.meshes.len()
    }

    fn evict_lru_mesh(&mut self) -> Result<(), Box<dyn Error>> {
        let oldest = self
            .meshes
            .iter()
            .min_by_key(|(_, mesh)| mesh.last_accessed)
            .map(|(handle, _)| *handle)
            .ok_or("No meshes to evict")?;

        self.meshes.remove(&oldest);
        Ok(())
    }

    pub fn clear(&mut self) {
        self.meshes.clear();
    }
}

// ============================================================================
// Performance Metrics
// ============================================================================

#[derive(Debug, Clone, Copy, Default)]
pub struct PerformanceMetrics {
    pub frame_rate: f64,
    pub frame_time_ms: f64,
    pub cpu_usage_percent: f64,
    pub gpu_usage_percent: f64,
    pub memory_usage_mb: f64,
    pub draw_calls: u32,
    pub triangle_count: u32,
    pub mesh_count: u32,
    pub voxel_count: u64,
    pub frame_drops: u32,
    pub quality_level: u8,
}

#[derive(Debug)]
pub struct MetricsCollector {
    metrics: PerformanceMetrics,
    frame_samples: Vec<f64>,
    max_samples: usize,
}

impl MetricsCollector {
    pub fn new(window_size: usize) -> Self {
        Self {
            metrics: PerformanceMetrics::default(),
            frame_samples: Vec::with_capacity(window_size),
            max_samples: window_size,
        }
    }

    pub fn record_frame(&mut self, frame_time_ms: f64) {
        self.frame_samples.push(frame_time_ms);
        if self.frame_samples.len() > self.max_samples {
            self.frame_samples.remove(0);
        }

        if !self.frame_samples.is_empty() {
            let avg = self.frame_samples.iter().sum::<f64>() / self.frame_samples.len() as f64;
            self.metrics.frame_time_ms = frame_time_ms;
            self.metrics.frame_rate = if avg > 0.0 { 1000.0 / avg } else { 0.0 };
        }
    }

    pub fn get_metrics(&self) -> PerformanceMetrics {
        self.metrics
    }

    pub fn update_live_fields(
        &mut self,
        draw_calls: u32,
        triangle_count: u32,
        mesh_count: u32,
        quality_level: u8,
        frame_dropped: bool,
    ) {
        self.metrics.draw_calls = draw_calls;
        self.metrics.triangle_count = triangle_count;
        self.metrics.mesh_count = mesh_count;
        self.metrics.quality_level = quality_level;
        if frame_dropped {
            self.metrics.frame_drops = self.metrics.frame_drops.saturating_add(1);
        }

        // Honest placeholders until real probes are wired in.
        self.metrics.cpu_usage_percent = 0.0;
        self.metrics.gpu_usage_percent = 0.0;
        self.metrics.memory_usage_mb = 0.0;
        self.metrics.voxel_count = 0;
    }
}

#[derive(Debug, Clone, Copy, Default)]
pub struct RenderStats {
    pub draw_calls: u32,
    pub triangle_count: u32,
    pub used_fallback_cube: bool,
}

// ============================================================================
// Core Render State
// ============================================================================

pub struct RenderState<'w> {
    surface: wgpu::Surface<'w>,
    device: wgpu::Device,
    queue: wgpu::Queue,
    config: wgpu::SurfaceConfiguration,
    size: winit::dpi::PhysicalSize<u32>,
    render_pipeline: wgpu::RenderPipeline,
    camera_uniform_buffer: wgpu::Buffer,
    camera_bind_group: wgpu::BindGroup,
    mesh_pool: Arc<Mutex<MeshPool>>,
    frame_budget: FrameBudget,
    fallback_vertex_buffer: wgpu::Buffer,
    fallback_index_buffer: wgpu::Buffer,
    fallback_num_indices: u32,
}

impl<'w> RenderState<'w> {
    pub async fn new(window: &'w Window, frame_budget: FrameBudget) -> Result<Self, Box<dyn Error>> {
        let size = window.inner_size();
        if size.width == 0 || size.height == 0 {
            return Err("Render window has zero width or height".into());
        }

        let instance = wgpu::Instance::new(wgpu::InstanceDescriptor {
            backends: wgpu::Backends::all(),
            dx12_shader_compiler: Default::default(),
        });

        let surface = unsafe { instance.create_surface(window) }?;

        let adapter = instance
            .request_adapter(&wgpu::RequestAdapterOptions {
                power_preference: wgpu::PowerPreference::HighPerformance,
                compatible_surface: Some(&surface),
                force_fallback_adapter: false,
            })
            .await
            .ok_or("Failed to find suitable GPU adapter")?;

        let (device, queue) = adapter
            .request_device(
                &wgpu::DeviceDescriptor {
                    label: Some("PubCast Renderer Device"),
                    features: wgpu::Features::empty(),
                    limits: if cfg!(target_arch = "wasm32") {
                        wgpu::Limits::downlevel_webgl2_defaults()
                    } else {
                        wgpu::Limits::default()
                    },
                },
                None,
            )
            .await?;

        let surface_caps = surface.get_capabilities(&adapter);
        if surface_caps.formats.is_empty() {
            return Err("Surface reports no supported formats".into());
        }

        let surface_format = surface_caps
            .formats
            .iter()
            .copied()
            .find(|format| format.is_srgb())
            .unwrap_or(surface_caps.formats[0]);

        let present_mode = if surface_caps.present_modes.contains(&wgpu::PresentMode::Fifo) {
            wgpu::PresentMode::Fifo
        } else {
            surface_caps
                .present_modes
                .first()
                .copied()
                .ok_or("Surface reports no supported present modes")?
        };

        let alpha_mode = surface_caps
            .alpha_modes
            .first()
            .copied()
            .ok_or("Surface reports no supported alpha modes")?;

        let config = wgpu::SurfaceConfiguration {
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT,
            format: surface_format,
            width: size.width,
            height: size.height,
            present_mode,
            alpha_mode,
            view_formats: vec![],
        };

        surface.configure(&device, &config);

        let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("PubCast Voxel Shader"),
            source: wgpu::ShaderSource::Wgsl(include_str!("shaders/voxel.wgsl").into()),
        });

        let camera_uniform_buffer = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("Camera Uniform Buffer"),
            size: std::mem::size_of::<CameraUniform>() as u64,
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });

        let camera_bind_group_layout =
            device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
                label: Some("Camera Bind Group Layout"),
                entries: &[wgpu::BindGroupLayoutEntry {
                    binding: 0,
                    visibility: wgpu::ShaderStages::VERTEX,
                    ty: wgpu::BindingType::Buffer {
                        ty: wgpu::BufferBindingType::Uniform,
                        has_dynamic_offset: false,
                        min_binding_size: None,
                    },
                    count: None,
                }],
            });

        let camera_bind_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("Camera Bind Group"),
            layout: &camera_bind_group_layout,
            entries: &[wgpu::BindGroupEntry {
                binding: 0,
                resource: camera_uniform_buffer.as_entire_binding(),
            }],
        });

        let render_pipeline_layout =
            device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
                label: Some("Render Pipeline Layout"),
                bind_group_layouts: &[&camera_bind_group_layout],
                push_constant_ranges: &[],
            });

        let render_pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("PubCast Voxel Render Pipeline"),
            layout: Some(&render_pipeline_layout),
            vertex: wgpu::VertexState {
                module: &shader,
                entry_point: "vs_main",
                buffers: &[Vertex::desc()],
            },
            fragment: Some(wgpu::FragmentState {
                module: &shader,
                entry_point: "fs_main",
                targets: &[Some(wgpu::ColorTargetState {
                    format: config.format,
                    blend: Some(wgpu::BlendState::REPLACE),
                    write_mask: wgpu::ColorWrites::ALL,
                })],
            }),
            primitive: wgpu::PrimitiveState {
                topology: wgpu::PrimitiveTopology::TriangleList,
                strip_index_format: None,
                front_face: wgpu::FrontFace::Ccw,
                cull_mode: Some(wgpu::Face::Back),
                polygon_mode: wgpu::PolygonMode::Fill,
                unclipped_depth: false,
                conservative: false,
            },
            depth_stencil: None,
            multisample: wgpu::MultisampleState {
                count: 1,
                mask: !0,
                alpha_to_coverage_enabled: false,
            },
            multiview: None,
        });

        let mesh_pool = Arc::new(Mutex::new(MeshPool::new(512)));

        let fallback_vertices = create_cube_vertices();
        let fallback_indices = create_cube_indices();
        let fallback_vertex_buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("Fallback Cube Vertex Buffer"),
            contents: bytemuck::cast_slice(&fallback_vertices),
            usage: wgpu::BufferUsages::VERTEX,
        });
        let fallback_index_buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("Fallback Cube Index Buffer"),
            contents: bytemuck::cast_slice(&fallback_indices),
            usage: wgpu::BufferUsages::INDEX,
        });
        let fallback_num_indices = fallback_indices.len() as u32;

        tracing::info!(
            "PubCast renderer initialized: {}x{} @ {:.2} FPS target",
            size.width,
            size.height,
            1000.0 / frame_budget.target_frame_ms
        );

        Ok(Self {
            surface,
            device,
            queue,
            config,
            size,
            render_pipeline,
            camera_uniform_buffer,
            camera_bind_group,
            mesh_pool,
            frame_budget,
            fallback_vertex_buffer,
            fallback_index_buffer,
            fallback_num_indices,
        })
    }

    pub fn size(&self) -> winit::dpi::PhysicalSize<u32> {
        self.size
    }

    pub fn resize(&mut self, new_size: winit::dpi::PhysicalSize<u32>) {
        if new_size.width == 0 || new_size.height == 0 {
            return;
        }

        self.size = new_size;
        self.config.width = new_size.width;
        self.config.height = new_size.height;
        self.surface.configure(&self.device, &self.config);

        tracing::info!("Renderer resized to {}x{}", new_size.width, new_size.height);
    }

    pub fn render(
        &mut self,
        camera: &Camera,
        meshes_to_render: &[MeshHandle],
    ) -> Result<RenderStats, wgpu::SurfaceError> {
        let output = self.surface.get_current_texture()?;
        let view = output
            .texture
            .create_view(&wgpu::TextureViewDescriptor::default());

        let camera_uniform = CameraUniform::from_camera(camera);
        self.queue.write_buffer(
            &self.camera_uniform_buffer,
            0,
            bytemuck::cast_slice(&[camera_uniform]),
        );

        let mut encoder = self
            .device
            .create_command_encoder(&wgpu::CommandEncoderDescriptor {
                label: Some("Render Encoder"),
            });

        let mut stats = RenderStats::default();

        {
            let mut render_pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("Render Pass"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: &view,
                    resolve_target: None,
                    ops: wgpu::Operations {
                        load: wgpu::LoadOp::Clear(wgpu::Color {
                            r: 0.01,
                            g: 0.01,
                            b: 0.02,
                            a: 1.0,
                        }),
                        store: wgpu::StoreOp::Store,
                    },
                })],
                depth_stencil_attachment: None,
            });

            render_pass.set_pipeline(&self.render_pipeline);
            render_pass.set_bind_group(0, &self.camera_bind_group, &[]);

            let mut rendered_any = false;
            {
                let mut pool = self.mesh_pool.lock().map_err(|_| wgpu::SurfaceError::Timeout)?;
                for handle in meshes_to_render {
                    if let Some(mesh) = pool.get_mesh(*handle) {
                        render_pass.set_vertex_buffer(0, mesh.vertex_buffer.slice(..));
                        render_pass.set_index_buffer(mesh.index_buffer.slice(..), wgpu::IndexFormat::Uint16);
                        render_pass.draw_indexed(0..mesh.num_indices, 0, 0..1);
                        stats.draw_calls += 1;
                        stats.triangle_count += mesh.num_indices / 3;
                        rendered_any = true;
                    }
                }
            }

            if !rendered_any {
                render_pass.set_vertex_buffer(0, self.fallback_vertex_buffer.slice(..));
                render_pass.set_index_buffer(self.fallback_index_buffer.slice(..), wgpu::IndexFormat::Uint16);
                render_pass.draw_indexed(0..self.fallback_num_indices, 0, 0..1);
                stats.draw_calls = 1;
                stats.triangle_count = self.fallback_num_indices / 3;
                stats.used_fallback_cube = true;
            }
        }

        self.queue.submit(std::iter::once(encoder.finish()));
        output.present();
        Ok(stats)
    }

    pub fn get_mesh_pool(&self) -> Arc<Mutex<MeshPool>> {
        Arc::clone(&self.mesh_pool)
    }

    pub fn frame_budget(&self) -> &FrameBudget {
        &self.frame_budget
    }
}

// ============================================================================
// Vertex Layout
// ============================================================================

#[repr(C)]
#[derive(Copy, Clone, Debug, bytemuck::Pod, bytemuck::Zeroable)]
pub struct Vertex {
    pub position: [f32; 3],
    pub color: [f32; 3],
}

impl Vertex {
    const ATTRIBS: [wgpu::VertexAttribute; 2] =
        wgpu::vertex_attr_array![0 => Float32x3, 1 => Float32x3];

    pub fn desc() -> wgpu::VertexBufferLayout<'static> {
        wgpu::VertexBufferLayout {
            array_stride: std::mem::size_of::<Vertex>() as wgpu::BufferAddress,
            step_mode: wgpu::VertexStepMode::Vertex,
            attributes: &Self::ATTRIBS,
        }
    }
}

// ============================================================================
// Camera Uniforms
// ============================================================================

#[repr(C)]
#[derive(Debug, Copy, Clone, bytemuck::Pod, bytemuck::Zeroable)]
pub struct CameraUniform {
    pub view_proj: [[f32; 4]; 4],
}

impl CameraUniform {
    pub fn new() -> Self {
        Self {
            view_proj: cgmath::Matrix4::identity().into(),
        }
    }

    pub fn from_camera(camera: &Camera) -> Self {
        Self {
            view_proj: camera.build_view_projection_matrix().into(),
        }
    }
}

// ============================================================================
// Main PubCastRenderer (v5.5 Integration Point)
// ============================================================================

pub struct PubCastRenderer<'w> {
    render_state: Option<RenderState<'w>>,
    metrics: MetricsCollector,
    last_frame_start: Instant,
    pending_meshes: Vec<MeshHandle>,
}

impl<'w> PubCastRenderer<'w> {
    pub fn new() -> Result<Self, Box<dyn Error>> {
        Ok(Self {
            render_state: None,
            metrics: MetricsCollector::new(120),
            last_frame_start: Instant::now(),
            pending_meshes: Vec::new(),
        })
    }

    pub async fn initialize(&mut self) -> Result<(), Box<dyn Error>> {
        Err("Headless initialization is not implemented in this file. Use initialize_with_window(window, frame_budget).".into())
    }

    pub async fn initialize_with_window(
        &mut self,
        window: &'w Window,
        frame_budget: FrameBudget,
    ) -> Result<(), Box<dyn Error>> {
        self.render_state = Some(RenderState::new(window, frame_budget).await?);
        self.last_frame_start = Instant::now();
        tracing::info!("PubCast renderer initialized with window-backed surface (built-on-Claude branch)");
        Ok(())
    }

    pub fn is_initialized(&self) -> bool {
        self.render_state.is_some()
    }

    pub fn resize(&mut self, new_size: winit::dpi::PhysicalSize<u32>) {
        if let Some(render_state) = self.render_state.as_mut() {
            render_state.resize(new_size);
        }
    }

    pub fn queue_mesh(&mut self, handle: MeshHandle) {
        if !self.pending_meshes.contains(&handle) {
            self.pending_meshes.push(handle);
        }
    }

    pub fn clear_queued_meshes(&mut self) {
        self.pending_meshes.clear();
    }

    pub async fn render_frame(
        &mut self,
        camera_manager: &CameraManager,
        _scene_manager: &SceneManager,
    ) -> Result<RenderFrameResult, Box<dyn Error>> {
        let render_state = self
            .render_state
            .as_mut()
            .ok_or("Renderer not initialized. Call initialize_with_window(window, frame_budget) first.")?;

        let camera = camera_manager
            .get_active_camera()
            .ok_or("No active camera available for rendering")?;

        let frame_start = Instant::now();
        let frame_budget = *render_state.frame_budget();
        let delta_since_last_ms = frame_start.duration_since(self.last_frame_start).as_secs_f64() * 1000.0;

        // Only skip obviously too-early frames. This avoids burning GPU on accidental double-pumps,
        // but does not fake pacing with sleeps.
        if delta_since_last_ms > 0.0 && delta_since_last_ms < frame_budget.target_frame_ms * 0.25 {
            self.metrics.record_frame(delta_since_last_ms);
            self.metrics.update_live_fields(0, 0, 0, 100, true);
            self.last_frame_start = frame_start;
            return Ok(RenderFrameResult {
                frame_time_ms: delta_since_last_ms,
                skipped: true,
                quality_level: 100,
                draw_calls: 0,
                triangle_count: 0,
                used_fallback_cube: false,
                error: None,
            });
        }

        let mesh_count = render_state
            .get_mesh_pool()
            .lock()
            .map(|p| p.mesh_count() as u32)
            .unwrap_or(0);

        let render_result = render_state.render(camera, &self.pending_meshes);
        self.pending_meshes.clear();

        let elapsed_ms = frame_start.elapsed().as_secs_f64() * 1000.0;
        self.metrics.record_frame(elapsed_ms);
        self.last_frame_start = frame_start;

        match render_result {
            Ok(stats) => {
                let quality_level = if elapsed_ms >= frame_budget.max_frame_ms {
                    50
                } else if elapsed_ms > frame_budget.quality_reduction_threshold {
                    75
                } else {
                    100
                };

                self.metrics.update_live_fields(
                    stats.draw_calls,
                    stats.triangle_count,
                    mesh_count,
                    quality_level,
                    false,
                );

                Ok(RenderFrameResult {
                    frame_time_ms: elapsed_ms,
                    skipped: false,
                    quality_level,
                    draw_calls: stats.draw_calls,
                    triangle_count: stats.triangle_count,
                    used_fallback_cube: stats.used_fallback_cube,
                    error: None,
                })
            }
            Err(wgpu::SurfaceError::Lost | wgpu::SurfaceError::Outdated) => {
                let current_size = render_state.size();
                render_state.resize(current_size);
                tracing::warn!("Surface lost/outdated; recovering");
                self.metrics.update_live_fields(0, 0, mesh_count, 100, true);
                Ok(RenderFrameResult {
                    frame_time_ms: elapsed_ms,
                    skipped: true,
                    quality_level: 100,
                    draw_calls: 0,
                    triangle_count: 0,
                    used_fallback_cube: false,
                    error: None,
                })
            }
            Err(wgpu::SurfaceError::OutOfMemory) => {
                Err("GPU out of memory; reduce voxel budget or scene complexity".into())
            }
            Err(wgpu::SurfaceError::Timeout) => {
                tracing::warn!("GPU timeout; frame skipped");
                self.metrics.update_live_fields(0, 0, mesh_count, 100, true);
                Ok(RenderFrameResult {
                    frame_time_ms: elapsed_ms,
                    skipped: true,
                    quality_level: 100,
                    draw_calls: 0,
                    triangle_count: 0,
                    used_fallback_cube: false,
                    error: Some("GPU timeout".to_string()),
                })
            }
        }
    }

    pub fn get_performance_metrics(&self) -> PerformanceMetrics {
        self.metrics.get_metrics()
    }

    pub fn get_mesh_pool(&self) -> Option<Arc<Mutex<MeshPool>>> {
        self.render_state.as_ref().map(|rs| rs.get_mesh_pool())
    }
}

#[derive(Debug, Clone, Default)]
pub struct RenderFrameResult {
    pub frame_time_ms: f64,
    pub skipped: bool,
    pub quality_level: u8,
    pub draw_calls: u32,
    pub triangle_count: u32,
    pub used_fallback_cube: bool,
    pub error: Option<String>,
}

fn create_cube_vertices() -> Vec<Vertex> {
    vec![
        Vertex { position: [-0.5, -0.5,  0.5], color: [1.0, 0.0, 0.0] },
        Vertex { position: [ 0.5, -0.5,  0.5], color: [0.0, 1.0, 0.0] },
        Vertex { position: [ 0.5,  0.5,  0.5], color: [0.0, 0.0, 1.0] },
        Vertex { position: [-0.5,  0.5,  0.5], color: [1.0, 1.0, 0.0] },
        Vertex { position: [-0.5, -0.5, -0.5], color: [1.0, 0.0, 1.0] },
        Vertex { position: [ 0.5, -0.5, -0.5], color: [0.0, 1.0, 1.0] },
        Vertex { position: [ 0.5,  0.5, -0.5], color: [1.0, 1.0, 1.0] },
        Vertex { position: [-0.5,  0.5, -0.5], color: [0.5, 0.5, 0.5] },
    ]
}

fn create_cube_indices() -> Vec<u16> {
    vec![
        0, 1, 2,  2, 3, 0,
        4, 6, 5,  6, 4, 7,
        4, 0, 3,  3, 7, 4,
        1, 5, 6,  6, 2, 1,
        3, 2, 6,  6, 7, 3,
        4, 5, 1,  1, 0, 4,
    ]
}
