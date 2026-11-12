import { LOINC, METRIC_ORDER } from "./api";
import type {
  ArtifactVerdict,
  DischargeProfile,
  DriftFinding,
  News2Result,
  Observation,
} from "./types";
import { ARTIFACT_LABEL, Empty, Panel, Score, num } from "./ui";

/** Current vitals, with untrusted channels visibly withheld rather than scored. */
export function VitalsStrip({
  observation,
  untrusted,
}: {
  observation: Observation | null;
  untrusted: string[];
}) {
  if (!observation) return <Empty>No reading received yet.</Empty>;
  return (
    <div className="vitals">
      {METRIC_ORDER.map((metric) => {
        const meta = LOINC[metric];
        const value = observation[metric];
        const bad = untrusted.includes(metric);
        const absent = value === null || value === undefined;
        return (
          <div
            key={metric}
            className={`vital${bad ? " vital--untrusted" : ""}${absent ? " vital--absent" : ""}`}
          >
            <div className="vital__label">
              {meta.label}
              <span className="code" style={{ fontSize: 9.5, padding: "0 3px" }}>
                {meta.code}
              </span>
            </div>
            <div className="vital__value">
              {absent ? "—" : num(value, metric === "temperature" ? 2 : 1)}
              {!absent && <span className="vital__unit">{meta.unit}</span>}
            </div>
            <div className={`vital__note${bad ? " vital__note--warn" : ""}`}>
              {bad
                ? "withheld — sensor fault"
                : absent
                  ? "not reported"
                  : `LOINC ${meta.code}`}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** The full NEWS2 parameter table: every band and every point, auditable. */
export function News2Breakdown({
  news2,
  profile,
  observation,
  suppressed,
}: {
  news2: News2Result | null;
  profile: DischargeProfile;
  observation: Observation | null;
  suppressed: boolean;
}) {
  if (!news2) {
    return (
      <Panel title="NEWS2 scoring">
        <Empty>
          {!observation
            ? "No reading received yet — NEWS2 has not been computed."
            : suppressed
              ? "Not scored — clinical scoring was withheld because the signal was unreliable."
              : "No NEWS2 result available for the latest reading."}
        </Empty>
      </Panel>
    );
  }
  return (
    <Panel
      title="NEWS2 scoring"
      hint={
        <>
          SpO₂ Scale {news2.spo2_scale}
          {news2.spo2_scale === 2 && " (target 88–92%)"}
          {news2.single_parameter_three && " · single parameter 3"}
          {!news2.complete && " · incomplete inputs"}
        </>
      }
      action={
        <span className="row row--tight" style={{ alignItems: "center" }}>
          <Score value={news2.total} big />
          <span className="small muted">{news2.risk_level}</span>
        </span>
      }
    >
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Parameter</th>
            <th scope="col">Reading</th>
            <th scope="col">Band</th>
            <th scope="col" className="num">
              Points
            </th>
          </tr>
        </thead>
        <tbody>
          {news2.parameters.map((p) => {
            const corrected =
              p.parameter === "heart_rate" && profile.beta_blocked
                ? profile.pulse_correction_bpm
                : 0;
            return (
              <tr key={p.parameter}>
                <td>
                  {p.label}
                  {p.scale === 2 && <span className="chip chip--scale2" style={{ marginLeft: 6 }}>Scale 2</span>}
                </td>
                <td className="mono nowrap">
                  {p.value === null ? "—" : `${p.value}${p.unit ? ` ${p.unit}` : ""}`}
                  {corrected > 0 && (
                    <span className="muted small"> (incl. +{corrected} β-correction)</span>
                  )}
                </td>
                <td className="muted">{p.band}</td>
                <td className="num">
                  <Score value={p.score} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {news2.missing.length > 0 && (
        <p className="notice notice--warn" style={{ marginTop: 10 }}>
          Missing parameters reported, never imputed:{" "}
          {news2.missing.map((m) => LOINC[m]?.label ?? m).join(", ")}.
        </p>
      )}
      {!news2.parameters.some((p) => p.parameter === "systolic_bp") && (
        <p className="small muted" style={{ marginTop: 10 }}>
          Systolic BP is unavailable and is not included in this total.
        </p>
      )}
      <p className="small muted" style={{ marginTop: 10 }}>
        Consciousness “Alert” and no supplemental oxygen are monitoring-service
        defaults, not wearable measurements. Review these inputs clinically.
      </p>
    </Panel>
  );
}

/** Conditions, medications and the personalization rules they trigger (§3.3). */
export function ProfilePanel({ profile }: { profile: DischargeProfile }) {
  return (
    <Panel
      title="Discharge profile"
      hint={
        <>
          {profile.source} · discharged {profile.discharged_on}
        </>
      }
    >
      <div className="col" style={{ gap: 14 }}>
        <div>
          <div className="panel__title" style={{ marginBottom: 6 }}>
            Conditions
          </div>
          <ul className="list list--codes">
            {profile.conditions.map((c) => (
              <li key={c.code}>
                <span>
                  {c.display} <span className="code">SNOMED {c.code}</span>
                </span>
              </li>
            ))}
            {profile.conditions.length === 0 && <li className="muted">None recorded</li>}
          </ul>
        </div>
        <div>
          <div className="panel__title" style={{ marginBottom: 6 }}>
            Medications
          </div>
          <ul className="list list--codes">
            {profile.medications.map((m) => (
              <li key={m.code}>
                <span>
                  {m.display} <span className="code">RxNorm {m.code}</span>
                </span>
              </li>
            ))}
            {profile.medications.length === 0 && <li className="muted">None recorded</li>}
          </ul>
        </div>
        <div>
          <div className="panel__title" style={{ marginBottom: 6 }}>
            Personalization applied
          </div>
          <div className="col" style={{ gap: 8 }}>
            {profile.rules.map((rule) => (
              <div className="evidence__block" key={rule.rule_id}>
                <div className="evidence__rule-title" style={{ fontSize: 13 }}>
                  {rule.title}
                </div>
                <div className="evidence__fact" style={{ margin: "3px 0" }}>
                  {rule.profile_fact}
                </div>
                <div className="evidence__effect small">{rule.effect}</div>
              </div>
            ))}
            {profile.rules.length === 0 && <p className="small muted">No personalization rules recorded.</p>}
          </div>
        </div>
        {profile.notes && <p className="small muted">{profile.notes}</p>}
      </div>
    </Panel>
  );
}

/** Gradual drift against the patient's own rolling baseline (§3.4 rule 2). */
export function DriftPanel({
  drift,
  baselines,
  ready,
}: {
  drift: DriftFinding[];
  baselines: Record<string, { center: number; spread: number }>;
  ready: boolean;
}) {
  const metrics = Object.keys(baselines);
  return (
    <Panel
      title="Rolling baseline drift"
      hint={ready ? "baseline established" : "learning — needs more history"}
    >
      {drift.length > 0 && (
        <div className="notice notice--warn" style={{ marginBottom: 10 }}>
          Sustained movement away from this patient's rolling baseline. This can
          trigger escalation independently of population NEWS2 bands.
        </div>
      )}
      {metrics.length === 0 ? (
        <Empty>
          The baseline needs at least 12 readings older than two hours. It excludes
          recent readings so an ongoing deterioration cannot drag its own baseline.
        </Empty>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Vital</th>
              <th scope="col" className="num">
                Usual
              </th>
              <th scope="col" className="num">
                Spread
              </th>
              <th scope="col" className="num">
                Now
              </th>
              <th scope="col" className="num">
                Robust z
              </th>
              <th scope="col" className="num">
                Sustained
              </th>
            </tr>
          </thead>
          <tbody>
            {metrics.map((metric) => {
              const band = baselines[metric];
              const finding = drift.find((d) => d.metric === metric);
              return (
                <tr key={metric} className={finding ? "table__row--highlight" : undefined}>
                  <td>{LOINC[metric]?.label ?? metric}</td>
                  <td className="num">{num(band.center, 2)}</td>
                  <td className="num muted">{num(band.spread, 2)}</td>
                  <td className="num">{finding ? num(finding.value, 2) : "—"}</td>
                  <td className="num">
                    {finding ? (
                      <strong style={{ color: "var(--review)" }}>{num(finding.robust_z, 1)}</strong>
                    ) : (
                      <span className="muted" title="No current z-score supplied without a sustained finding">—</span>
                    )}
                  </td>
                  <td className="num muted">
                    {finding ? `${finding.sustained} readings` : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {metrics.length > 0 && (
        <p className="small muted" style={{ marginTop: 10 }}>
          {drift.length === 0 && "No sustained drift finding reported. "}
          Current values, z-scores and streaks are supplied only for sustained findings;
          a dash does not mean the z-score is below a threshold.
        </p>
      )}
    </Panel>
  );
}

/** Sensor findings, kept visible so the device gets attention (§3.5). */
export function SensorPanel({
  artifact,
  suppressedCount,
  artifactCounts,
}: {
  artifact: ArtifactVerdict | null;
  suppressedCount: number;
  artifactCounts: Record<string, number>;
}) {
  const kinds = Object.entries(artifactCounts);
  return (
    <Panel
      title="Sensor reliability"
      hint={`${suppressedCount} reading${suppressedCount === 1 ? "" : "s"} withheld`}
    >
      {artifact?.is_artifact ? (
        <div className="notice" style={{ marginBottom: 10 }}>
          <strong>Clinical alert suppressed.</strong>
          <ul className="list" style={{ marginTop: 6 }}>
            {artifact.reasons.map((reason, i) => (
              <li key={i}>{reason}</li>
            ))}
          </ul>
          {artifact.untrusted_metrics.length > 0 && (
            <p className="small" style={{ marginTop: 6 }}>
              Withheld from scoring:{" "}
              {artifact.untrusted_metrics.map((m) => LOINC[m]?.label ?? m).join(", ")}.
            </p>
          )}
        </div>
      ) : artifact ? (
        <p className="small muted" style={{ marginBottom: kinds.length ? 10 : 0 }}>
          No sensor artifact detected in the latest assessment.
        </p>
      ) : (
        <Empty>No sensor reliability assessment available yet.</Empty>
      )}
      {kinds.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Artifact detected</th>
              <th scope="col" className="num">
                Readings
              </th>
            </tr>
          </thead>
          <tbody>
            {kinds.map(([kind, count]) => (
              <tr key={kind}>
                <td>
                  <span className="chip chip--sensor">{ARTIFACT_LABEL[kind] ?? kind}</span>
                </td>
                <td className="num">{count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}