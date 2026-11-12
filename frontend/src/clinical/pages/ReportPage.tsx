import { useCallback, useEffect, useState } from "react";
import { clinicalApi } from "../api";
import type { PerformanceReport, ScenarioEvaluation } from "../types";
import {
  ARTIFACT_LABEL,
  Empty,
  Metric,
  Panel,
  SCENARIO_LABEL,
  TierBadge,
  hours,
  num,
} from "../ui";

function ComparisonRow({ e }: { e: ScenarioEvaluation }) {
  const better =
    e.proposed.lead_time_hours !== null &&
    e.naive.lead_time_hours !== null &&
    e.proposed.lead_time_hours > e.naive.lead_time_hours;
  return (
    <tr>
      <td>
        <div style={{ fontWeight: 620 }}>{e.display_name}</div>
        <div className="small muted">{SCENARIO_LABEL[e.scenario] ?? e.scenario}</div>
      </td>
      <td className="num muted">{hours(e.onset_hours)}</td>
      <td className="num muted">{hours(e.severity_at_hours)}</td>
      <td className="num">
        <strong>{hours(e.proposed.first_alert_hours)}</strong>
      </td>
      <td className="num">{hours(e.proposed.detection_delay_hours)}</td>
      <td className="num">
        <strong style={{ color: better ? "var(--ok)" : undefined }}>
          {hours(e.proposed.lead_time_hours)}
        </strong>
      </td>
      <td className="num">{e.proposed.alerts}</td>
      <td className="num">{num(e.proposed.alerts_per_patient_day, 2)}</td>
      <td className="num">
        {e.proposed.false_alerts === 0 ? (
          <span className="chip chip--beta">0</span>
        ) : (
          e.proposed.false_alerts
        )}
      </td>
      <td className="num muted">{hours(e.naive.first_alert_hours)}</td>
      <td className="num muted">{hours(e.naive.lead_time_hours)}</td>
      <td className="num muted">{e.naive.alerts}</td>
      <td className="num muted">{num(e.naive.alerts_per_patient_day, 2)}</td>
      <td className="num">
        {e.naive.false_alerts > 0 ? (
          <span className="chip chip--event">{e.naive.false_alerts}</span>
        ) : (
          <span className="muted">0</span>
        )}
      </td>
      <td>
        <TierBadge tier={e.proposed.max_tier} />
        {e.naive.alerting_before_onset && (
          <div className="small muted" style={{ marginTop: 4 }}>
            naive alerted before onset
          </div>
        )}
      </td>
    </tr>
  );
}

