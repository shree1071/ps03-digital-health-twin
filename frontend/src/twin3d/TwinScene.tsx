import { ContactShadows, Line, OrbitControls, useGLTF } from "@react-three/drei";
import { Canvas, useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { Component, Suspense, useEffect, useMemo, useRef, useState, type MutableRefObject, type ReactNode } from "react";
import * as THREE from "three";
import type { TwinStatePayload } from "../api/types";
import { DEFAULT_LAYERS, STRUCTURES, type Layer } from "../lib/anatomy";
import { arterialColor, heartbeatShape, visualsFor, type TwinVisuals } from "../lib/twinVisuals";

const MODEL_URL = "/models/anatomy_twin.glb";
const DRACO_PATH = "/draco/";

interface Props {
  payload: TwinStatePayload | null;
  showLabels?: boolean;
  xray?: boolean;
  layers?: Record<Layer, boolean>;
  selected?: string | null;
  onSelect?: (name: string | null) => void;
  height?: number | string;
}

export function TwinScene({
  payload,
  showLabels = true,
  xray = false,
  layers = DEFAULT_LAYERS,
  selected = null,
  onSelect,
  height = "100%",
}: Props) {
  const reduceMotion = usePrefersReducedMotion();
  const labelEls = useRef<Record<LabelKey, HTMLDivElement | null>>({ heart: null, lung: null, temp: null });
  const labels = labelContent(payload);
  return (
    <div className="twin-canvas" style={{ height }}>
      <ModelBoundary
        fallback={
          <p className="twin-canvas__error">
            The 3D anatomy model could not be loaded. Check that frontend/public/models/anatomy_twin.glb exists, then
            reload.
          </p>
        }
      >
        <Canvas
          camera={{ position: [0, 1.0, 4.3], fov: 30 }}
          dpr={[1, 2]}
          gl={{ antialias: true, alpha: true, preserveDrawingBuffer: true }}
          aria-label="3D anatomical digital twin of the subject"
          role="img"
          onPointerMissed={() => onSelect?.(null)}
        >
          <hemisphereLight args={["#ffffff", "#9aa9b3", 0.9]} />
          <directionalLight position={[2.5, 4, 3]} intensity={1.6} />
          <directionalLight position={[-3, 2, -2]} intensity={0.6} color="#cfe0ea" />
          <directionalLight position={[0, 1.5, 4]} intensity={0.5} />
          <CalibrationPlate />
          <ContactShadows position={[0, 0.002, 0]} opacity={0.35} scale={3} blur={2.4} far={2} />
          <Suspense fallback={null}>
            <Anatomy
              payload={payload}
              xray={xray}
              layers={layers}
              selected={selected}
              onSelect={onSelect}
              still={reduceMotion}
              labelEls={labelEls}
              labelVisible={{
                heart: showLabels && labels.heart !== null,
                lung: showLabels && labels.lung !== null,
                temp: showLabels && labels.temp !== null,
              }}
            />
          </Suspense>
          <OrbitControls target={[0, 0.86, 0]} enablePan={false} minDistance={1.2} maxDistance={6} minPolarAngle={0.3} maxPolarAngle={1.8} />
        </Canvas>
        {(Object.keys(labelEls.current) as LabelKey[]).map((key) => (
          <div
            key={key}
            ref={(el) => {
              labelEls.current[key] = el;
            }}
            className={`callout callout-${labels[key]?.status ?? "normal"} ${key === "lung" ? "callout-left" : ""}`}
            style={{ display: "none" }}
            aria-hidden
          >
            {labels[key]?.content}
          </div>
        ))}
      </ModelBoundary>
    </div>
  );
}

class ModelBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

// ------------------------------------------------------------------ materials
function skinMaterial(): THREE.MeshStandardMaterial {
  const mat = new THREE.MeshStandardMaterial({
    color: "#D9B8A6",
    roughness: 0.45,
    metalness: 0,
    transparent: true,
    opacity: 0.16,
    depthWrite: false,
    side: THREE.FrontSide,
  });
  // Fresnel rim: the silhouette reads clearly while organs stay visible inside.
  mat.onBeforeCompile = (shader) => {
    shader.fragmentShader = shader.fragmentShader.replace(
      "#include <opaque_fragment>",
      `float rim = 1.0 - abs(dot(normalize(normal), normalize(vViewPosition)));
       diffuseColor.a = clamp(diffuseColor.a + pow(rim, 2.6) * 0.55, 0.0, 1.0);
       #include <opaque_fragment>`,
    );
  };
  return mat;
}

type Parts = Record<string, THREE.Mesh>;

// ---------------------------------------------------------------- the anatomy
function Anatomy({
  payload,
  xray,
  layers,
  selected,
  onSelect,
  still,
  labelEls,
  labelVisible,
}: {
  payload: TwinStatePayload | null;
  labelEls: MutableRefObject<Record<LabelKey, HTMLDivElement | null>>;
  labelVisible: Record<LabelKey, boolean>;
  xray: boolean;
  layers: Record<Layer, boolean>;
  selected: string | null;
  onSelect?: (name: string | null) => void;
  still: boolean;
}) {
  const { scene } = useGLTF(MODEL_URL, DRACO_PATH);
  const root = useMemo(() => scene.clone(true), [scene]);
  const posture = useRef<THREE.Group>(null);
  const bed = useRef<THREE.Mesh>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const visuals: TwinVisuals | null = payload ? visualsFor(payload) : null;
  const vRef = useRef(visuals);
  vRef.current = visuals;
  const selRef = useRef<string | null>(selected);
  selRef.current = hovered ?? selected;

  // Give every structure its own material so its glow can be animated independently.
  const parts = useMemo<Parts>(() => {
    const out: Parts = {};
    root.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh) return;
      const name = STRUCTURES[mesh.name] ? mesh.name : mesh.parent && STRUCTURES[mesh.parent.name] ? mesh.parent.name : mesh.name;
      if (name === "Skin") {
        mesh.material = skinMaterial();
        mesh.renderOrder = 3;
        mesh.raycast = () => undefined; // clicks pass through the skin to the organs
      } else {
        const src = mesh.material as THREE.MeshStandardMaterial;
        const mat = src.clone();
        // Bones and muscles are see-through so the organs they cover stay visible.
        const veil = name === "Skeleton" ? 0.42 : name === "Muscles" ? 0.55 : 1;
        mat.transparent = veil < 1;
        mat.opacity = veil;
        mat.depthWrite = veil === 1;
        mat.emissive = new THREE.Color(0, 0, 0);
        mesh.material = mat;
        mesh.renderOrder = veil < 1 ? 2 : 1;
      }
      mesh.userData.base = { position: mesh.position.clone(), scale: mesh.scale.clone() };
      out[name] = mesh;
    });
    return out;
  }, [root]);

  // Layer visibility.
  useEffect(() => {
    for (const [name, mesh] of Object.entries(parts)) {
      const info = STRUCTURES[name];
      mesh.visible = info ? layers[info.layer] : true;
    }
  }, [layers, parts]);

  const { camera, size } = useThree();
  const visRef = useRef(labelVisible);
  visRef.current = labelVisible;
  const anchors = useMemo(() => {
    const p = (n: string, fallback: [number, number, number]): [number, number, number] =>
      parts[n] ? [parts[n].position.x, parts[n].position.y, parts[n].position.z + 0.04] : fallback;
    return { heart: p("Heart", [0.03, 1.27, 0.08]), lung: p("Lung_R", [-0.08, 1.3, 0.05]), head: [0.05, 1.62, 0.09] as [number, number, number] };
  }, [parts]);

  const clock = useRef({ beat: 0, breath: 0, t: 0, lying: 0 });
  const tmp = useMemo(() => ({ c: new THREE.Color(), teal: new THREE.Color("#0E7C7B"), v: new THREE.Vector3() }), []);
  const targets = useMemo<Record<LabelKey, [number, number, number]>>(
    () => ({
      heart: [0.42, anchors.heart[1] + 0.03, 0.1],
      lung: [-0.46, anchors.lung[1] + 0.1, 0.1],
      temp: [0.36, 1.72, 0.1],
    }),
    [anchors],
  );

  useFrame((_, delta) => {
    const v = vRef.current;
    if (!v || !posture.current) return;
    const dt = Math.min(delta, 0.05);
    const c = clock.current;
    const motion = still ? 0.25 : 1;
    c.t += dt;
    c.beat += (dt * v.heartBpm) / 60;
    if (v.breathsPerMin !== null) c.breath += (dt * v.breathsPerMin) / 60;
    c.lying += ((v.lying ? 1 : 0) - c.lying) * Math.min(1, dt * 2.5);
    const pulse = heartbeatShape(c.beat) * motion;
    const breath = v.breathsPerMin === null ? 0 : Math.sin(c.breath * Math.PI * 2) * motion;
    const focus = selRef.current;

    const emissive = (name: string, color: THREE.ColorRepresentation, k: number) => {
      const mesh = parts[name];
      if (!mesh) return;
      const mat = mesh.material as THREE.MeshStandardMaterial;
      mat.emissive.set(color).multiplyScalar(Math.max(k, 0));
      if (focus === name) mat.emissive.lerp(tmp.teal, 0.6).addScalar(0.05);
    };

    // Heart: beats at the live heart rate.
    const heart = parts.Heart;
    if (heart) {
      heart.scale.copy(heart.userData.base.scale).multiplyScalar(1 + 0.06 * pulse);
      emissive("Heart", v.regions.heart === "normal" ? "#6b1018" : v.regionColors.heart, 0.15 + (v.regions.heart === "normal" ? 0.35 : 0.8) * pulse);
    }
    // Arteries: pulse wave with each beat; colour from SpO2 when measured.
    const art = parts.Arteries;
    if (art) {
      const [r, g, b] = arterialColor(v.spo2);
      (art.material as THREE.MeshStandardMaterial).color.setRGB(r, g, b);
      emissive("Arteries", tmp.c.setRGB(r, g, b), 0.12 + 0.3 * heartbeatShape(c.beat - 0.08) * motion);
    }
    // Lungs and diaphragm: breathe at the live breathing rate (still when not measured).
    for (const n of ["Lung_L", "Lung_R"]) {
      const lung = parts[n];
      if (!lung) continue;
      const base = lung.userData.base.scale as THREE.Vector3;
      lung.scale.set(base.x * (1 + 0.035 * breath), base.y * (1 + 0.025 * breath), base.z * (1 + 0.035 * breath));
      emissive(n, v.regionColors.lungs, v.regions.lungs === "normal" ? 0 : 0.35 + 0.25 * Math.sin(c.t * 3));
    }
    const dia = parts.Diaphragm;
    if (dia) dia.position.y = dia.userData.base.position.y - 0.012 * breath;
    // Muscles: activation glow from measured movement intensity.
    emissive("Muscles", "#ff7a45", v.movement * (0.35 + 0.15 * Math.sin(c.t * 6)) * motion);
    // Brain: slow glow during sleep.
    emissive("Brain", "#4B57A6", c.lying * (0.25 + 0.1 * Math.sin(c.t * 1.2)));
    // Skin: warms when temperature is unusual (only when measured).
    const skin = parts.Skin?.material as THREE.MeshStandardMaterial | undefined;
    if (skin) {
      skin.opacity = xray ? 0.05 : 0.16;
      if (v.monitored.skin && v.regions.skin !== "normal") skin.emissive.set(v.regionColors.skin).multiplyScalar(0.18);
      else skin.emissive.set("#000000");
    }
    // Everything else: only the selection/hover highlight.
    for (const n of ["Veins", "Airway", "Liver", "Stomach", "Intestines", "Pancreas", "Spleen", "Kidney_L", "Kidney_R", "Bladder", "Skeleton"]) {
      emissive(n, "#000000", 0);
    }

    // Posture: lie down on a bed while the source reports sleep.
    posture.current.rotation.z = (Math.PI / 2) * c.lying;
    posture.current.position.x = 0.9 * c.lying;
    posture.current.position.y = 0.84 * c.lying;
    if (bed.current) {
      bed.current.visible = c.lying > 0.02;
      (bed.current.material as THREE.MeshStandardMaterial).opacity = 0.55 * c.lying;
    }

    // Position the DOM labels at the projected callout ends (hidden while lying down).
    posture.current.updateMatrixWorld();
    for (const key of ["heart", "lung", "temp"] as LabelKey[]) {
      const el = labelEls.current[key];
      if (!el) continue;
      const show = visRef.current[key] && c.lying < 0.05;
      if (!show) {
        el.style.display = "none";
        continue;
      }
      tmp.v.set(...targets[key]);
      posture.current.localToWorld(tmp.v).project(camera);
      if (tmp.v.z > 1) {
        el.style.display = "none";
        continue;
      }
      el.style.display = "block";
      el.style.left = `${((tmp.v.x + 1) / 2) * size.width}px`;
      el.style.top = `${((1 - tmp.v.y) / 2) * size.height}px`;
    }
  });

  const pick = (e: ThreeEvent<PointerEvent>) => {
    e.stopPropagation();
    const name = nameOf(e.object, parts);
    if (name) onSelect?.(name === selected ? null : name);
  };
  const hover = (e: ThreeEvent<PointerEvent>) => {
    e.stopPropagation();
    const name = nameOf(e.object, parts);
    setHovered(name);
    document.body.style.cursor = name ? "pointer" : "";
  };

  const a = payload?.assessments;
  const asleep = payload?.observation.is_asleep;
  return (
    <>
      <mesh ref={bed} position={[0, 0.3, 0]} visible={false}>
        <boxGeometry args={[2.0, 0.6, 0.75]} />
        <meshStandardMaterial color="#C9D6DE" transparent opacity={0} roughness={0.8} depthWrite={false} />
      </mesh>
      <group ref={posture}>
        <primitive
          object={root}
          onPointerDown={pick}
          onPointerMove={hover}
          onPointerOut={() => {
            setHovered(null);
            document.body.style.cursor = "";
          }}
        />
        <CalloutLine from={anchors.heart} to={targets.heart} status={a?.heart_rate?.status} hidden={!labelVisible.heart || !!asleep} />
        <CalloutLine from={anchors.lung} to={targets.lung} status={worse(a?.respiratory_rate?.status, a?.spo2?.status)} hidden={!labelVisible.lung || !!asleep} />
        <CalloutLine from={anchors.head} to={targets.temp} status={a?.temperature?.status} hidden={!labelVisible.temp || !!asleep} />
      </group>
    </>
  );
}

