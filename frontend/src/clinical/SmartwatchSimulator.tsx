import { useEffect, useRef, useState } from "react";
import type { WatchLiveOverride } from "./ClinicalTwin3D";
import { clinicalApi, type XgbComparison } from "./api";
import type { MonitorResult, PatientStatePayload } from "./types";
import { TIER_LABEL } from "./ui";

const PRESETS: Array<{ label: string; values: WatchLiveOverride }> = [
  { label: "Normal", values: { heart_rate: 72, spo2: 97, respiratory_rate: 16, temperature: 36.7 } },
  { label: "Watch", values: { heart_rate: 98, spo2: 94, respiratory_rate: 21, temperature: 37.6 } },
  { label: "Urgent", values: { heart_rate: 128, spo2: 86, respiratory_rate: 27, temperature: 38.9 } },
  { label: "Cardiac / Heart Attack", values: { heart_rate: 148, spo2: 84, respiratory_rate: 28, temperature: 37.3 } },
  { label: "Probe Off (0% SpO₂)", values: { heart_rate: 72, spo2: 0, respiratory_rate: 16, temperature: 36.7 } },
];

const CARDIAC_SEQUENCE: Array<{ label: string; values: WatchLiveOverride; notify: boolean }> = [
  { label: "1/5 Baseline resting", values: { heart_rate: 74, spo2: 97, respiratory_rate: 16, temperature: 36.7 }, notify: false },
  { label: "2/5 Rising heart rate", values: { heart_rate: 92, spo2: 95, respiratory_rate: 19, temperature: 36.8 }, notify: false },
  { label: "3/5 Watch threshold", values: { heart_rate: 108, spo2: 93, respiratory_rate: 22, temperature: 37.0 }, notify: false },
  { label: "4/5 Acute tachycardia", values: { heart_rate: 126, spo2: 89, respiratory_rate: 25, temperature: 37.2 }, notify: false },
  { label: "5/5 Urgent cardiac distress", values: { heart_rate: 148, spo2: 84, respiratory_rate: 28, temperature: 37.3 }, notify: true },
];

