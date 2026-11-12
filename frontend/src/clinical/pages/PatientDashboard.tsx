import { useEffect, useState } from "react";
import { ClinicalTwin3D, type WatchLiveOverride } from "../ClinicalTwin3D";
import { SmartwatchSimulator } from "../SmartwatchSimulator";
import { clinicalApi } from "../api";
import { usePatient } from "../hooks";
import { VitalsStrip } from "../PatientPanels";
import { ReportModal } from "../ReportModal";
import { buildPatientHealthReport, type GeneratedReport } from "../reportGenerator";
import type { FhirObservation } from "../types";
import { VitalsCharts } from "../VitalsCharts";
import { Empty, Panel, TIER_LABEL, dateTime } from "../ui";

function patientStatusCopy(
  hasTimestamp: boolean,
  suppressed: boolean,
  tier: string,
  alertKind: string | null,
): { badge: string; tone: "ok" | "watch" | "review" | "urgent" | "sensor"; message: string } {
  if (!hasTimestamp) {
    return {
      badge: "Waiting for sensor",
      tone: "sensor",
      message:
        "Your wearable monitor has not sent a reading yet. Once your care team or simulator starts streaming, your vitals will appear here automatically.",
    };
  }
  if (suppressed || (alertKind && alertKind !== "CLINICAL")) {
    return {
      badge: "Check wearable sensor",
      tone: "sensor",
      message:
        "Your last reading looked noisy or disconnected (for example, a loose pulse oximeter). Please check that your sensor is snug and charged.",
    };
  }
  if (tier === "NORMAL") {
    return {
      badge: "Recovery steady",
      tone: "ok",
      message:
        "Your recent vital signs are within your personalized post-discharge target range. Keep following your daily care plan.",
    };
  }
  if (tier === "WATCH") {
    return {
      badge: "Keep an eye on readings",
      tone: "watch",
      message:
        "A mild shift was noticed compared with your usual range. Rest, stay hydrated, and re-check your sensor readings.",
    };
  }
  if (tier === "REVIEW") {
    return {
      badge: "Check in with care team",
      tone: "review",
      message:
        "Your readings show a moderate change from your personal baseline. Please contact your post-discharge nurse or doctor today.",
    };
  }
  return {
    badge: "Contact care team promptly",
    tone: "urgent",
    message:
      "Your latest vital signs show a significant change. Please reach out to your care team promptly or seek urgent medical attention if you feel unwell.",
  };
}