function nameOf(obj: THREE.Object3D, parts: Parts): string | null {
  let o: THREE.Object3D | null = obj;
  while (o) {
    if (parts[o.name] && STRUCTURES[o.name]) return o.name;
    o = o.parent;
  }
  return null;
}

function worse(a?: string, b?: string) {
  const rank: Record<string, number> = { normal: 0, watch: 1, anomalous: 2 };
  const x = a ?? "normal";
  const y = b ?? "normal";
  return rank[x] >= rank[y] ? x : y;
}

function CalloutLine({
  from,
  to,
  status = "normal",
  hidden,
}: {
  from: [number, number, number];
  to: [number, number, number];
  status?: string;
  hidden: boolean;
}) {
  const color = status === "anomalous" ? "#B4363F" : status === "watch" ? "#C98A1B" : "#5B6F7C";
  return (
    <group visible={!hidden}>
      <Line points={[from, to]} color={color} lineWidth={1} transparent opacity={0.8} />
      <mesh position={from}>
        <sphereGeometry args={[0.008, 10, 8]} />
        <meshBasicMaterial color={color} />
      </mesh>
    </group>
  );
}

type LabelKey = "heart" | "lung" | "temp";

/** Text for the on-body readings (plain DOM, positioned each frame by the scene). */
function labelContent(p: TwinStatePayload | null): Record<LabelKey, { status: string; content: ReactNode } | null> {
  const a = p?.assessments;
  return {
    heart: a?.heart_rate
      ? { status: a.heart_rate.status, content: <>Heart <b>{Math.round(a.heart_rate.value)}</b> bpm</> }
      : null,
    lung:
      a?.respiratory_rate || a?.spo2
        ? {
            status: worse(a?.respiratory_rate?.status, a?.spo2?.status),
            content: (
              <>
                {a?.respiratory_rate && (
                  <>
                    <b>{Math.round(a.respiratory_rate.value)}</b> br/min
                  </>
                )}
                {a?.respiratory_rate && a?.spo2 && ", "}
                {a?.spo2 && (
                  <>
                    SpO₂ <b>{a.spo2.value.toFixed(1)}</b>%
                  </>
                )}
              </>
            ),
          }
        : null,
    temp: a?.temperature ? { status: a.temperature.status, content: <><b>{a.temperature.value.toFixed(1)}</b> °C</> } : null,
  };
}