export function SmartwatchSimulator({
  patientId,
  state,
  connected,
  onToggleConnect,
  values,
  onChangeValues,
}: {
  patientId: string;
  state: PatientStatePayload;
  connected: boolean;
  onToggleConnect: () => void;
  values: WatchLiveOverride;
  onChangeValues: (next: WatchLiveOverride) => void;
}) {
  const [sending, setSending] = useState(false);
  const [simStep, setSimStep] = useState<string | null>(null);
  const simTimerRef = useRef<number | null>(null);
  const [sendWhatsapp, setSendWhatsapp] = useState(true);
  const [whatsappTo, setWhatsappTo] = useState("");
  const [lastResult, setLastResult] = useState<
    (MonitorResult & {
      whatsapp?: {
        queued?: boolean;
        job_id?: string;
        sent: boolean;
        dry_run?: boolean;
        recipients?: string[];
        error?: string;
      };
      xgboost?: XgbComparison;
    }) | null
  >(null);
  const [xgbComp, setXgbComp] = useState<XgbComparison | null>(null);
  const [doubtQ, setDoubtQ] = useState("");
  const [doubtAns, setDoubtAns] = useState<string | null>(null);
  const [doubtSending, setDoubtSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    clinicalApi
      .patientXgb(patientId)
      .then((res) => {
        if (!cancelled) setXgbComp(res);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [patientId, state.timestamp]);

  const activeTier = lastResult?.tier ?? state.tier;
  const activeKind = lastResult?.alert_kind ?? state.alert_kind;
  const isSensor = activeKind != null && activeKind !== "CLINICAL";

  const watchTone = !connected
    ? "sensor"
    : isSensor
      ? "sensor"
      : activeTier === "URGENT"
        ? "urgent"
        : activeTier === "REVIEW"
          ? "review"
          : activeTier === "WATCH"
            ? "watch"
            : "ok";

  const watchNotifText = !connected
    ? "Watch disconnected · Click Connect"
    : lastResult
      ? lastResult.suppressed || lastResult.alert_kind !== "CLINICAL"
        ? `Sensor check: ${lastResult.artifact.reasons[0] ?? "Unreliable signal suppressed"}`
        : lastResult.tier === "NORMAL"
          ? "Vitals normal · No alert sent"
          : `${TIER_LABEL[lastResult.tier]} alert sent to care team`
      : state.timestamp
        ? `Latest: ${isSensor ? "Sensor check" : TIER_LABEL[state.tier]}`
        : "Connected · Adjust & send";

  useEffect(() => {
    return () => {
      if (simTimerRef.current) window.clearTimeout(simTimerRef.current);
    };
  }, []);

  const handleSend = async (override?: WatchLiveOverride, allowWhatsapp = true) => {
    const payload = override ?? values;
    setSending(true);
    setError(null);
    // Advance 60s past the patient's latest reading so simulator-clocked streams
    // don't trigger a multi-day DEVICE_OFFLINE dropout gap.
    const nextTs = state.timestamp
      ? new Date(new Date(state.timestamp).getTime() + 60_000).toISOString()
      : new Date().toISOString();
    try {
      const res = await clinicalApi.sendReading(patientId, {
        timestamp: nextTs,
        heart_rate: payload.heart_rate,
        spo2: payload.spo2,
        respiratory_rate: payload.respiratory_rate,
        temperature: payload.temperature,
        activity_intensity: 0,
        source: "smartwatch_demo",
        send_whatsapp: sendWhatsapp && allowWhatsapp,
        whatsapp_to: whatsappTo.trim() || null,
      });
      if ("tier" in res) {
        setLastResult(res);
        if (res.xgboost) setXgbComp(res.xgboost);
        const jobId = res.whatsapp?.job_id;
        if (res.whatsapp?.queued && jobId) {
          void (async () => {
            for (let attempt = 0; attempt < 20; attempt += 1) {
              await new Promise((r) => window.setTimeout(r, 1000));
              try {
                const status = await clinicalApi.whatsappStatus(jobId);
                if (!status.queued) {
                  setLastResult((prev) =>
                    prev && prev.whatsapp?.job_id === jobId
                      ? { ...prev, whatsapp: { ...status, job_id: jobId } }
                      : prev,
                  );
                  break;
                }
              } catch {
                break;
              }
            }
          })();
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to send smartwatch reading");
    } finally {
      setSending(false);
    }
  };

  const runCardiacSimulation = () => {
    if (!connected) return;
    if (simTimerRef.current) window.clearTimeout(simTimerRef.current);

    let idx = 0;
    const step = async () => {
      const item = CARDIAC_SEQUENCE[idx];
      if (!item) {
        setSimStep(null);
        return;
      }
      setSimStep(item.label);
      onChangeValues(item.values);
      await handleSend(item.values, item.notify);
      idx += 1;
      if (idx < CARDIAC_SEQUENCE.length) {
        simTimerRef.current = window.setTimeout(step, 1300);
      } else {
        setSimStep(null);
      }
    };
    void step();
  };

  return (
    <div className="watch-demo">
      {/* Realistic Smartwatch (no hand) */}
      <div className="watch-stage" aria-label="Smartwatch showing live vitals">
        <div className="watch-wrap">
          <div className="watch-band watch-band--top">
            <span className="watch-lug" />
          </div>
          <div className="watch-band watch-band--bottom">
            <span className="watch-lug" />
          </div>

          <div className={`watch-body watch-body--${watchTone}`}>
            <div className="watch-crown" aria-hidden />
            <div className="watch-side-btn" aria-hidden />
            <div className="watch-screen">
              <div className="watch-glass" aria-hidden />
              <div className="watch-screen__top">
                <span className={`watch-dot watch-dot--${watchTone}`} />
                <span className="watch-status">
                  {!connected ? "DISCONNECTED" : isSensor ? "SENSOR" : TIER_LABEL[activeTier].toUpperCase()}
                </span>
                <span className="watch-clock">
                  {new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </span>
              </div>

              <div className="watch-hr">
                <span className="watch-heart" aria-hidden>♥</span>
                <span className="watch-hr__val">{connected ? values.heart_rate : "--"}</span>
                <span className="watch-hr__unit">bpm</span>
              </div>

              <div className="watch-grid">
                <div>
                  <span className="watch-lbl">SpO₂</span>
                  <strong>{connected ? `${values.spo2}%` : "--"}</strong>
                </div>
                <div>
                  <span className="watch-lbl">RR</span>
                  <strong>{connected ? values.respiratory_rate : "--"}</strong>
                </div>
                <div>
                  <span className="watch-lbl">Temp</span>
                  <strong>{connected ? `${values.temperature.toFixed(1)}°` : "--"}</strong>
                </div>
              </div>

              <div className={`watch-toast watch-toast--${watchTone}`}>
                {watchNotifText}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Interactive Controls */}
      <div className="watch-controls">
        <div className="spread" style={{ gap: 8 }}>
          <div>
            <strong className="small">Watch Pairing</strong>
            <div className="small muted">
              {connected
                ? "Connected — slider changes update your 3D body live"
                : "Disconnected — click Connect Your Watch to sync"}
            </div>
          </div>
          <button
            type="button"
            className={`btn btn--small ${connected ? "" : "btn--primary"}`}
            onClick={onToggleConnect}
          >
            {connected ? "Disconnect Watch" : "Connect Your Watch"}
          </button>
        </div>

        <div>
          <div className="spread" style={{ marginBottom: 6, gap: 8 }}>
            <span className="small muted" style={{ fontWeight: 600 }}>
              Quick scenarios (auto-sends &amp; notifies phone if abnormal)
            </span>
            <button
              type="button"
              className="btn btn--small btn--primary"
              disabled={!connected || simStep !== null}
              onClick={runCardiacSimulation}
            >
              {simStep ? `Simulating: ${simStep}` : "▶ Run Heart-Attack Simulation"}
            </button>
          </div>
          <div className="row row--tight" style={{ flexWrap: "wrap" }}>
            {PRESETS.map((p) => (
              <button
                key={p.label}
                type="button"
                className="btn btn--small"
                disabled={!connected || simStep !== null}
                onClick={() => {
                  onChangeValues(p.values);
                  void handleSend(p.values, true);
                }}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        <div className="watch-sliders">
          <label className="watch-slider">
            <div className="spread small">
              <span>Heart rate</span>
              <strong>{values.heart_rate} bpm</strong>
            </div>
            <input
              type="range"
              min={30}
              max={180}
              step={1}
              disabled={!connected}
              value={values.heart_rate}
              onChange={(e) => onChangeValues({ ...values, heart_rate: Number(e.target.value) })}
              onMouseUp={() => void handleSend()}
              onTouchEnd={() => void handleSend()}
            />
          </label>

          <label className="watch-slider">
            <div className="spread small">
              <span>Oxygen (SpO₂)</span>
              <strong>{values.spo2}%</strong>
            </div>
            <input
              type="range"
              min={0}
              max={100}
              step={1}
              disabled={!connected}
              value={values.spo2}
              onChange={(e) => onChangeValues({ ...values, spo2: Number(e.target.value) })}
              onMouseUp={() => void handleSend()}
              onTouchEnd={() => void handleSend()}
            />
          </label>

          <label className="watch-slider">
            <div className="spread small">
              <span>Breathing rate</span>
              <strong>{values.respiratory_rate} br/min</strong>
            </div>
            <input
              type="range"
              min={6}
              max={40}
              step={1}
              disabled={!connected}
              value={values.respiratory_rate}
              onChange={(e) =>
                onChangeValues({ ...values, respiratory_rate: Number(e.target.value) })
              }
              onMouseUp={() => void handleSend()}
              onTouchEnd={() => void handleSend()}
            />
          </label>

          <label className="watch-slider">
            <div className="spread small">
              <span>Temperature</span>
              <strong>{values.temperature.toFixed(1)} °C</strong>
            </div>
            <input
              type="range"
              min={34}
              max={41}
              step={0.1}
              disabled={!connected}
              value={values.temperature}
              onChange={(e) => onChangeValues({ ...values, temperature: Number(e.target.value) })}
              onMouseUp={() => void handleSend()}
              onTouchEnd={() => void handleSend()}
            />
          </label>
        </div>

        <div className="row row--tight" style={{ alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
          <label className="toggle small">
            <input
              type="checkbox"
              checked={sendWhatsapp}
              onChange={(e) => setSendWhatsapp(e.target.checked)}
            />
            Send WhatsApp alert on abnormal reading
          </label>
          {sendWhatsapp && (
            <input
              className="input"
              style={{ maxWidth: 230, padding: "5px 9px", fontSize: 13 }}
              type="text"
              value={whatsappTo}
              onChange={(e) => setWhatsappTo(e.target.value)}
              placeholder="Phone (blank = default in w.env)"
              aria-label="WhatsApp recipient phone number"
            />
          )}
        </div>

        <div className="row row--tight" style={{ alignItems: "center", justifyContent: "space-between" }}>
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => handleSend()}
            disabled={sending || !connected}
          >
            {sending ? "Sending to monitor…" : "Send Watch Reading & Check Notification"}
          </button>
          <span className="small muted">
            Sends to backend, updates Doctor Ward &amp; Report
          </span>
        </div>

        {error && (
          <div className="notice notice--warn" role="alert">
            {error}
          </div>
        )}

        {lastResult && (
          <div
            className={`notice ${
              lastResult.suppressed || lastResult.alert_kind !== "CLINICAL"
                ? ""
                : lastResult.tier === "NORMAL"
                  ? ""
                  : "notice--warn"
            }`}
          >
            <strong>
              {lastResult.suppressed || lastResult.alert_kind !== "CLINICAL"
                ? `Artifact Suppressed (${lastResult.alert_kind})`
                : `Notification Result: ${TIER_LABEL[lastResult.tier]}`}
            </strong>{" "}
            —{" "}
            {lastResult.suppressed || lastResult.alert_kind !== "CLINICAL"
              ? lastResult.artifact.reasons.join("; ") || "Sensor issue detected; clinical alert withheld."
              : lastResult.explanation?.narrative ??
                `NEWS2 score ${lastResult.news2?.total ?? "—"} (${lastResult.news2?.risk_level ?? "scored"}).`}
            {lastResult.whatsapp && (
              <div className="small" style={{ marginTop: 6, fontWeight: 600 }}>
                {lastResult.whatsapp.queued
                  ? "Sending WhatsApp alert..."
                  : lastResult.whatsapp.sent
                    ? `WhatsApp alert sent${lastResult.whatsapp.dry_run ? " (dry-run)" : ""} to ${lastResult.whatsapp.recipients?.join(", ")}.`
                    : `WhatsApp failed: ${lastResult.whatsapp.error ?? "unknown error"}`}
              </div>
            )}
          </div>
        )}

        {xgbComp && (
          <div className="panel" style={{ padding: 12, marginTop: 10, background: "var(--surface-2, #f8fafc)" }}>
            <div className="row" style={{ justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <strong style={{ fontSize: 13 }}>Dual-Engine Risk Analysis (Without XGBoost vs With XGBoost)</strong>
              <span className="pill">{xgbComp.agreement}{xgbComp.score_delta != null ? ` · Δ ${xgbComp.score_delta} pts` : ""}</span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <div className="panel" style={{ padding: 10 }}>
                <div className="small muted">Without XGBoost (NEWS2 + Baseline)</div>
                <div style={{ fontSize: 16, fontWeight: 700, marginTop: 2 }}>
                  {xgbComp.without_xgboost.tier} · {xgbComp.without_xgboost.risk_score}/100
                </div>
                <div className="small muted">
                  NEWS2 Total: {xgbComp.without_xgboost.news2_total ?? "—"}
                </div>
              </div>
              <div className="panel" style={{ padding: 10 }}>
                <div className="small muted">With XGBoost (Trained Model)</div>
                {xgbComp.with_xgboost ? (
                  <>
                    <div style={{ fontSize: 16, fontWeight: 700, marginTop: 2 }}>
                      {xgbComp.with_xgboost.tier} · {xgbComp.with_xgboost.risk_score}/100
                    </div>
                    <div className="small muted">
                      Confidence: {Math.round(xgbComp.with_xgboost.confidence * 100)}%
                    </div>
                  </>
                ) : (
                  <div className="small muted">Model bundle not loaded</div>
                )}
              </div>
            </div>
            <div className="small muted" style={{ marginTop: 8 }}>
              <strong>Should we use both?</strong> {xgbComp.should_use_both}
            </div>
          </div>
        )}

        <div className="panel" style={{ padding: 12, marginTop: 10 }}>
          <div className="small" style={{ fontWeight: 700, marginBottom: 6 }}>
            Ask a Doubt (Groq AI · Tailored to {state.profile.display_name} &amp; Sent to WhatsApp)
          </div>
          <div className="row row--tight">
            <input
              type="text"
              className="input"
              style={{ flex: 1, minWidth: 180 }}
              placeholder="e.g. Why is my heart rate elevated right now?"
              value={doubtQ}
              onChange={(e) => setDoubtQ(e.target.value)}
            />
            <button
              type="button"
              className="btn btn--small btn--primary"
              disabled={doubtSending || !doubtQ.trim()}
              onClick={async () => {
                setDoubtSending(true);
                setDoubtAns("Asking Groq AI and sending reply to WhatsApp...");
                try {
                  const res = await clinicalApi.askWhatsapp({
                    question: doubtQ.trim(),
                    send_to_whatsapp: true,
                    whatsapp_to: whatsappTo.trim() || null,
                    patient_id: patientId,
                  });
                  setDoubtAns(res.answer);
                  setDoubtQ("");
                } catch (e) {
                  setDoubtAns(e instanceof Error ? e.message : "Failed to answer doubt");
                } finally {
                  setDoubtSending(false);
                }
              }}
            >
              {doubtSending ? "Asking..." : "Ask & Send to WhatsApp"}
            </button>
          </div>
          {doubtAns && (
            <div className="notice" style={{ marginTop: 8, whiteSpace: "pre-wrap" }}>
              {doubtAns}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
