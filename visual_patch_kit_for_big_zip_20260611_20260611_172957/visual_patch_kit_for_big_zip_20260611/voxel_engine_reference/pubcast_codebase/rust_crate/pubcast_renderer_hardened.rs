/*!
Core Rendering Module for PubCast AI

High-performance rendering pipeline using wgpu for cross-platform GPU acceleration.
Handles voxel rendering, skeletal animation, and real-time 3D visualization.

Hardening notes:
- Removed simulated render timing and fake performance counters.
- Added explicit window-backed initialization path.
- Made headless initialization fail honestly instead of pretending to work.
- Tightened frame timing math and surface error recovery.
- Kept the public shape close to the donor file where practical.
*/

use std::error::Error;
use std::time::Instant;

use cgmath::SquareMatrix;
use wgpu::util::DeviceExt;
use winit::window::Window;

use crate::camera::{Camera, CameraManager};
use crate::scene::SceneManager;

#[derive(Debug, Clone, Copy, Default)]
pub struct PerformanceMetrics {
    pub frame_rate: f64,
    pub frame_time_ms: f64,
    pub cpu_usage_percent: f64,
    pub gpu_usage_percent: f64,
    pub memory_usage_mb: f64,
    pub draw_calls: u32,
    pub triangle_count: u32,
}

pub struct RenderState {
    surface: wgpu::Surface,
    device: wgpu::Device,
    queue: wgpu::Queue,
    config: wgpu::SurfaceConfiguration,
    size: winit::dpi::PhysicalSize<u32>,
    render_pipeline: wgpu::RenderPipeline,
    vertex_buffer: wgpu::Buffer,
    index_buffer: wgpu::Buffer,
    num_indices: u32,
    camera_uniform_buffer: wgpu::Buffer,
    camera_bind_group: wgpu::BindGroup,
}

impl RenderState {
    pub async fn new(window: &Window) -> Result<Self, Box<dyn Error>> {
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

        let present_mode = if surface_caps
            .present_modes
            .contains(&wgpu::PresentMode::Fifo)
        {
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
            label: Some("Voxel Shader"),
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
            label: Some("Voxel Render Pipeline"),
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

        let vertices = Self::create_cube_vertices();
        let indices = Self::create_cube_indices();

        let vertex_buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("Vertex Buffer"),
            contents: bytemuck::cast_slice(&vertices),
            usage: wgpu::BufferUsages::VERTEX,
        });

        let index_buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("Index Buffer"),
            contents: bytemuck::cast_slice(&indices),
            usage: wgpu::BufferUsages::INDEX,
        });

        Ok(Self {
            surface,
            device,
            queue,
            config,
            size,
            render_pipeline,
            vertex_buffer,
            index_buffer,
            num_indices: indices.len() as u32,
            camera_uniform_buffer,
            camera_bind_group,
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
    }

    pub fn render(&mut self, camera: &Camera) -> Result<(), wgpu::SurfaceError> {
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

        {
            let mut render_pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("Render Pass"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: &view,
                    resolve_target: None,
                    ops: wgpu::Operations {
                        load: wgpu::LoadOp::Clear(wgpu::Color {
                            r: 0.1,
                            g: 0.2,
                            b: 0.3,
                            a: 1.0,
                        }),
                        store: wgpu::StoreOp::Store,
                    },
                })],
                depth_stencil_attachment: None,
            });

            render_pass.set_pipeline(&self.render_pipeline);
            render_pass.set_bind_group(0, &self.camera_bind_group, &[]);
            render_pass.set_vertex_buffer(0, self.vertex_buffer.slice(..));
            render_pass.set_index_buffer(self.index_buffer.slice(..), wgpu::IndexFormat::Uint16);
            render_pass.draw_indexed(0..self.num_indices, 0, 0..1);
        }

        self.queue.submit(std::iter::once(encoder.finish()));
        output.present();
        Ok(())
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
}

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

pub struct PubCastRenderer {
    render_state: Option<RenderState>,
    performance_metrics: PerformanceMetrics,
    frame_count: u64,
    last_frame_started: Instant,
}

impl PubCastRenderer {
    pub fn new() -> Result<Self, Box<dyn Error>> {
        Ok(Self {
            render_state: None,
            performance_metrics: PerformanceMetrics::default(),
            frame_count: 0,
            last_frame_started: Instant::now(),
        })
    }

    pub async fn initialize(&mut self) -> Result<(), Box<dyn Error>> {
        Err("Headless initialization is not implemented in this file. Use initialize_with_window(window).".into())
    }

    pub async fn initialize_with_window(
        &mut self,
        window: &Window,
    ) -> Result<(), Box<dyn Error>> {
        self.render_state = Some(RenderState::new(window).await?);
        self.last_frame_started = Instant::now();
        tracing::info!("PubCast renderer initialized with a window-backed surface");
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

    pub async fn render_frame(
        &mut self,
        camera_manager: &CameraManager,
        _scene_manager: &SceneManager,
    ) -> Result<(), Box<dyn Error>> {
        let render_state = self
            .render_state
            .as_mut()
            .ok_or("Renderer is not initialized. Call initialize_with_window(window) first.")?;

        let camera = camera_manager
            .get_active_camera()
            .ok_or("No active camera available for rendering")?;

        let frame_started = Instant::now();

        match render_state.render(camera) {
            Ok(()) => {}
            Err(wgpu::SurfaceError::Lost | wgpu::SurfaceError::Outdated) => {
                let current_size = render_state.size();
                render_state.resize(current_size);
                return Ok(());
            }
            Err(wgpu::SurfaceError::OutOfMemory) => {
                return Err("Renderer ran out of GPU memory".into());
            }
            Err(wgpu::SurfaceError::Timeout) => {
                tracing::warn!("Renderer surface timed out this frame");
                return Ok(());
            }
        }

        let frame_time_secs = frame_started.elapsed().as_secs_f64();
        self.performance_metrics.frame_time_ms = frame_time_secs * 1000.0;
        self.performance_metrics.frame_rate = if frame_time_secs > 0.0 {
            1.0 / frame_time_secs
        } else {
            0.0
        };

        // Honest counters only. This donor file currently knows about exactly one draw call
        // and one cube worth of indexed triangles. Leave the rest at zero until real probes
        // are wired in from the wider engine.
        self.performance_metrics.draw_calls = 1;
        self.performance_metrics.triangle_count = self.num_triangles_for_demo_cube();
        self.performance_metrics.cpu_usage_percent = 0.0;
        self.performance_metrics.gpu_usage_percent = 0.0;
        self.performance_metrics.memory_usage_mb = 0.0;

        self.frame_count += 1;
        self.last_frame_started = frame_started;
        Ok(())
    }

    fn num_triangles_for_demo_cube(&self) -> u32 {
        12
    }

    pub async fn get_performance_metrics(&self) -> PerformanceMetrics {
        self.performance_metrics
    }
}