// ---------------------------------------------------------- calibration plate
function CalibrationPlate() {
  const ticks = useMemo(() => {
    const out: [number, number, number][][] = [];
    for (let i = 0; i < 72; i++) {
      const t = (i / 72) * Math.PI * 2;
      const r0 = i % 6 === 0 ? 0.86 : 0.9;
      out.push([
        [Math.cos(t) * r0, 0.003, Math.sin(t) * r0],
        [Math.cos(t) * 0.94, 0.003, Math.sin(t) * 0.94],
      ]);
    }
    return out;
  }, []);
  return (
    <group>
      <mesh rotation-x={-Math.PI / 2}>
        <circleGeometry args={[0.98, 96]} />
        <meshStandardMaterial color="#E3EAEE" roughness={0.9} />
      </mesh>
      {[0.42, 0.94].map((r) => (
        <mesh key={r} rotation-x={-Math.PI / 2} position={[0, 0.002, 0]}>
          <ringGeometry args={[r - 0.004, r, 128]} />
          <meshBasicMaterial color="#98AAB5" />
        </mesh>
      ))}
      {ticks.map((pts, i) => (
        <Line key={i} points={pts} color="#98AAB5" lineWidth={1} />
      ))}
    </group>
  );
}

function usePrefersReducedMotion() {
  const [reduce, setReduce] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduce(mq.matches);
    const on = () => setReduce(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return reduce;
}

useGLTF.preload(MODEL_URL, DRACO_PATH);
