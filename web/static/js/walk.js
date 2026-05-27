// Interactive 3D "pi random walk": each digit 0-9 picks one of 10 evenly spread
// 3D directions; we step along it, building a glowing poly-line coloured per digit.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { PALETTE } from './scene.js';

// 10 directions spread over a sphere (fibonacci) -> each digit is a heading.
function makeDirections() {
  const dirs = [];
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < 10; i++) {
    const y = 1 - (i / 9) * 2;
    const r = Math.sqrt(1 - y * y);
    const th = i * golden;
    dirs.push(new THREE.Vector3(Math.cos(th) * r, y, Math.sin(th) * r));
  }
  return dirs;
}
const DIRS = makeDirections();
const STEP = 1.0;

export function initWalk(canvas) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(55, 1, 0.1, 100000);
  camera.position.set(0, 0, 120);

  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 0.7;

  let pathObj = null;
  let useLine2 = false;
  let line2mods = null;

  // Try to load fat-line support for thick glowing strokes; fall back to THREE.Line
  async function ensureLine2() {
    if (line2mods !== null) return line2mods;
    try {
      const [{ Line2 }, { LineMaterial }, { LineGeometry }] = await Promise.all([
        import('three/addons/lines/Line2.js'),
        import('three/addons/lines/LineMaterial.js'),
        import('three/addons/lines/LineGeometry.js'),
      ]);
      line2mods = { Line2, LineMaterial, LineGeometry };
      useLine2 = true;
    } catch (e) {
      line2mods = {}; useLine2 = false;
      console.warn('Line2 unavailable, using basic lines', e);
    }
    return line2mods;
  }

  function build(digits) {
    if (pathObj) { scene.remove(pathObj); pathObj.geometry?.dispose?.(); pathObj = null; }

    const n = digits.length;
    const positions = new Float32Array(n * 3);
    const colors = new Float32Array(n * 3);
    const cur = new THREE.Vector3(0, 0, 0);
    const c = new THREE.Color();
    let minX=1e9,minY=1e9,minZ=1e9,maxX=-1e9,maxY=-1e9,maxZ=-1e9;

    for (let i = 0; i < n; i++) {
      const d = digits.charCodeAt(i) - 48;
      if (d >= 0 && d <= 9) cur.addScaledVector(DIRS[d], STEP);
      positions[i*3]=cur.x; positions[i*3+1]=cur.y; positions[i*3+2]=cur.z;
      // colour gradient: blend digit colour with progress along the walk
      c.setHex(PALETTE[d] ?? 0xffffff);
      colors[i*3]=c.r; colors[i*3+1]=c.g; colors[i*3+2]=c.b;
      minX=Math.min(minX,cur.x); maxX=Math.max(maxX,cur.x);
      minY=Math.min(minY,cur.y); maxY=Math.max(maxY,cur.y);
      minZ=Math.min(minZ,cur.z); maxZ=Math.max(maxZ,cur.z);
    }

    if (useLine2 && line2mods.Line2) {
      const { Line2, LineMaterial, LineGeometry } = line2mods;
      const geo = new LineGeometry();
      geo.setPositions(positions);
      geo.setColors(colors);
      const mat = new LineMaterial({
        linewidth: 2.2, vertexColors: true, transparent: true, opacity: 0.96,
        worldUnits: false, blending: THREE.NormalBlending, depthWrite: true,
      });
      mat.resolution.set(canvas.clientWidth || 800, canvas.clientHeight || 560);
      pathObj = new Line2(geo, mat);
    } else {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
      pathObj = new THREE.Line(geo, new THREE.LineBasicMaterial({
        vertexColors: true, transparent: true, opacity: 0.95,
        blending: THREE.NormalBlending, depthWrite: true,
      }));
    }
    scene.add(pathObj);

    // frame the camera on the walk
    const cx=(minX+maxX)/2, cy=(minY+maxY)/2, cz=(minZ+maxZ)/2;
    const span = Math.max(maxX-minX, maxY-minY, maxZ-minZ, 10);
    controls.target.set(cx, cy, cz);
    camera.position.set(cx + span*0.4, cy + span*0.3, cz + span*1.1);
    camera.near = span / 1000; camera.far = span * 50;
    camera.updateProjectionMatrix();
  }

  async function setWalk(digits) {
    await ensureLine2();
    build(digits);
  }

  function resize() {
    const w = canvas.clientWidth || canvas.parentElement.clientWidth;
    const h = canvas.clientHeight || 560;
    renderer.setSize(w, h, false);
    camera.aspect = w / h; camera.updateProjectionMatrix();
    if (useLine2 && pathObj?.material?.resolution) pathObj.material.resolution.set(w, h);
  }
  window.addEventListener('resize', resize);
  // also observe container size (canvas starts at 0 before layout)
  new ResizeObserver(resize).observe(canvas.parentElement);

  function loop() {
    controls.update();
    renderer.render(scene, camera);
    requestAnimationFrame(loop);
  }
  resize(); loop();

  return { setWalk };
}
