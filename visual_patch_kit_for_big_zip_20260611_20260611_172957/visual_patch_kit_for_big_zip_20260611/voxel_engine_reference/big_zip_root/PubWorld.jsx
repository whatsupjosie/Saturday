import { useEffect, useRef, useState } from "react";
import * as THREE from "three";

// ─── Orbit helper ─────────────────────────────────────────────────────────────
function applyOrbit(cam, o) {
  cam.position.x = o.tx + o.r * Math.sin(o.phi) * Math.sin(o.theta);
  cam.position.y = o.ty + o.r * Math.cos(o.phi);
  cam.position.z = o.tz + o.r * Math.sin(o.phi) * Math.cos(o.theta);
  cam.lookAt(o.tx, o.ty, o.tz);
}

// ─── Snap to voxel grid ───────────────────────────────────────────────────────
function snap(v) { return Math.floor(v) + 0.5; }

// ─── Geometry merge ───────────────────────────────────────────────────────────
function mergeGeos(geos) {
  let tv = 0, ti = 0;
  geos.forEach(g => {
    tv += g.getAttribute("position").count;
    ti += g.index ? g.index.count : g.getAttribute("position").count;
  });
  const pos = new Float32Array(tv * 3);
  const nrm = new Float32Array(tv * 3);
  const idx = new Uint32Array(ti);
  let vo = 0, io = 0;
  geos.forEach(g => {
    const p = g.getAttribute("position"), n = g.getAttribute("normal");
    pos.set(p.array, vo * 3);
    if (n) nrm.set(n.array, vo * 3);
    if (g.index) {
      for (let i = 0; i < g.index.count; i++) idx[io + i] = g.index.array[i] + vo;
      io += g.index.count;
    }
    vo += p.count;
  });
  const merged = new THREE.BufferGeometry();
  merged.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  merged.setAttribute("normal", new THREE.BufferAttribute(nrm, 3));
  merged.setIndex(new THREE.BufferAttribute(idx, 1));
  merged.computeVertexNormals();
  return merged;
}

// ─── Virtual Camera prop ──────────────────────────────────────────────────────
function mkVirtualCamera() {
  const g = new THREE.Group();
  const dark = new THREE.MeshStandardMaterial({ color: 0x1c1c1c, metalness: 0.85, roughness: 0.15 });
  const body = new THREE.Mesh(new THREE.BoxGeometry(1.0, 0.7, 1.5), dark);
  body.castShadow = true; g.add(body);
  const barrel = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.35, 0.8, 16), dark);
  barrel.rotation.x = Math.PI / 2; barrel.position.z = -1.15; barrel.castShadow = true; g.add(barrel);
  const glass = new THREE.Mesh(
    new THREE.CircleGeometry(0.24, 16),
    new THREE.MeshStandardMaterial({ color: 0x2244ff, metalness: 0.2, roughness: 0, transparent: true, opacity: 0.85 })
  );
  glass.position.z = -1.56; g.add(glass);
  const vf = new THREE.Mesh(new THREE.BoxGeometry(0.32, 0.2, 0.3), dark);
  vf.position.set(0, 0.46, 0.18); g.add(vf);
  const handle = new THREE.Mesh(new THREE.BoxGeometry(0.18, 0.6, 0.22), dark);
  handle.position.set(0.46, -0.35, 0.1); g.add(handle);
  const recGeo = new THREE.SphereGeometry(0.06, 8, 8);
  const recMat = new THREE.MeshStandardMaterial({ color: 0xff1100, emissive: 0xff1100, emissiveIntensity: 2.5 });
  const rec = new THREE.Mesh(recGeo, recMat);
  rec.position.set(0.36, 0.4, -0.4); g.add(rec);
  g.userData.rec = rec;
  // Frustum lines (viewing cone)
  const frustumMat = new THREE.LineBasicMaterial({ color: 0x4400cc, transparent: true, opacity: 0.5 });
  const pts = [
    new THREE.Vector3(0, 0, -0.8), new THREE.Vector3(1.5, 1.0, -4),
    new THREE.Vector3(0, 0, -0.8), new THREE.Vector3(-1.5, 1.0, -4),
    new THREE.Vector3(0, 0, -0.8), new THREE.Vector3(1.5, -1.0, -4),
    new THREE.Vector3(0, 0, -0.8), new THREE.Vector3(-1.5, -1.0, -4),
  ];
  const fGeo = new THREE.BufferGeometry().setFromPoints(pts);
  g.add(new THREE.LineSegments(fGeo, frustumMat));
  return g;
}