export function PatientDashboard({ patientId }: { patientId: string }) {
  const { state, alerts, readings, connection, error } = usePatient(patientId);
  const [report, setReport] = useState<GeneratedReport | null>(null);
  const [streamingBusy, setStreamingBusy] = useState(false);
  const [fhirEntries, setFhirEntries] = useState<FhirObservation[]>([]);
  const [hapiHealthy, setHapiHealthy] = useState<boolean | null>(null);
  const [watchConnected, setWatchConnected] = useState(true);
  const [activeTab, setActiveTab] = useState<"graphs" | "body3d">("graphs");
  const [watchValues, setWatchValues] = useState<WatchLiveOverride>({
    heart_rate: 74,
    spo2: 97,
    respiratory_rate: 16,
    temperature: 36.7,
  });

  useEffect(() => {
    if (state?.observation && !watchConnected) {
      setWatchValues({
        heart_rate: Math.round(state.observation.heart_rate ?? 74),
        spo2: Math.round(state.observation.spo2 ?? 97),
        respiratory_rate: Math.round(state.observation.respiratory_rate ?? 16),
        temperature: Number((state.observation.temperature ?? 36.7).toFixed(1)),
      });
    }
  }, [state?.observation, watchConnected]);

  useEffect(() => {
    let cancelled = false;
    clinicalApi
      .hapiHealth()
      .then((h) => {
        if (!cancelled) setHapiHealthy(h.healthy);
      })
      .catch(() => {
        if (!cancelled) setHapiHealthy(false);
      });
    clinicalApi
      .hapiObservations(patientId, undefined, 8)
      .then((res) => {
        if (!cancelled) setFhirEntries(res.entries ?? []);
      })
      .catch(() => {
        if (!cancelled) setFhirEntries([]);
      });
    return () => {
      cancelled = true;
    };
  }, [patientId, state?.stored_observations]);

  const startMyStream = async () => {
    setStreamingBusy(true);
    try {
      await clinicalApi.replayStart([patientId]);
    } finally {
      setStreamingBusy(false);
    }
  };

  if (error && !state) {
    return (
      <Panel className="error">
        <strong>Could not load your health record.</strong> {error}
      </Panel>
    );
  }
  if (!state) return <div className="skeleton">Loading your dashboard…</div>;

  const p = state.profile;
  const untrusted = state.artifact?.untrusted_metrics ?? [];
  const hasBp = readings.some((r) => r.systolic_bp !== null && r.systolic_bp !== undefined);
  const status = patientStatusCopy(
    Boolean(state.timestamp),
    state.suppressed,
    state.tier,
    state.alert_kind,
  );

  const checkIns = alerts.filter((a) => a.alert_kind === "CLINICAL" && a.tier !== "NORMAL").slice(0, 8);
  const deviceNotices = alerts.filter((a) => a.alert_kind !== "CLINICAL").slice(0, 6);

  return (
    <div className="col patient-dash">
      <div className="spread">
        <div>
          <span className="chip">Patient Portal · {p.patient_id}</span>
          <h1 className="section-title" style={{ marginTop: 8 }}>
            Welcome back, {p.display_name}
          </h1>
          <p className="lede">
            {p.age_years} years · {p.sex} · Discharged {p.discharged_on}
          </p>
        </div>
        <div className="row row--tight" style={{ alignItems: "center" }}>
          <span className={`pill pill--${connection === "live" ? "live" : "off"}`}>
            <span className="pill__dot" aria-hidden />
            {connection === "live" ? "Live monitor" : "Reconnecting"}
          </span>
          {!state.timestamp && (
            <button
              className="btn"
              onClick={startMyStream}
              disabled={streamingBusy}
            >
              {streamingBusy ? "Starting stream…" : "Start Wearable Stream"}
            </button>
          )}
          <button
            className="btn"
            onClick={() => setWatchConnected((v) => !v)}
          >
            {watchConnected ? "Watch Connected ✓" : "Connect Your Watch"}
          </button>
          <button
            className="btn btn--primary"
            onClick={() =>
              setReport(
                buildPatientHealthReport(
                  state,
                  alerts,
                  readings,
                  watchConnected ? watchValues : null,
                ),
              )
            }
          >
            Generate My Health Report
          </button>
        </div>
      </div>

      <section className={`patient-banner patient-banner--${status.tone}`}>
        <div className="spread">
          <div>
            <div className="patient-banner__badge">{status.badge}</div>
            <p className="patient-banner__msg">{status.message}</p>
          </div>
          <div className="small muted" style={{ textAlign: "right" }}>
            {state.timestamp ? (
              <>
                Last reading
                <br />
                <strong>{dateTime(state.timestamp)} UTC</strong>
              </>
            ) : (
              "No readings yet"
            )}
          </div>
        </div>
      </section>

      <div className="metrics">
        <div className="metric">
          <div className="metric__label">Personal SpO₂ Target</div>
          <div className="metric__value">
            {p.spo2_scale === 2 ? `${p.spo2_target[0]}–${p.spo2_target[1]}%` : "96–100%"}
          </div>
          <div className="metric__note">
            {p.spo2_scale === 2
              ? "Tailored Scale 2 oxygen target for your breathing plan"
              : "Standard oxygen saturation target"}
          </div>
        </div>
        <div className="metric">
          <div className="metric__label">Medication Adjustment</div>
          <div className="metric__value">{p.beta_blocked ? "Beta-blocker" : "Standard"}</div>
          <div className="metric__note">
            {p.beta_blocked
              ? `Pulse alerts adjusted (+${p.pulse_correction_bpm} bpm) for your heart medication`
              : "No heart-rate medication offset applied"}
          </div>
        </div>
        <div className="metric">
          <div className="metric__label">Readings Tracked</div>
          <div className="metric__value">{readings.length}</div>
          <div className="metric__note">
            {state.baseline_ready
              ? "Personal baseline active"
              : "Learning your normal resting baseline"}
          </div>
        </div>
        <div className="metric">
          <div className="metric__label">Wearable Health</div>
          <div className="metric__value">{state.suppressed_count}</div>
          <div className="metric__note">Noisy / loose-sensor readings filtered out</div>
        </div>
      </div>

      <div className="row row--tight" role="tablist" aria-label="Patient dashboard views">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "graphs"}
          className={`btn ${activeTab === "graphs" ? "btn--primary" : ""}`}
          onClick={() => setActiveTab("graphs")}
        >
          Smartwatch &amp; Vital Graphs
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "body3d"}
          className={`btn ${activeTab === "body3d" ? "btn--primary" : ""}`}
          onClick={() => setActiveTab("body3d")}
        >
          Open 3D Body Twin
        </button>
      </div>

      {activeTab === "body3d" ? (
        <Panel
          title="My 3D Digital Twin"
          hint="Live anatomical model driven by your smartwatch / wearable vitals (amber/red highlights show stressed organs)"
          action={
            <button className="btn btn--small" onClick={() => setActiveTab("graphs")}>
              ← Back to Watch &amp; Graphs
            </button>
          }
        >
          <ClinicalTwin3D
            state={state}
            liveOverride={watchConnected ? watchValues : null}
            height="540px"
          />
        </Panel>
      ) : (
        <>
          <Panel
            title="Wearable Smartwatch on Wrist (Live Demo)"
            hint="Adjust vitals on your watch and send readings to trigger notifications & update graphs"
          >
            <SmartwatchSimulator
              patientId={patientId}
              state={state}
              connected={watchConnected}
              onToggleConnect={() => setWatchConnected((v) => !v)}
              values={watchValues}
              onChangeValues={setWatchValues}
            />
          </Panel>

          <Panel
            title="My Vital Trend Graphs"
            hint={`${readings.length} readings · shaded band shows your personal resting baseline`}
          >
            {readings.length === 0 ? (
              <Empty>
                No trend history yet. Click &ldquo;Send Watch Reading&rdquo; above or &ldquo;Start Wearable Stream&rdquo; to plot live graphs.
              </Empty>
            ) : (
              <VitalsCharts
                readings={readings}
                baselines={state.baselines ?? {}}
                untrustedMetrics={untrusted}
                includeBp={hasBp}
              />
            )}
          </Panel>

          <Panel
            title="My Latest Vitals"
            hint={state.timestamp ? `Recorded ${dateTime(state.timestamp)} UTC` : "Waiting for first reading"}
          >
            <VitalsStrip observation={state.observation} untrusted={untrusted} />
          </Panel>

          <div className="grid grid--sidebar">
            <div className="col">
              <Panel
                title="Health Check-In Notices"
                hint="Plain-language updates when your vitals shift"
              >
                {checkIns.length === 0 ? (
                  <Empty>No check-in alerts right now. Your readings look steady.</Empty>
                ) : (
                  <div className="col" style={{ gap: 10 }}>
                    {checkIns.map((a) => (
                      <div className="evidence__block" key={`${a.timestamp}-${a.tier}`}>
                        <div className="spread">
                          <strong>{TIER_LABEL[a.tier]}</strong>
                          <span className="small muted">{dateTime(a.timestamp)} UTC</span>
                        </div>
                        <p className="small" style={{ margin: "6px 0 0" }}>
                          {a.explanation?.narrative ?? "A change from your usual vital range was detected."}
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </Panel>
            </div>

            <div className="col">
              <Panel title="My Care Plan & Medications">
                <div className="col" style={{ gap: 12 }}>
                  <div>
                    <div className="small muted" style={{ fontWeight: 600, marginBottom: 4 }}>
                      Conditions on discharge summary
                    </div>
                    {p.conditions.length === 0 ? (
                      <p className="small muted">None listed</p>
                    ) : (
                      <ul className="patient-list">
                        {p.conditions.map((c) => (
                          <li key={c.code}>{c.display}</li>
                        ))}
                      </ul>
                    )}
                  </div>

                  <div>
                    <div className="small muted" style={{ fontWeight: 600, marginBottom: 4 }}>
                      Active medications
                    </div>
                    {p.medications.length === 0 ? (
                      <p className="small muted">None listed</p>
                    ) : (
                      <ul className="patient-list">
                        {p.medications.map((m) => (
                          <li key={m.code}>{m.display}</li>
                        ))}
                      </ul>
                    )}
                  </div>

                  {p.notes && (
                    <div>
                      <div className="small muted" style={{ fontWeight: 600, marginBottom: 4 }}>
                        Care team notes
                      </div>
                      <p className="small" style={{ margin: 0 }}>
                        {p.notes}
                      </p>
                    </div>
                  )}
                </div>
              </Panel>

              <Panel title="Wearable Device Reminders" hint="Sensor fit & connectivity">
                {deviceNotices.length === 0 ? (
                  <Empty>Your wearable sensor connection looks good.</Empty>
                ) : (
                  <div className="col" style={{ gap: 8 }}>
                    {deviceNotices.map((a) => (
                      <div className="evidence__block" key={`${a.timestamp}-dev`}>
                        <div className="spread">
                          <span className="chip chip--sensor">Device check</span>
                          <span className="small muted">{dateTime(a.timestamp)} UTC</span>
                        </div>
                        <p className="small" style={{ margin: "6px 0 0" }}>
                          {a.artifact.reasons[0] ?? "Please check that your wearable sensor is snug."}
                        </p>
                      </div>
                    ))}
                  </div>
                )}
              </Panel>

              <Panel
                title="My FHIR R4 Health Records"
                hint={`${state.stored_observations} observations · HAPI ${hapiHealthy ? "connected" : "offline"}`}
              >
                {fhirEntries.length > 0 ? (
                  <div className="col" style={{ gap: 6 }}>
                    {fhirEntries.slice(0, 5).map((obs, idx) => (
                      <div className="evidence__block small" key={obs.id ?? idx}>
                        <div className="spread">
                          <strong>{obs.code?.text ?? obs.code?.coding?.[0]?.display ?? "Observation"}</strong>
                          <span className="muted">
                            {obs.valueQuantity ? `${obs.valueQuantity.value} ${obs.valueQuantity.unit ?? ""}` : "missing"}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="small muted" style={{ margin: 0 }}>
                    {hapiHealthy === false
                      ? "Local HAPI FHIR server (port 8080) is not running; readings are still tracked live in your monitor."
                      : "No FHIR observations stored for your record yet."}
                  </p>
                )}
              </Panel>
            </div>
          </div>
        </>
      )}

      {report && <ReportModal report={report} onClose={() => setReport(null)} />}
    </div>
  );
}
