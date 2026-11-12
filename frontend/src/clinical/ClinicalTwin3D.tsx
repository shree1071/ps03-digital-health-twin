import { useMemo, useState } from "react";
import type { Assessment, AssessmentStatus, TwinStateName, TwinStatePayload, VitalMetric } from "../api/types";
import { OrganCard } from "../components/OrganCard";
import { ANATOMY_CREDIT, DEFAULT_LAYERS, LAYERS, type Layer } from "../lib/anatomy";
import { TwinScene } from "../twin3d/TwinScene";
import type { PatientStatePayload } from "./types";

export interface WatchLiveOverride {
  heart_rate: number;
  spo2: number;
  respiratory_rate: number;
  temperature: number;
}

function clinicalThresholdStatus(
  key: VitalMetric,
  val: number,
  state: PatientStatePayload,
): AssessmentStatus {
  if (key === "heart_rate") {
    const adj = state.profile.beta_blocked ? val + state.profile.pulse_correction_bpm : val;
    if (adj >= 115 || adj <= 45) return "anomalous";
    if (adj >= 95 || adj <= 50) return "watch";
    return "normal";
  }
  if (key === "spo2") {
    if (val === 0) return "normal"; // probe-off artifact, handled by artifact banner
    const lowTarget = state.profile.spo2_scale === 2 ? state.profile.spo2_target[0] : 92;
    if (val < lowTarget) return "anomalous";
    if (val <= 94 && state.profile.spo2_scale !== 2) return "watch";
    return "normal";
  }
  if (key === "respiratory_rate") {
    if (val >= 25 || val <= 8) return "anomalous";
    if (val >= 21 || val <= 11) return "watch";
    return "normal";
  }
  if (key === "temperature") {
    if (val >= 38.5 || val <= 35.0) return "anomalous";
    if (val >= 37.6 || val <= 35.8) return "watch";
    return "normal";
  }
  return "normal";
}

const STATUS_RANK: Record<AssessmentStatus, number> = { normal: 0, watch: 1, anomalous: 2 };

function buildAssessments(
  state: PatientStatePayload,
  liveOverride?: WatchLiveOverride | null,
): Partial<Record<VitalMetric, Assessment>> {
  const obs = liveOverride ?? state.observation;
  if (!obs) return {};
  const out: Partial<Record<VitalMetric, Assessment>> = {};
  const driftByMetric = new Map(state.drift.map((d) => [d.metric, d]));

  const metrics: Array<{ key: VitalMetric; val: number | null }> = [
    { key: "heart_rate", val: obs.heart_rate },
    { key: "spo2", val: obs.spo2 },
    { key: "temperature", val: obs.temperature },
    { key: "respiratory_rate", val: obs.respiratory_rate },
  ];

  for (const { key, val } of metrics) {
    if (val === null || val === undefined) continue;
    const base = state.baselines?.[key];
    const expected = base?.center ?? val;
    const spread = base?.spread ?? 1;
    const drift = driftByMetric.get(key);
    const z = drift?.robust_z ?? (spread > 0 ? (val - expected) / spread : 0);
    const driftStatus: AssessmentStatus =
      Math.abs(z) >= 3 ? "anomalous" : Math.abs(z) >= 2 ? "watch" : "normal";
    const threshStatus = clinicalThresholdStatus(key, val, state);
    const status =
      STATUS_RANK[threshStatus] >= STATUS_RANK[driftStatus] ? threshStatus : driftStatus;

    out[key] = {
      metric: key,
      value: val,
      expected,
      expected_low: expected - 2 * spread,
      expected_high: expected + 2 * spread,
      deviation: val - expected,
      deviation_pct: expected !== 0 ? ((val - expected) / expected) * 100 : 0,
      robust_z: z,
      status,
      consecutive: drift?.sustained ?? 0,
      context: liveOverride ? "smartwatch-live" : "post-discharge",
      reason:
        status !== "normal"
          ? `${key.replace("_", " ")} ${val} (${status})`
          : drift
            ? `Sustained drift (z=${drift.robust_z.toFixed(1)})`
            : null,
      reference_flag: null,
    };
  }
  return out;
}

function deriveTwinState(
  state: PatientStatePayload,
  assessments: Partial<Record<VitalMetric, Assessment>>,
  hasOverride: boolean,
): TwinStateName {
  const statuses = Object.values(assessments).map((a) => a?.status);
  if (statuses.includes("anomalous")) return "ANOMALOUS";
  if (statuses.includes("watch")) return "ELEVATED";
  if (!hasOverride && state.timestamp) {
    if (state.tier === "URGENT" || state.tier === "REVIEW") return "ANOMALOUS";
    if (state.tier === "WATCH") return "ELEVATED";
  }
  return "STABLE";
}