export function ReportPage() {
  const [report, setReport] = useState<PerformanceReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback((refresh: boolean) => {
    setBusy(true);
    setError(null);
    clinicalApi
      .report(refresh)
      .then(setReport)
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  }, []);

  useEffect(() => load(false), [load]);

  if (error) {
    return (
      <Panel className="error">
        <strong>Could not build the performance report.</strong> {error}
        <div style={{ marginTop: 10 }}>
          <button className="btn btn--small" onClick={() => load(true)} disabled={busy}>
            Retry
          </button>
        </div>
      </Panel>
    );
  }
  if (!report) return <div className="skeleton">Replaying every scenario through both monitors…</div>;

  const t = report.totals;
  const artifacts = Object.entries(t.artifact_counts);

  return (
    <div className="col">
      <div className="spread">
        <div>
          <h1 className="section-title">Performance report</h1>
          <p className="lede">
            Both systems replayed the same seeded readings. The comparator is naive
            fixed-threshold monitoring: one set of population thresholds for every
            patient, no personalization, no baseline, no artifact handling.
          </p>
        </div>
        <button className="btn btn--small" onClick={() => load(true)} disabled={busy}>
          {busy ? "Recomputing…" : "Recompute"}
        </button>
      </div>

      <div className="notice notice--warn">
        Deterministic scripted benchmark, not clinical validation. Timing averages use
        each system’s detected cases, which may differ; compare individual patient
        rows for a paired result. BIDMC recordings are evaluated separately.
      </div>

      <div className="metrics">
        <Metric
          label="Alerts per patient-day"
          value={num(t.proposed_alerts_per_patient_day, 2)}
          delta={
            t.alert_reduction_percent !== null
              ? `${Math.abs(t.alert_reduction_percent)}% ${t.alert_reduction_percent >= 0 ? "fewer" : "more"} than naive (${num(t.naive_alerts_per_patient_day, 2)})`
              : undefined
          }
          good={(t.alert_reduction_percent ?? 0) > 0}
          note={`${t.proposed_alerts} vs ${t.naive_alerts} alerts over ${num(t.patient_days, 2)} patient-days`}
        />
        <Metric
          label="False alerts"
          value={t.proposed_false_alerts}
          delta={`${t.naive_false_alerts} raised by naive monitoring`}
          good={t.proposed_false_alerts <= t.naive_false_alerts}
          note={
            t.false_alert_reduction_percent !== null
              ? `${t.false_alert_reduction_percent}% reduction`
              : undefined
          }
        />
        <Metric
          label="Lead time before severity"
          value={num(t.mean_lead_time_hours, 1)}
          unit="h"
          delta={`naive ${num(t.naive_mean_lead_time_hours, 1)} h`}
          good={(t.mean_lead_time_hours ?? 0) >= (t.naive_mean_lead_time_hours ?? 0)}
          note="mean across scenarios with ground-truth onset"
        />
        <Metric
          label="Detection delay after onset"
          value={num(t.mean_detection_delay_hours, 1)}
          unit="h"
          delta={`naive ${num(t.naive_mean_detection_delay_hours, 1)} h`}
          good={(t.mean_detection_delay_hours ?? 99) <= (t.naive_mean_detection_delay_hours ?? 0)}
          note="lower is better"
        />
        <Metric
          label="Readings suppressed"
          value={t.suppressed_readings}
          delta="sensor faults, not clinical alerts"
          good
          note={`${artifacts.reduce((s, [, n]) => s + n, 0)} artifact findings across ${t.readings} readings`}
        />
      </div>

      <Panel
        title="Scenario comparison"
        hint="proposed system against naive fixed thresholds"
        flush
      >
        <div style={{ overflowX: "auto" }}>
          <table className="table table--compare">
            <thead>
              <tr>
                <th scope="col" rowSpan={2}>
                  Patient
                </th>
                <th scope="col" rowSpan={2} className="num">
                  Onset
                </th>
                <th scope="col" rowSpan={2} className="num">
                  Became severe
                </th>
                <th scope="col" colSpan={6} style={{ textAlign: "center", color: "var(--signal)" }}>
                  Proposed — personalized
                </th>
                <th scope="col" colSpan={5} style={{ textAlign: "center" }}>
                  Naive — fixed thresholds
                </th>
                <th scope="col" rowSpan={2}>
                  Outcome
                </th>
              </tr>
              <tr>
                <th scope="col" className="num">First alert</th>
                <th scope="col" className="num">Delay</th>
                <th scope="col" className="num">Lead time</th>
                <th scope="col" className="num">Alerts</th>
                <th scope="col" className="num">Per day</th>
                <th scope="col" className="num">False</th>
                <th scope="col" className="num">First alert</th>
                <th scope="col" className="num">Lead time</th>
                <th scope="col" className="num">Alerts</th>
                <th scope="col" className="num">Per day</th>
                <th scope="col" className="num">False</th>
              </tr>
            </thead>
            <tbody>
              {report.scenarios.map((e) => (
                <ComparisonRow e={e} key={e.patient_id} />
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <div className="grid grid--2">
        <Panel title="Artifact suppression" hint="what was withheld, and why">
          {artifacts.length === 0 ? (
            <Empty>No sensor artifacts were detected in these scenarios.</Empty>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Artifact</th>
                  <th scope="col" className="num">
                    Readings
                  </th>
                  <th scope="col">Effect</th>
                </tr>
              </thead>
              <tbody>
                {artifacts.map(([kind, count]) => (
                  <tr key={kind}>
                    <td>
                      <span className="chip chip--sensor">{ARTIFACT_LABEL[kind] ?? kind}</span>
                    </td>
                    <td className="num">{count}</td>
                    <td className="small muted">
                      {kind === "DROPOUT"
                        ? "device connectivity alert, not clinical"
                        : "untrusted metrics withheld from NEWS2"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="small muted" style={{ marginTop: 10 }}>
            Suppressed findings are still reported to the clinician as device issues —
            they never silently disappear.
          </p>
        </Panel>

        <Panel title="How the numbers are defined" hint="so the comparison is auditable">
          <ul className="list">
            <li>
              <span>
                <strong>Alert unit:</strong> an episode — a transition from not-alerting
                to alerting. Identical rule for both systems.
              </span>
            </li>
            <li>
              <span>
                <strong>Detection delay:</strong> first alert minus ground-truth
                deterioration onset. Lower is better.
              </span>
            </li>
            <li>
              <span>
                <strong>Lead time:</strong> the severity milestone minus first alert —
                how long <em>before the patient became more severe</em> the system
                flagged them. The milestone is the first personalized NEWS2 aggregate
                ≥ 5. Higher is better.
              </span>
            </li>
            <li>
              <span>
                <strong>False alert:</strong> a clinical alert before ground-truth onset,
                or any clinical alert in a scenario where the patient never deteriorates.
              </span>
            </li>
            <li>
              <span>
                <strong>Invalid naive detection:</strong> a naive monitor already alerting
                before onset is counted as a false alert, not as early detection.
              </span>
            </li>
          </ul>
          <div className="evidence__block" style={{ marginTop: 10 }}>
            <div className="evidence__label">Naive thresholds applied to every patient</div>
            <div className="row row--tight" style={{ flexWrap: "wrap" }}>
              {Object.entries(
                (report.method.naive_thresholds ?? {}) as Record<string, string>,
              ).map(([metric, rule]) => (
                <span className="code" key={metric}>
                  {metric.replace(/_/g, " ")} {rule}
                </span>
              ))}
            </div>
          </div>
        </Panel>
      </div>

      {report.recorded.length > 0 && (
        <Panel
          title="Recorded data — BIDMC PhysioNet"
          hint="reported separately: an 8-minute recording is not extrapolated to a per-day rate"
        >
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Patient</th>
                <th scope="col" className="num">
                  Span
                </th>
                <th scope="col" className="num">
                  Readings
                </th>
                <th scope="col" className="num">
                  Proposed alerts
                </th>
                <th scope="col" className="num">
                  Suppressed
                </th>
                <th scope="col" className="num">
                  Naive alerts
                </th>
                <th scope="col">Highest tier</th>
              </tr>
            </thead>
            <tbody>
              {report.recorded.map((e) => (
                <tr key={e.patient_id}>
                  <td>{e.display_name}</td>
                  <td className="num">{hours(e.span_hours, 2)}</td>
                  <td className="num">{e.readings}</td>
                  <td className="num">{e.proposed.alerts}</td>
                  <td className="num">{e.proposed.suppressed}</td>
                  <td className="num muted">{e.naive.alerts}</td>
                  <td>
                    <TierBadge tier={e.proposed.max_tier} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="small muted" style={{ marginTop: 10 }}>
            BIDMC supplies heart rate, respiratory rate and SpO₂ only. Body temperature
            and blood pressure come from the virtual wearable, as PS-03 §3.2A requires.
          </p>
        </Panel>
      )}

      {report.errors.length > 0 && (
        <Panel className="error" title="Skipped">
          <ul className="list">
            {report.errors.map((e) => (
              <li key={e.patient_id}>
                <span>
                  <span className="code">{e.patient_id}</span> — {e.detail}
                </span>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <p className="small muted">
        Generated {report.generated_at} · {t.readings} readings across {t.patients} scripted
        scenarios. Deterministic: the same seeds produce the same report.
      </p>
    </div>
  );
}