// ─── Actor / Performer ────────────────────────────────────────────────────────
function mkActor() {
  const g = new THREE.Group();
  const mat = new THREE.MeshStandardMaterial({ color: 0x2277dd, roughness: 0.55, metalness: 0.1 });
  const mkr = new THREE.MeshStandardMaterial({ color: 0xffffff, emissive: 0xffffff, emissiveIntensity: 3 });
  const parts = [
    [0.5, 0.5, 0.5, 0, 1.75, 0],
    [0.65, 0.82, 0.3, 0, 1.09, 0],
    [0.23, 0.7, 0.23, -0.46, 1.06, 0],
    [0.23, 0.7, 0.23, 0.46, 1.06, 0],
    [0.25, 0.8, 0.25, -0.18, 0.4, 0],
    [0.25, 0.8, 0.25, 0.18, 0.4, 0],
  ];
  parts.forEach(([w, h, d, x, y, z]) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
    m.position.set(x, y, z); m.castShadow = true; g.add(m);
  });
  // Mocap markers
  [[0, 1.75, 0.27], [-0.32, 1.47, 0.16], [0.32, 1.47, 0.16],
   [-0.46, 1.04, 0.13], [0.46, 1.04, 0.13], [0, 1.09, 0.16],
   [-0.18, 0.76, 0.14], [0.18, 0.76, 0.14], [0, 1.75, 0],
   [-0.18, 0.01, 0.14], [0.18, 0.01, 0.14]].forEach(([x, y, z]) => {
    const m = new THREE.Mesh(new THREE.SphereGeometry(0.048, 6, 6), mkr);
    m.position.set(x, y, z); g.add(m);
  });
  return g;
}

// ─── Palette ──────────────────────────────────────────────────────────────────
const SWATCHES = [
  "#ff6a00", "#ffcc00", "#00ff88", "#00ccff", "#aa55ff",
  "#ff2266", "#ffffff", "#888888", "#4444ff", "#ff44aa",
];