export function clinicalToTwinPayload(
  state: PatientStatePayload,
  liveOverride?: WatchLiveOverride | null,
): TwinStatePayload {
  const obs = liveOverride ?? state.observation;
  const ts = liveOverride ? new Date().toISOString() : state.timestamp ?? new Date().toISOString();
  const assessments = buildAssessments(state, liveOverride);
  const twinState = deriveTwinState(state, assessments, Boolean(liveOverride));

  return {
    twin_id: state.patient_id,
    state: twinState,
    previous_state: null,
    state_since: ts,
    state_reason: liveOverride
      ? "Live smartwatch vitals driving 3D digital twin"
      : state.explanation?.narrative ?? "Post-discharge clinical monitoring",
    state_description: state.profile.notes || "3D anatomical digital twin driven by live vitals",
    twin_time: ts,
    received_at: ts,
    data_age_seconds: 0,
    observation: {
      timestamp: ts,
      heart_rate: obs?.heart_rate ?? 72,
      spo2: obs?.spo2 ?? 97,
      temperature: obs?.temperature ?? 36.7,
      respiratory_rate: obs?.respiratory_rate ?? 16,
      steps: 0,
      activity_intensity: !liveOverride && state.observation ? state.observation.activity_intensity : 0,
      is_asleep: false,
      source: liveOverride ? "smartwatch_live" : state.observation?.source ?? "wearable",
    },
    assessments,
    effective_intensity: 0,
    activity_level: "resting",
    rolling: {},
    last_hr_recovery_bpm: null,
  };
}

export function ClinicalTwin3D({
  state,
  liveOverride,
  height = "460px",
}: {
  state: PatientStatePayload;
  liveOverride?: WatchLiveOverride | null;
  height?: string;
}) {
  const [labels, setLabels] = useState(true);
  const [xray, setXray] = useState(false);
  const [layers, setLayers] = useState<Record<Layer, boolean>>(DEFAULT_LAYERS);
  const [selected, setSelected] = useState<string | null>(null);
  const [viewKey, setViewKey] = useState(0);

  const payload = useMemo(() => clinicalToTwinPayload(state, liveOverride), [state, liveOverride]);

  return (
    <div className="col" style={{ gap: 10 }}>
      <div className="toolbar" role="group" aria-label="Body systems" style={{ alignItems: "center" }}>
        {LAYERS.map((l) => (
          <label key={l.key} className="toggle">
            <input
              type="checkbox"
              checked={layers[l.key]}
              onChange={(e) => setLayers({ ...layers, [l.key]: e.target.checked })}
            />
            {l.label}
          </label>
        ))}
        <span className="toolbar__sep" aria-hidden />
        <label className="toggle">
          <input type="checkbox" checked={xray} onChange={(e) => setXray(e.target.checked)} />
          X-ray
        </label>
        <label className="toggle">
          <input type="checkbox" checked={labels} onChange={(e) => setLabels(e.target.checked)} />
          Readings
        </label>
        <span className="toolbar__sep" aria-hidden />
        <button
          type="button"
          className="btn btn--small"
          onClick={() => setViewKey((k) => k + 1)}
          title="Center and fit human body in frame"
        >
          Center Body
        </button>
      </div>

      {labels && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
            gap: 8,
            marginBottom: 10,
          }}
          aria-label="Live vital readings"
        >
          {([
            { key: "heart_rate", label: "Heart Rate", unit: "bpm" },
            { key: "spo2", label: "Oxygen (SpO₂)", unit: "%" },
            { key: "respiratory_rate", label: "Respiratory Rate", unit: "br/min" },
            { key: "temperature", label: "Temperature", unit: "°C" },
          ] as const).map(({ key, label, unit }) => {
            const a = payload.assessments[key];
            const tone =
              a?.status === "anomalous"
                ? { bg: "#fef2f2", border: "#fca5a5", text: "#991b1b" }
                : a?.status === "watch"
                  ? { bg: "#fffbeb", border: "#fcd34d", text: "#92400e" }
                  : { bg: "#f0fdf4", border: "#86efac", text: "#166534" };
            return (
              <div
                key={key}
                style={{
                  background: tone.bg,
                  border: `1px solid ${tone.border}`,
                  borderRadius: 10,
                  padding: "8px 12px",
                  display: "flex",
                  flexDirection: "column",
                  gap: 2,
                }}
              >
                <span className="small muted" style={{ fontSize: 11 }}>{label}</span>
                <span style={{ fontSize: 18, fontWeight: 700, color: tone.text }}>
                  {a?.value != null ? `${a.value} ${unit}` : "—"}
                </span>
              </div>
            );
          })}
        </div>
      )}

      <TwinScene
        key={viewKey}
        payload={payload}
        showLabels={labels}
        xray={xray}
        layers={layers}
        selected={selected}
        onSelect={setSelected}
        height={height}
      />

      {selected && (
        <OrganCard name={selected} state={payload} onClose={() => setSelected(null)} />
      )}

      <p className="small muted" style={{ margin: 0 }}>
        {liveOverride
          ? "3D anatomy synced live with your connected smartwatch — abnormal heart, lung, or temperature values highlight directly on the body."
          : state.timestamp
            ? "3D anatomy driven live by your latest wearable heart rate, SpO₂, respiration, and temperature."
            : "Connect your watch or start streaming to animate and highlight the 3D body."}{" "}
        Drag to rotate, scroll to zoom, click any organ to inspect. {ANATOMY_CREDIT}
      </p>
    </div>
  );
}
