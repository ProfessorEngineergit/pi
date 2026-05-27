// Cosmic background: a slowly drifting starfield + a 3D spiral of pi's digits,
// each point coloured by its digit value. Pure three.js core (robust).
import * as THREE from 'three';

export const PALETTE = [
  0x5b6cff, 0x00f0ff, 0x3ce6a0, 0x9bff3c, 0xffe23c,
  0xffae3c, 0xff6b3c, 0xff2bd6, 0xc44bff, 0x7b5bff,
];

// soft round glowing sprite for points (instead of hard squares)
function discTexture() {
  const s = 64, cv = document.createElement('canvas');
  cv.width = cv.height = s;
  const ctx = cv.getContext('2d');
  const g = ctx.createRadialGradient(s/2, s/2, 0, s/2, s/2, s/2);
  g.addColorStop(0.0, 'rgba(255,255,255,1)');
  g.addColorStop(0.35, 'rgba(255,255,255,0.85)');
  g.addColorStop(1.0, 'rgba(255,255,255,0)');
  ctx.fillStyle = g; ctx.beginPath();
  ctx.arc(s/2, s/2, s/2, 0, Math.PI*2); ctx.fill();
  return new THREE.CanvasTexture(cv);
}

export function initBackground(canvas) {
  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  const DISC = discTexture();

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(60, 1, 0.1, 2000);
  camera.position.set(0, 0, 360);

  // ---- starfield -----------------------------------------------------------
  const STARS = 2600;
  const sPos = new Float32Array(STARS * 3);
  const sCol = new Float32Array(STARS * 3);
  const c = new THREE.Color();
  for (let i = 0; i < STARS; i++) {
    const r = 500 + Math.random() * 700;
    const th = Math.random() * Math.PI * 2;
    const ph = Math.acos(2 * Math.random() - 1);
    sPos[i*3]   = r * Math.sin(ph) * Math.cos(th);
    sPos[i*3+1] = r * Math.sin(ph) * Math.sin(th);
    sPos[i*3+2] = r * Math.cos(ph);
    c.setHSL(0.55 + Math.random()*0.25, 0.7, 0.5 + Math.random()*0.4);
    sCol[i*3]=c.r; sCol[i*3+1]=c.g; sCol[i*3+2]=c.b;
  }
  const starGeo = new THREE.BufferGeometry();
  starGeo.setAttribute('position', new THREE.BufferAttribute(sPos, 3));
  starGeo.setAttribute('color', new THREE.BufferAttribute(sCol, 3));
  const stars = new THREE.Points(starGeo, new THREE.PointsMaterial({
    size: 4.5, map: DISC, alphaTest: 0.02, vertexColors: true,
    transparent: true, opacity: 0.85,
    blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  scene.add(stars);

  // ---- digit spiral (filled in once digits arrive) -------------------------
  const spiralGroup = new THREE.Group();
  scene.add(spiralGroup);

  function setDigits(digits) {
    spiralGroup.clear();
    const n = Math.min(digits.length, 2400);
    const pos = new Float32Array(n * 3);
    const col = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      const d = digits.charCodeAt(i) - 48;
      const t = i / n;
      const angle = i * 0.24;                 // golden-ish spiral
      const radius = 40 + t * 230;
      pos[i*3]   = Math.cos(angle) * radius;
      pos[i*3+1] = (t - 0.5) * 320;
      pos[i*3+2] = Math.sin(angle) * radius;
      c.setHex(PALETTE[d] ?? 0xffffff);
      col[i*3]=c.r; col[i*3+1]=c.g; col[i*3+2]=c.b;
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.setAttribute('color', new THREE.BufferAttribute(col, 3));
    spiralGroup.add(new THREE.Points(g, new THREE.PointsMaterial({
      size: 6.5, map: DISC, alphaTest: 0.02, vertexColors: true,
      transparent: true, opacity: 0.98,
      blending: THREE.AdditiveBlending, depthWrite: false,
    })));
  }

  // ---- interaction + loop --------------------------------------------------
  const mouse = { x: 0, y: 0 };
  window.addEventListener('pointermove', (e) => {
    mouse.x = (e.clientX / window.innerWidth - 0.5);
    mouse.y = (e.clientY / window.innerHeight - 0.5);
  });
  let scrollY = 0;
  window.addEventListener('scroll', () => { scrollY = window.scrollY; });

  function resize() {
    const w = window.innerWidth, h = window.innerHeight;
    renderer.setSize(w, h);           // updateStyle=true -> CSS matches viewport
    camera.aspect = w / h; camera.updateProjectionMatrix();
  }
  window.addEventListener('resize', resize); resize();

  const clock = new THREE.Clock();
  function loop() {
    const t = clock.getElapsedTime();
    stars.rotation.y = t * 0.008;
    spiralGroup.rotation.y = t * 0.04;
    spiralGroup.rotation.z = Math.sin(t * 0.1) * 0.05;
    // very gentle parallax + minimal scroll dive
    camera.position.x += (mouse.x * 16 - camera.position.x) * 0.025;
    camera.position.y += (-mouse.y * 16 - camera.position.y) * 0.025;
    camera.position.z = 360 - Math.min(scrollY, 1600) * 0.012;
    camera.lookAt(0, 0, 0);
    renderer.render(scene, camera);
    requestAnimationFrame(loop);
  }
  loop();

  return { setDigits };
}