export default function PubWorld() {
  const mountRef = useRef(null);
  const engRef   = useRef(null);
  const modeRef  = useRef("build");
  const palRef   = useRef("#ff6a00");
  const yRef     = useRef(0);

  const [mode,       setMode]       = useState("build");
  const [pal,        setPal]        = useState("#ff6a00");
  const [buildY,     setBuildY]     = useState(0);
  const [voxCount,   setVoxCount]   = useState(0);
  const [sets,       setSets]       = useState([]);
  const [status,     setStatus]     = useState("PUB WORLD INITIALIZED · VOID READY · START BUILDING YOUR SET");
  const [recState,   setRecState]   = useState("idle"); // idle | recording | paused

  useEffect(() => { modeRef.current = mode; }, [mode]);
  useEffect(() => { palRef.current  = pal;  }, [pal]);
  useEffect(() => { yRef.current    = buildY; }, [buildY]);

  // ─── Three.js bootstrap ─────────────────────────────────────────────────────
  useEffect(() => {
    const el = mountRef.current;
    let W = el.clientWidth, H = el.clientHeight;

    // Scene
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x03030a);
    scene.fog = new THREE.Fog(0x03030a, 60, 120);

    // Camera
    const cam = new THREE.PerspectiveCamera(55, W / H, 0.1, 400);
    const orbit = { theta: 0.78, phi: 1.08, r: 28, tx: 0, ty: 1.5, tz: 0 };
    applyOrbit(cam, orbit);

    // Renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(W, H);
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    el.appendChild(renderer.domElement);

    // ── Lighting ───────────────────────────────────────────────────────────────
    scene.add(new THREE.AmbientLight(0x0d0d28, 3));

    const key = new THREE.SpotLight(0xffe8c0, 5, 130, Math.PI / 5.5, 0.45, 1.2);
    key.position.set(12, 38, 10); key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048); scene.add(key);

    const fill = new THREE.DirectionalLight(0x1122ff, 0.9);
    fill.position.set(-14, 8, -6); scene.add(fill);

    const rim = new THREE.DirectionalLight(0xff2200, 0.4);
    rim.position.set(3, 4, -22); scene.add(rim);

    // ── Floor ──────────────────────────────────────────────────────────────────
    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(80, 80),
      new THREE.MeshStandardMaterial({ color: 0x060614, roughness: 0.97 })
    );
    floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true; scene.add(floor);

    // Primary grid
    const grid = new THREE.GridHelper(40, 40, 0x120d28, 0x0a0820);
    grid.position.y = 0.002; scene.add(grid);

    // Stage perimeter glow lines
    const perimMat = new THREE.LineBasicMaterial({ color: 0x5500ff });
    const perimGeo = new THREE.EdgesGeometry(new THREE.BoxGeometry(20, 0.02, 20));
    scene.add(new THREE.LineSegments(perimGeo, perimMat));

    // Corner markers
    [[-10,0,-10],[10,0,-10],[-10,0,10],[10,0,10]].forEach(([x,,z]) => {
      const m = new THREE.Mesh(
        new THREE.BoxGeometry(0.15, 0.3, 0.15),
        new THREE.MeshStandardMaterial({ color: 0x8800ff, emissive: 0x4400ff, emissiveIntensity: 1 })
      );
      m.position.set(x, 0.15, z); scene.add(m);
    });

    // ── Ceiling rig ────────────────────────────────────────────────────────────
    const ceilGrid = new THREE.GridHelper(26, 5, 0x1a0040, 0x0d0020);
    ceilGrid.position.y = 16; scene.add(ceilGrid);
    const ceilMat = new THREE.LineBasicMaterial({ color: 0x220044 });
    const truss = new THREE.EdgesGeometry(new THREE.BoxGeometry(24, 0.3, 24));
    scene.add(new THREE.LineSegments(truss, ceilMat));
    (new THREE.LineSegments(truss, ceilMat)).position.y = 16;
    scene.add(Object.assign(new THREE.LineSegments(truss, ceilMat), { position: new THREE.Vector3(0, 16, 0) }));

    // Studio overhead spots
    [[-7,15.6,-7],[7,15.6,-7],[-7,15.6,7],[7,15.6,7],[0,15.6,0]].forEach(([x,y,z], i) => {
      const fix = new THREE.Mesh(
        new THREE.CylinderGeometry(0.18, 0.28, 0.55, 8),
        new THREE.MeshStandardMaterial({ color: 0x111111, metalness: 0.8 })
      );
      fix.position.set(x, y, z); scene.add(fix);
      const sl = new THREE.SpotLight(i === 4 ? 0xffffff : 0xfff0cc, 1.2, 22, Math.PI / 7, 0.6);
      sl.position.set(x, y - 0.3, z); scene.add(sl);
    });

    // ── Raycast grid planes (one per build height) ─────────────────────────────
    const castPlanes = [];
    for (let ly = 0; ly <= 25; ly++) {
      const p = new THREE.Mesh(
        new THREE.PlaneGeometry(40, 40),
        new THREE.MeshBasicMaterial({ visible: false, side: THREE.DoubleSide })
      );
      p.rotation.x = -Math.PI / 2;
      p.position.y = ly + 0.5;
      p.userData.ly = ly;
      scene.add(p); castPlanes.push(p);
    }

    // ── Scene objects ──────────────────────────────────────────────────────────
    const vcam = mkVirtualCamera();
    vcam.position.set(7, 3.5, 7);
    vcam.rotation.y = -Math.PI * 0.73;
    scene.add(vcam);

    const actor = mkActor();
    scene.add(actor);

    // Ghost voxel (preview)
    const ghost = new THREE.Mesh(
      new THREE.BoxGeometry(1, 1, 1),
      new THREE.MeshStandardMaterial({ color: 0xffffff, transparent: true, opacity: 0.22, depthWrite: false })
    );
    ghost.visible = false; scene.add(ghost);

    // State
    const voxMap = new Map(); // key->"x,y,z" : mesh
    const bakedList = [];
    const rc = new THREE.Raycaster();

    engRef.current = { scene, cam, renderer, orbit, rc, voxMap, bakedList, vcam, actor, ghost, castPlanes };

    // ── Helpers ────────────────────────────────────────────────────────────────
    const vKey = (x, y, z) => `${Math.round(x * 2)},${Math.round(y * 2)},${Math.round(z * 2)}`;

    function getNDC(e) {
      const r = el.getBoundingClientRect();
      return new THREE.Vector2(
        ((e.clientX - r.left) / W) * 2 - 1,
        -((e.clientY - r.top)  / H) * 2 + 1
      );
    }

    function castForPlacement(e) {
      rc.setFromCamera(getNDC(e), cam);
      // 1. Existing voxels
      const vHits = rc.intersectObjects([...voxMap.values()]);
      if (vHits.length) {
        const h = vHits[0];
        const n = h.face.normal.clone();
        const p = h.object.position.clone().add(n);
        return { pos: [p.x, p.y, p.z], hitVox: h.object };
      }
      // 2. Baked meshes
      const bHits = rc.intersectObjects(bakedList);
      if (bHits.length) {
        const h = bHits[0];
        const n = h.face.normal.clone().multiplyScalar(0.5);
        const pt = h.point.clone().add(n);
        return { pos: [snap(pt.x), snap(pt.y), snap(pt.z)] };
      }
      // 3. Current height plane
      const plane = castPlanes[yRef.current] || castPlanes[0];
      const pHits = rc.intersectObject(plane);
      if (pHits.length) {
        const pt = pHits[0].point;
        return { pos: [snap(pt.x), yRef.current + 0.5, snap(pt.z)] };
      }
      return null;
    }

    function placeVoxel(x, y, z) {
      const k = vKey(x, y, z);
      if (voxMap.has(k)) return;
      const hex = parseInt(palRef.current.slice(1), 16);
      const mesh = new THREE.Mesh(
        new THREE.BoxGeometry(0.96, 0.96, 0.96),
        new THREE.MeshStandardMaterial({ color: hex, roughness: 0.65, metalness: 0.06 })
      );
      mesh.position.set(x, y, z);
      mesh.castShadow = mesh.receiveShadow = true;
      scene.add(mesh); voxMap.set(k, mesh);
      setVoxCount(voxMap.size);
      setStatus(`VOXEL PLACED · (${Math.round(x)}, ${Math.round(y - 0.5)}, ${Math.round(z)}) · TOTAL ${voxMap.size}`);
    }

    function removeVoxelAt(e) {
      rc.setFromCamera(getNDC(e), cam);
      const hits = rc.intersectObjects([...voxMap.values()]);
      if (!hits.length) return;
      const mesh = hits[0].object;
      const p = mesh.position;
      const k = vKey(p.x, p.y, p.z);
      scene.remove(mesh); mesh.geometry.dispose(); mesh.material.dispose();
      voxMap.delete(k);
      setVoxCount(voxMap.size);
      setStatus(`VOXEL REMOVED · TOTAL ${voxMap.size}`);
    }

    // ── Pointer handling ───────────────────────────────────────────────────────
    let dn = false, btn = 0, sx = 0, sy = 0, moved = false;

    const onDown = e => {
      dn = true; btn = e.button; sx = e.clientX; sy = e.clientY; moved = false;
      el.setPointerCapture(e.pointerId);
    };

    const onMove = e => {
      // Ghost preview (only when hovering, not dragging)
      if (!dn && modeRef.current === "build") {
        const r = castForPlacement(e);
        if (r) {
          ghost.visible = true;
          ghost.material.color.setHex(parseInt(palRef.current.slice(1), 16));
          ghost.position.set(...r.pos);
        } else { ghost.visible = false; }
      }
      if (!dn) return;

      const dx = e.clientX - sx, dy = e.clientY - sy;
      if (Math.abs(dx) > 2 || Math.abs(dy) > 2) moved = true;

      if (moved) {
        if (btn === 0) {
          orbit.theta -= dx * 0.0038;
          orbit.phi = Math.max(0.06, Math.min(1.56, orbit.phi + dy * 0.0038));
        } else if (btn === 2) {
          const speed = orbit.r * 0.0014;
          const right = new THREE.Vector3();
          right.crossVectors(cam.getWorldDirection(new THREE.Vector3()), new THREE.Vector3(0, 1, 0)).normalize();
          orbit.tx -= right.x * dx * speed;
          orbit.tz -= right.z * dx * speed;
          orbit.ty += dy * speed * 0.6;
        }
        applyOrbit(cam, orbit);
        sx = e.clientX; sy = e.clientY;
      }
    };

    const onUp = e => {
      if (!moved && modeRef.current === "build") {
        if (e.button === 0) {
          const r = castForPlacement(e);
          if (r) placeVoxel(...r.pos);
        } else if (e.button === 2) {
          removeVoxelAt(e);
        }
      }
      dn = false;
    };

    const onWheel = e => {
      orbit.r = Math.max(3, Math.min(90, orbit.r + e.deltaY * 0.055));
      applyOrbit(cam, orbit);
    };

    const onLeave = () => { ghost.visible = false; };

    el.addEventListener("pointerdown",  onDown);
    el.addEventListener("pointermove",  onMove);
    el.addEventListener("pointerup",    onUp);
    el.addEventListener("wheel",        onWheel, { passive: true });
    el.addEventListener("pointerleave", onLeave);
    el.addEventListener("contextmenu",  e => e.preventDefault());

    // ── Animate ────────────────────────────────────────────────────────────────
    let raf;
    const clock = new THREE.Clock();
    const animate = () => {
      raf = requestAnimationFrame(animate);
      const t = clock.getElapsedTime();
      // Breathing actor
      actor.position.y = Math.sin(t * 1.15) * 0.018;
      // REC indicator pulse
      if (vcam.userData.rec) {
        vcam.userData.rec.material.emissiveIntensity = 1.5 + Math.sin(t * 4.5) * 1.2;
      }
      // Subtle ghost pulse
      if (ghost.visible) {
        ghost.material.opacity = 0.15 + Math.sin(t * 6) * 0.08;
      }
      renderer.render(scene, cam);
    };
    animate();

    const onResize = () => {
      W = el.clientWidth; H = el.clientHeight;
      cam.aspect = W / H; cam.updateProjectionMatrix();
      renderer.setSize(W, H);
    };
    window.addEventListener("resize", onResize);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", onResize);
      el.removeEventListener("pointerdown",  onDown);
      el.removeEventListener("pointermove",  onMove);
      el.removeEventListener("pointerup",    onUp);
      el.removeEventListener("wheel",        onWheel);
      el.removeEventListener("pointerleave", onLeave);
      if (el.contains(renderer.domElement)) el.removeChild(renderer.domElement);
      renderer.dispose();
    };
  }, []);

  // ─── Bake voxels to mesh ─────────────────────────────────────────────────────
  const handleBake = () => {
    const eng = engRef.current;
    if (!eng || eng.voxMap.size === 0) { setStatus("NOTHING TO BAKE"); return; }

    const colorGroups = new Map();
    eng.voxMap.forEach(mesh => {
      const hex = mesh.material.color.getHex();
      if (!colorGroups.has(hex)) colorGroups.set(hex, []);
      mesh.updateWorldMatrix(true, false);
      const g = mesh.geometry.clone();
      g.applyMatrix4(mesh.matrixWorld);
      colorGroups.get(hex).push(g);
      eng.scene.remove(mesh);
      mesh.geometry.dispose(); mesh.material.dispose();
    });

    const total = eng.voxMap.size;
    eng.voxMap.clear(); setVoxCount(0);

    const setName = `SET ${eng.bakedList.length + 1}`;
    colorGroups.forEach((geos, hex) => {
      const merged = mergeGeos(geos);
      const mat = new THREE.MeshStandardMaterial({ color: hex, roughness: 0.62, metalness: 0.08 });
      const mesh = new THREE.Mesh(merged, mat);
      mesh.castShadow = mesh.receiveShadow = true;
      mesh.userData.setName = setName;
      eng.scene.add(mesh);
      eng.bakedList.push(mesh);
    });

    const entry = { name: setName, voxels: total, meshes: colorGroups.size, colors: [...colorGroups.keys()] };
    setSets(prev => [...prev, entry]);
    setStatus(`✓ BAKED "${setName}" · ${total} VOXELS → ${colorGroups.size} MESH OBJECT(S) · SET IS NOW A SOLID PROP`);
  };

  // ─── Clear all ───────────────────────────────────────────────────────────────
  const handleClear = () => {
    const eng = engRef.current; if (!eng) return;
    eng.voxMap.forEach(m => { eng.scene.remove(m); m.geometry.dispose(); m.material.dispose(); });
    eng.voxMap.clear(); setVoxCount(0);
    eng.bakedList.forEach(m => { eng.scene.remove(m); m.geometry.dispose(); m.material.dispose(); });
    eng.bakedList.length = 0; setSets([]);
    setStatus("VOID CLEARED · FRESH START");
  };

  // ─── UI ──────────────────────────────────────────────────────────────────────
  const MODES = [
    { id: "build",   icon: "⬛", label: "BUILD"   },
    { id: "camera",  icon: "◎",  label: "CAMERA"  },
    { id: "capture", icon: "⚡", label: "CAPTURE" },
    { id: "review",  icon: "▶",  label: "REVIEW"  },
  ];

  const S = {
    root:   { width:"100%", height:"100vh", background:"#02020a", display:"flex", flexDirection:"column", fontFamily:'"Share Tech Mono", "Courier New", monospace', color:"#fff", overflow:"hidden", userSelect:"none" },
    topbar: { display:"flex", alignItems:"center", height:44, background:"linear-gradient(90deg,#08001a,#04000e)", borderBottom:"1px solid #2200aa", padding:"0 14px", gap:14, flexShrink:0 },
    modeBtn:(active) => ({ background: active ? "#18006a" : "transparent", border: active ? "1px solid #6600ff" : "1px solid #1a0040", color: active ? "#cc88ff" : "#44226a", padding:"4px 16px", cursor:"pointer", fontSize:10, letterSpacing:2, fontFamily:"inherit", transition:"all .15s" }),
    sidebar: { width:168, background:"#030212", borderRight:"1px solid #120030", padding:"12px 10px", display:"flex", flexDirection:"column", gap:10, flexShrink:0, overflowY:"auto" },
    rPanel: { width:162, background:"#030212", borderLeft:"1px solid #120030", padding:"12px 10px", display:"flex", flexDirection:"column", gap:8, flexShrink:0, overflowY:"auto" },
    label:  { fontSize:9, color:"#440077", letterSpacing:2 },
    divider:{ borderBottom:"1px solid #0f0025", paddingBottom:6, marginBottom:2 },
    btn:    (c="#aa55ff") => ({ background:"#0a0022", border:`1px solid #250050`, color:c, padding:"6px 8px", cursor:"pointer", fontSize:9, letterSpacing:1, fontFamily:"inherit", width:"100%", textAlign:"left" }),
    status: { height:26, background:"#020109", borderTop:"1px solid #0d001e", display:"flex", alignItems:"center", padding:"0 14px", flexShrink:0 },
    swatch: (c, sel) => ({ width:22, height:22, background:c, border: sel ? "2px solid #fff" : "2px solid transparent", cursor:"pointer", borderRadius:2, flexShrink:0 }),
    sceneRow: { display:"flex", alignItems:"center", gap:6, padding:"5px 7px", background:"#07001a", border:"1px solid #140030", marginBottom:2 },
  };

  return (
    <div style={S.root}>
      {/* ── Top bar ── */}
      <div style={S.topbar}>
        <div style={{ display:"flex", alignItems:"center", gap:8 }}>
          <div style={{ width:9, height:9, borderRadius:"50%", background:"#ff2200", boxShadow:"0 0 8px #ff2200", flexShrink:0 }} />
          <span style={{ color:"#cc44ff", fontWeight:"bold", fontSize:14, letterSpacing:4 }}>PUB WORLD</span>
          <span style={{ color:"#2a0055", fontSize:11 }}>|</span>
          <span style={{ color:"#550099", fontSize:9, letterSpacing:3 }}>PUBCAST AI</span>
        </div>
        <div style={{ flex:1, display:"flex", gap:3, justifyContent:"center" }}>
          {MODES.map(m => (
            <button key={m.id} onClick={() => setMode(m.id)} style={S.modeBtn(mode === m.id)}>
              {m.icon} {m.label}
            </button>
          ))}
        </div>
        <div style={{ display:"flex", gap:6, alignItems:"center" }}>
          <div style={{ width:7, height:7, borderRadius:"50%", background: recState === "recording" ? "#ff0000" : "#220033", boxShadow: recState === "recording" ? "0 0 6px #ff0000" : "none" }} />
          <span style={{ fontSize:9, color:"#330055", letterSpacing:2 }}>3D STUDIO</span>
        </div>
      </div>

      {/* ── Main ── */}
      <div style={{ flex:1, display:"flex", overflow:"hidden" }}>

        {/* Left panel */}
        <div style={S.sidebar}>
          <div style={{ ...S.label, ...S.divider }}>TOOLS</div>

          {mode === "build" && <>
            <div style={S.label}>VOXEL COLOR</div>
            <div style={{ display:"flex", flexWrap:"wrap", gap:3, marginBottom:2 }}>
              {SWATCHES.map(c => (
                <div key={c} style={S.swatch(c, pal === c)} onClick={() => setPal(c)} />
              ))}
            </div>
            <input type="color" value={pal} onChange={e => setPal(e.target.value)}
              style={{ width:"100%", height:28, border:"1px solid #2200aa", background:"#000", cursor:"pointer", marginBottom:2 }} />

            <div style={S.label}>BUILD HEIGHT  Y={buildY}</div>
            <div style={{ display:"flex", gap:4 }}>
              <button onClick={() => setBuildY(v => Math.max(0, v-1))} style={{ ...S.btn(), flex:1, textAlign:"center" }}>▼</button>
              <div style={{ flex:1, display:"flex", alignItems:"center", justifyContent:"center", fontSize:11, color:"#8844cc" }}>{buildY}</div>
              <button onClick={() => setBuildY(v => Math.min(25, v+1))} style={{ ...S.btn(), flex:1, textAlign:"center" }}>▲</button>
            </div>

            <div style={{ ...S.label, marginTop:4 }}>VOXELS IN BUFFER: {voxCount}</div>

            <button onClick={handleBake} style={{ ...S.btn("#bb77ff"), padding:"9px 8px", fontSize:10, letterSpacing:2, marginTop:4 }}>
              ⬡ BAKE TO MESH
            </button>
            <button onClick={handleClear} style={{ ...S.btn("#ff3333"), fontSize:9 }}>
              ✕ CLEAR VOID
            </button>

            <div style={{ ...S.label, marginTop:6, lineHeight:1.7 }}>
              LMB: Place voxel<br/>
              RMB: Remove voxel<br/>
              Drag: Orbit camera<br/>
              R-drag: Pan<br/>
              Scroll: Zoom
            </div>
          </>}

          {mode === "camera" && <>
            <div style={{ ...S.label, lineHeight:2.0 }}>
              Virtual camera is live in scene.<br/><br/>
              Orbit: Left drag<br/>
              Pan: Right drag<br/>
              Zoom: Scroll<br/><br/>
              POV mode coming<br/>in next iteration.
            </div>
          </>}

          {mode === "capture" && <>
            <div style={S.label}>CAPTURE</div>
            <div style={{ ...S.label, color:"#553388", lineHeight:1.8, marginBottom:6 }}>
              Motion Capture<br/>
              Performance Capture<br/>
              Object Tracking
            </div>
            {["▶  RECORD","⏸  PAUSE","⏹  STOP","⊕  ADD MARKER"].map((l, i) => (
              <button key={l} onClick={() => {
                if (i === 0) setRecState("recording");
                if (i === 1) setRecState("paused");
                if (i === 2) { setRecState("idle"); setStatus("CAPTURE SESSION ENDED"); }
              }} style={{ ...S.btn(i===0?"#ff4444":i===3?"#44cc88":"#aa55ff"), marginBottom:2 }}>{l}</button>
            ))}
            {recState !== "idle" && (
              <div style={{ display:"flex", gap:5, alignItems:"center", marginTop:6 }}>
                <div style={{ width:7, height:7, borderRadius:"50%", background: recState === "recording" ? "#ff0000":"#ffaa00", boxShadow:`0 0 6px ${recState==="recording"?"#ff0000":"#ffaa00"}` }} />
                <span style={{ fontSize:9, color: recState==="recording"?"#ff5555":"#ffaa44", letterSpacing:1 }}>
                  {recState === "recording" ? "RECORDING" : "PAUSED"}
                </span>
              </div>
            )}
          </>}

          {mode === "review" && <>
            <div style={{ ...S.label, lineHeight:2.0, color:"#553388" }}>
              Review & Playback<br/><br/>
              Reskin pipeline:<br/>
              Capture → Bake → <br/>
              Replace geometry<br/>
              with hi-fidelity<br/>
              model via data.<br/><br/>
              No limit to final<br/>
              output quality.
            </div>
          </>}
        </div>

        {/* 3D Viewport */}
        <div ref={mountRef} style={{ flex:1, position:"relative", overflow:"hidden" }}>
          {/* Overlay hints */}
          <div style={{ position:"absolute", top:10, left:12, pointerEvents:"none" }}>
            <div style={{ fontSize:9, color:"#330066", letterSpacing:2 }}>VOID SPACE · 3D PRODUCTION STUDIO</div>
            {mode === "build" && <div style={{ fontSize:8, color:"#220044", marginTop:3, letterSpacing:1 }}>
              CLICK TO PLACE · RIGHT CLICK TO ERASE · DRAG TO ORBIT
            </div>}
          </div>
          <div style={{ position:"absolute", top:10, right:12, pointerEvents:"none", textAlign:"right" }}>
            <div style={{ fontSize:9, color:"#220044", letterSpacing:1 }}>MODE: {mode.toUpperCase()}</div>
            <div style={{ fontSize:8, color:"#18002a", letterSpacing:1, marginTop:2 }}>Y LAYER: {buildY}</div>
          </div>
          {/* Corner accent lines */}
          {[{t:0,l:0},{t:0,r:0},{b:0,l:0},{b:0,r:0}].map((p, i) => (
            <div key={i} style={{ position:"absolute", ...p, width:20, height:20,
              borderTop: (p.t===0)?"1px solid #2200aa":"none",
              borderBottom: (p.b===0)?"1px solid #2200aa":"none",
              borderLeft: (p.l===0)?"1px solid #2200aa":"none",
              borderRight: (p.r===0)?"1px solid #2200aa":"none",
              pointerEvents:"none" }} />
          ))}
        </div>

        {/* Right panel - Scene outliner */}
        <div style={S.rPanel}>
          <div style={{ ...S.label, ...S.divider }}>SCENE</div>

          <div style={{ ...S.label, fontSize:8, color:"#330055", marginBottom:4 }}>PERMANENT OBJECTS</div>

          <div style={S.sceneRow}>
            <span style={{ fontSize:11 }}>◎</span>
            <div>
              <div style={{ fontSize:8, color:"#8855ff", letterSpacing:1 }}>VIRTUAL CAMERA</div>
              <div style={{ fontSize:7, color:"#330044" }}>REC ACTIVE</div>
            </div>
          </div>
          <div style={S.sceneRow}>
            <span style={{ fontSize:11 }}>▣</span>
            <div>
              <div style={{ fontSize:8, color:"#3388ff", letterSpacing:1 }}>PERFORMER ACTOR</div>
              <div style={{ fontSize:7, color:"#330044" }}>MOCAP MARKERS ×11</div>
            </div>
          </div>

          {sets.length > 0 && <>
            <div style={{ ...S.label, fontSize:8, color:"#330055", marginTop:8, marginBottom:4 }}>BAKED SET OBJECTS</div>
            {sets.map((s, i) => (
              <div key={i} style={{ ...S.sceneRow, flexDirection:"column", alignItems:"flex-start", gap:3 }}>
                <div style={{ display:"flex", gap:5, alignItems:"center" }}>
                  <div style={{ display:"flex", gap:2 }}>
                    {s.colors.map(c => (
                      <div key={c} style={{ width:8, height:8, background:`#${c.toString(16).padStart(6,"0")}`, borderRadius:1 }} />
                    ))}
                  </div>
                  <span style={{ fontSize:8, color:"#cc8800", letterSpacing:1 }}>{s.name}</span>
                </div>
                <div style={{ fontSize:7, color:"#442200" }}>{s.voxels}v → {s.meshes} mesh{s.meshes!==1?"es":""}</div>
              </div>
            ))}
          </>}

          {sets.length === 0 && voxCount === 0 && (
            <div style={{ fontSize:8, color:"#1a0033", lineHeight:1.8, marginTop:8 }}>
              Place voxels to<br/>
              build set pieces.<br/>
              Bake them into<br/>
              solid mesh objects.<br/>
              Piece by piece,<br/>
              build your world.
            </div>
          )}

          {voxCount > 0 && (
            <div style={{ ...S.sceneRow, flexDirection:"column", alignItems:"flex-start", gap:2, marginTop:4, borderColor:"#3a1a00" }}>
              <div style={{ fontSize:8, color:"#ffaa44", letterSpacing:1 }}>VOXEL BUFFER</div>
              <div style={{ fontSize:7, color:"#552200" }}>{voxCount} voxels · unbaked</div>
              <div style={{ width:"100%", height:2, background:"#110500", borderRadius:1, marginTop:2 }}>
                <div style={{ height:"100%", width:`${Math.min(100, (voxCount/200)*100)}%`, background:"#ff6600", borderRadius:1, transition:"width .2s" }} />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Status bar */}
      <div style={S.status}>
        <div style={{ width:6, height:6, borderRadius:"50%", background:"#4400ff", marginRight:8, flexShrink:0 }} />
        <span style={{ fontSize:9, color:"#553377", letterSpacing:1 }}>{status}</span>
      </div>
    </div>
  );
}
