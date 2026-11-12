import { clinicalToTwinPayload, type WatchLiveOverride } from "./ClinicalTwin3D";
import type { AuthSession } from "./auth";
import type { MonitorResult, Observation, PatientRow, PatientStatePayload } from "./types";
import { SCENARIO_LABEL, TIER_LABEL, dateTime } from "./ui";

function fmtMetric(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return "—";
  return digits > 0 ? value.toFixed(digits) : String(Math.round(value));
}

export interface ReportTable {
  headers: string[];
  rows: string[][];
}

export interface ReportSection {
  title: string;
  paragraphs?: string[];
  bullets?: string[];
  table?: ReportTable;
}

export interface ReportVisualContext {
  state: PatientStatePayload;
  alerts: MonitorResult[];
  readings: Observation[];
  liveOverride?: WatchLiveOverride | null;
  session?: Extract<AuthSession, { role: "doctor" }> | null;
}

export interface OrganFinding {
  organ: string;
  metricLabel: string;
  valueText: string;
  baselineText: string;
  status: "normal" | "watch" | "anomalous";
  summary: string;
}

export interface GeneratedReport {
  id: string;
  kind: "doctor-patient" | "doctor-ward" | "patient-summary";
  title: string;
  subtitle: string;
  generatedAt: string;
  authorLabel: string;
  filename: string;
  disclaimer: string;
  sections: ReportSection[];
  visualContext?: ReportVisualContext;
}

const DEFAULT_DISCLAIMER =
  "Clinical decision support, not medical diagnosis. Synthetic data only (with optional BIDMC recorded replay).";

function nowIso(): string {
  return new Date().toISOString();
}

export function reportToMarkdown(report: GeneratedReport): string {
  const lines: string[] = [
    `# ${report.title}`,
    `_${report.subtitle}_`,
    "",
    `- **Generated:** ${dateTime(report.generatedAt)} UTC`,
    `- **Prepared for / by:** ${report.authorLabel}`,
    "",
    `> **Safety notice:** ${report.disclaimer}`,
    "",
  ];

  for (const sec of report.sections) {
    lines.push(`## ${sec.title}`, "");
    if (sec.paragraphs) {
      for (const p of sec.paragraphs) {
        lines.push(p, "");
      }
    }
    if (sec.bullets && sec.bullets.length > 0) {
      for (const b of sec.bullets) {
        lines.push(`- ${b}`);
      }
      lines.push("");
    }
    if (sec.table && sec.table.rows.length > 0) {
      lines.push(`| ${sec.table.headers.join(" | ")} |`);
      lines.push(`| ${sec.table.headers.map(() => "---").join(" | ")} |`);
      for (const row of sec.table.rows) {
        lines.push(`| ${row.join(" | ")} |`);
      }
      lines.push("");
    }
  }
  return lines.join("\n");
}

export function buildDoctorPatientReport(
  state: PatientStatePayload,
  alerts: MonitorResult[],
  readings: Observation[],
  session?: Extract<AuthSession, { role: "doctor" }> | null,
): GeneratedReport {
  const p = state.profile;
  const generatedAt = nowIso();
  const clinician = session
    ? `${session.clinicianName} (${session.clinicianId})`
    : "Attending Clinician";

  const statusSummary = !state.timestamp
    ? "Awaiting first wearable/replay observation."
    : state.suppressed
      ? `Clinical scoring withheld due to sensor artifact (${state.alert_kind ?? "SENSOR"}).`
      : `Current Tier: ${TIER_LABEL[state.tier]} · NEWS2 Total: ${state.news2?.total ?? "—"} (${state.news2?.risk_level ?? "not scored"}).`;

  const vitalsRows: string[][] = [];
  if (state.news2?.parameters?.length) {
    for (const param of state.news2.parameters) {
      vitalsRows.push([
        param.label,
        param.value === null ? "missing (dataAbsentReason)" : `${param.value} ${param.unit}`.trim(),
        String(param.score),
        param.band,
      ]);
    }
  } else if (state.observation) {
    const o = state.observation;
    vitalsRows.push(
      ["Heart rate", `${fmtMetric(o.heart_rate)} bpm`, "—", "unscored"],
      ["SpO₂", `${fmtMetric(o.spo2)} %`, "—", "unscored"],
      ["Respiratory rate", `${fmtMetric(o.respiratory_rate)} br/min`, "—", "unscored"],
      ["Temperature", `${fmtMetric(o.temperature, 1)} °C`, "—", "unscored"],
    );
  }

  const clinicalAlerts = alerts.filter((a) => a.alert_kind === "CLINICAL" && a.tier !== "NORMAL").slice(0, 8);
  const sensorAlerts = alerts.filter((a) => a.alert_kind !== "CLINICAL").slice(0, 6);

  const sections: ReportSection[] = [
    {
      title: "Patient & Discharge Summary",
      bullets: [
        `Patient: ${p.display_name} (${p.patient_id})`,
        `Demographics: ${p.age_years} years · ${p.sex} · Discharged ${p.discharged_on}`,
        `Scenario: ${SCENARIO_LABEL[p.scenario] ?? p.scenario}`,
        `Status: ${statusSummary}`,
        `FHIR R4 Observations stored in HAPI: ${state.stored_observations} · Suppressed readings: ${state.suppressed_count}`,
      ],
    },
    {
      title: "Discharge Profile & Personalization Rules",
      bullets: [
        `Conditions: ${p.conditions.map((c) => c.display).join("; ") || "None documented"}`,
        `Medications: ${p.medications.map((m) => m.display).join("; ") || "None documented"}`,
        `SpO₂ Scoring Scale: Scale ${p.spo2_scale} (target ${p.spo2_target[0]}–${p.spo2_target[1]}%)`,
        `Beta-blocker pulse adjustment: ${p.beta_blocked ? `Active (+${p.pulse_correction_bpm} bpm correction)` : "Not active"}`,
        ...p.rules.map((r) => `Rule [${r.title}]: ${r.profile_fact} → ${r.effect}`),
      ],
      paragraphs: p.notes ? [`Clinical notes: ${p.notes}`] : undefined,
    },
    {
      title: "Latest Vitals & NEWS2 Breakdown",
      paragraphs: [
        state.timestamp
          ? `Latest observation recorded at ${dateTime(state.timestamp)} UTC (${readings.length} recent readings in window).`
          : "No observations recorded yet.",
      ],
      table: {
        headers: ["Parameter", "Reading", "NEWS2 pts", "Band"],
        rows: vitalsRows.length > 0 ? vitalsRows : [["—", "No readings yet", "—", "—"]],
      },
    },
    {
      title: "Rolling Baseline Drift",
      bullets:
        state.drift.length > 0
          ? state.drift.map(
              (d) =>
                `${d.label}: ${d.value} vs personal baseline ${d.baseline_center.toFixed(1)} (±${d.baseline_spread.toFixed(1)}), robust z=${d.robust_z.toFixed(2)}, sustained ${d.sustained} readings.`,
            )
          : [
              state.baseline_ready
                ? "No sustained drift from personal rolling baseline detected."
                : "Personal rolling baseline is still warming up.",
            ],
    },
    {
      title: "Recent Clinical Alerts",
      table: {
        headers: ["Timestamp (UTC)", "Tier", "NEWS2", "Explanation"],
        rows:
          clinicalAlerts.length > 0
            ? clinicalAlerts.map((a) => [
                dateTime(a.timestamp),
                TIER_LABEL[a.tier],
                a.news2 ? String(a.news2.total) : "—",
                a.explanation?.narrative ?? "Threshold / drift triggered.",
              ])
            : [["—", "None", "—", "No Watch/Review/Urgent clinical alerts in recent history."]],
      },
    },
    {
      title: "Sensor Artifact & Device Log",
      bullets:
        sensorAlerts.length > 0
          ? sensorAlerts.map(
              (a) =>
                `${dateTime(a.timestamp)} UTC — ${a.alert_kind}: ${a.artifact.reasons.join("; ") || "Unreliable signal suppressed."}`,
            )
          : ["No sensor artifacts or device-offline events in recent history."],
    },
  ];

  return {
    id: `doc-patient-${p.patient_id}-${generatedAt}`,
    kind: "doctor-patient",
    title: `Clinical Monitoring Report — ${p.display_name}`,
    subtitle: `Post-Discharge Deterioration Assessment (${p.patient_id})`,
    generatedAt,
    authorLabel: clinician,
    filename: `clinical-report-${p.patient_id}.md`,
    disclaimer: state.disclaimer || DEFAULT_DISCLAIMER,
    sections,
    visualContext: { state, alerts, readings, session },
  };
}

export function buildDoctorWardReport(
  rows: PatientRow[],
  session?: Extract<AuthSession, { role: "doctor" }> | null,
): GeneratedReport {
  const generatedAt = nowIso();
  const clinician = session
    ? `${session.clinicianName} (${session.clinicianId})`
    : "Attending Clinician";

  const counts = { URGENT: 0, REVIEW: 0, WATCH: 0, NORMAL: 0 };
  let deviceIssues = 0;
  let awaiting = 0;
  for (const r of rows) {
    if (!r.timestamp) {
      awaiting += 1;
    } else if (r.alert_kind && r.alert_kind !== "CLINICAL") {
      deviceIssues += 1;
    } else if (r.alert_kind === "CLINICAL" && !r.suppressed) {
      counts[r.tier] += 1;
    }
  }
  const totalStored = rows.reduce((s, r) => s + r.stored_observations, 0);
  const totalSuppressed = rows.reduce((s, r) => s + r.suppressed_count, 0);

  return {
    id: `doc-ward-${generatedAt}`,
    kind: "doctor-ward",
    title: "Post-Discharge Ward Census Report",
    subtitle: `${rows.length} monitored patients · Snapshot summary`,
    generatedAt,
    authorLabel: clinician,
    filename: "ward-census-report.md",
    disclaimer: DEFAULT_DISCLAIMER,
    sections: [
      {
        title: "Ward Census Overview",
        bullets: [
          `Urgent: ${counts.URGENT} · Review: ${counts.REVIEW} · Watch: ${counts.WATCH} · Normal: ${counts.NORMAL}`,
          `Device / Sensor issues: ${deviceIssues} · Awaiting first reading: ${awaiting}`,
          `Total FHIR R4 Observations stored: ${totalStored.toLocaleString()} · Suppressed artifacts: ${totalSuppressed}`,
        ],
      },
      {
        title: "Patient Roster",
        table: {
          headers: ["Patient", "Age/Sex", "Tier", "NEWS2", "SpO₂ Scale", "Alerts / Suppressed"],
          rows: rows.map((r) => [
            `${r.display_name} (${r.patient_id})`,
            `${r.age_years}y ${r.sex}`,
            !r.timestamp
              ? "Awaiting"
              : r.alert_kind && r.alert_kind !== "CLINICAL"
                ? r.alert_kind
                : TIER_LABEL[r.tier],
            r.news2_total !== null ? String(r.news2_total) : "—",
            `Scale ${r.spo2_scale}${r.beta_blocked ? " · BB" : ""}`,
            `${r.alert_count} / ${r.suppressed_count}`,
          ]),
        },
      },
    ],
  };
}

export function buildPatientHealthReport(
  state: PatientStatePayload,
  alerts: MonitorResult[],
  readings: Observation[],
  liveOverride?: WatchLiveOverride | null,
): GeneratedReport {
  const p = state.profile;
  const generatedAt = nowIso();
  const obs = liveOverride ?? state.observation;

  const statusText = liveOverride
    ? "Showing live readings from your connected smartwatch alongside your clinical history."
    : !state.timestamp
      ? "Your wearable monitor has not sent a reading yet."
      : state.suppressed
        ? "Your wearable sensor had a signal issue on the last reading. Please check that your sensor is snug and charged."
        : state.tier === "NORMAL"
          ? "Your recent vital signs look steady and within your expected post-discharge range."
          : state.tier === "WATCH"
            ? "A mild change was noted in your readings. Continue monitoring and follow your discharge care plan."
            : "Your readings show a noticeable change from your usual range. Please contact your care team for a check-in.";

  const targetSpo2 =
    p.spo2_scale === 2
      ? `${p.spo2_target[0]}–${p.spo2_target[1]}% (personalized Scale 2 target)`
      : "96–100% (standard target)";

  const recentCheckins = alerts
    .filter((a) => a.alert_kind === "CLINICAL" && a.tier !== "NORMAL")
    .slice(0, 5);
  const deviceReminders = alerts.filter((a) => a.alert_kind !== "CLINICAL").slice(0, 4);

  return {
    id: `patient-summary-${p.patient_id}-${generatedAt}`,
    kind: "patient-summary",
    title: `Personal Post-Discharge Health Summary`,
    subtitle: `${p.display_name} · Discharged ${p.discharged_on}`,
    generatedAt,
    authorLabel: `${p.display_name} (Patient Portal)`,
    filename: `health-summary-${p.patient_id}.md`,
    disclaimer:
      "This summary is for personal tracking and care-team communication only — it is not a medical diagnosis. Seek urgent medical care if you feel unwell.",
    sections: [
      {
        title: "Current Recovery Status",
        paragraphs: [statusText],
        bullets: [
          `Last reading: ${liveOverride ? "Live from connected smartwatch" : state.timestamp ? `${dateTime(state.timestamp)} UTC` : "Waiting for first reading"}`,
          `Readings recorded in current window: ${readings.length}`,
          `Personal oxygen (SpO₂) target: ${targetSpo2}`,
        ],
      },
      {
        title: "Latest Vital Signs",
        table: {
          headers: ["Vital Sign", "Latest Reading", "Notes"],
          rows: obs
            ? [
                [
                  "Heart rate",
                  `${fmtMetric(obs.heart_rate)} bpm`,
                  p.beta_blocked ? "Adjusted for your beta-blocker medication" : "Resting pulse",
                ],
                ["Oxygen saturation (SpO₂)", `${fmtMetric(obs.spo2)} %`, `Target: ${targetSpo2}`],
                ["Breathing rate", `${fmtMetric(obs.respiratory_rate)} breaths/min`, ""],
                ["Body temperature", `${fmtMetric(obs.temperature, 1)} °C`, ""],
              ]
            : [["—", "No readings yet", "Wearable sync pending"]],
        },
      },
      {
        title: "My Conditions & Medications",
        bullets: [
          `Documented conditions: ${p.conditions.map((c) => c.display).join(", ") || "None listed"}`,
          `Active medications: ${p.medications.map((m) => m.display).join(", ") || "None listed"}`,
        ],
      },
      {
        title: "Recent Check-In Notices & Device Reminders",
        bullets: [
          ...(recentCheckins.length > 0
            ? recentCheckins.map(
                (a) => `[${TIER_LABEL[a.tier]}] ${dateTime(a.timestamp)} UTC — ${a.explanation?.narrative ?? "Change in vitals noted."}`,
              )
            : ["No health check-in alerts recently."]),
          ...(deviceReminders.length > 0
            ? deviceReminders.map(
                (a) => `[Device check] ${dateTime(a.timestamp)} UTC — ${a.artifact.reasons[0] ?? "Check wearable fit/connection."}`,
              )
            : []),
        ],
      },
    ],
    visualContext: { state, alerts, readings, liveOverride },
  };
}

export function deriveOrganFindings(
  state: PatientStatePayload,
  liveOverride?: WatchLiveOverride | null,
  patientFriendly = false,
): OrganFinding[] {
  const twin = clinicalToTwinPayload(state, liveOverride);
  const a = twin.assessments;
  const specs: Array<{
    key: "heart_rate" | "spo2" | "respiratory_rate" | "temperature";
    organ: string;
    metricLabel: string;
    unit: string;
    digits: number;
    highMsg: string;
    lowMsg: string;
    okMsg: string;
  }> = [
    {
      key: "heart_rate",
      organ: "Heart & Circulation",
      metricLabel: "Heart Rate",
      unit: "bpm",
      digits: 0,
      highMsg: patientFriendly
        ? "Heart rate is higher than your usual resting range."
        : "Tachycardic / elevated pulse relative to personal baseline & beta-blocker rule.",
      lowMsg: patientFriendly
        ? "Heart rate is lower than your usual resting range."
        : "Bradycardic / depressed pulse relative to personal baseline.",
      okMsg: patientFriendly ? "Steady heart rhythm and rate." : "Within expected personal range.",
    },
    {
      key: "spo2",
      organ: "Lungs & Oxygenation",
      metricLabel: "Blood Oxygen (SpO₂)",
      unit: "%",
      digits: 1,
      highMsg: patientFriendly ? "Oxygen level is above target." : "Above personalized SpO₂ target.",
      lowMsg: patientFriendly
        ? "Oxygen saturation is below your personal target."
        : `Hypoxemic trend below Scale ${state.profile.spo2_scale} target (${state.profile.spo2_target[0]}–${state.profile.spo2_target[1]}%).`,
      okMsg: patientFriendly ? "Oxygen delivery is on target." : "Within personalized SpO₂ target.",
    },
    {
      key: "respiratory_rate",
      organ: "Respiratory Drive",
      metricLabel: "Breathing Rate",
      unit: "br/min",
      digits: 1,
      highMsg: patientFriendly
        ? "Breathing is faster than usual — take a rest and check in if short of breath."
        : "Tachypnea / elevated respiratory rate.",
      lowMsg: patientFriendly
        ? "Breathing rate is slower than usual."
        : "Bradypnea / depressed respiratory rate.",
      okMsg: patientFriendly ? "Breathing pace is steady." : "Normal respiratory cadence.",
    },
    {
      key: "temperature",
      organ: "Systemic / Thermal",
      metricLabel: "Body Temperature",
      unit: "°C",
      digits: 2,
      highMsg: patientFriendly
        ? "Elevated temperature (possible fever)."
        : "Pyrexia / febrile elevation above baseline.",
      lowMsg: patientFriendly
        ? "Body temperature is lower than normal."
        : "Hypothermic drift below baseline.",
      okMsg: patientFriendly ? "Normal body temperature." : "Normothermic.",
    },
  ];

  return specs.map((s) => {
    const item = a[s.key];
    const val = item?.value;
    const expected = item?.expected ?? val ?? 0;
    const status = item?.status ?? "normal";
    const summary =
      status === "normal"
        ? s.okMsg
        : (val ?? expected) >= expected
          ? s.highMsg
          : s.lowMsg;
    const base = state.baselines?.[s.key];
    const baselineText = base
      ? `Baseline ${base.center.toFixed(s.digits)} (±${(2 * base.spread).toFixed(s.digits)} ${s.unit})`
      : "Baseline warming up";
    return {
      organ: s.organ,
      metricLabel: s.metricLabel,
      valueText: val !== undefined && val !== null ? `${fmtMetric(val, s.digits)} ${s.unit}` : "—",
      baselineText,
      status,
      summary,
    };
  });
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function buildSvgSparkline(
  readings: Observation[],
  metric: "heart_rate" | "spo2" | "respiratory_rate" | "temperature",
  label: string,
  unit: string,
  color: string,
): string {
  const vals = readings
    .map((r) => r[metric])
    .filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  if (vals.length < 2) {
    return `<div class="chart-card"><div class="chart-title">${escapeHtml(label)} (${escapeHtml(unit)})</div><div class="muted">Not enough readings for trend graph</div></div>`;
  }
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min || 1;
  const w = 280;
  const h = 74;
  const pts = vals
    .map((v, i) => {
      const x = (i / (vals.length - 1)) * (w - 16) + 8;
      const y = h - 10 - ((v - min) / span) * (h - 20);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const latest = vals[vals.length - 1];
  return `<div class="chart-card">
    <div class="chart-head"><span>${escapeHtml(label)}</span><strong>${latest.toFixed(metric === "temperature" ? 1 : 0)} ${escapeHtml(unit)}</strong></div>
    <svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" role="img" aria-label="${escapeHtml(label)} trend">
      <polyline fill="none" stroke="${color}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" points="${pts}" />
    </svg>
    <div class="chart-range">Min ${min.toFixed(1)} · Max ${max.toFixed(1)} ${escapeHtml(unit)}</div>
  </div>`;
}

export function reportToHtml(report: GeneratedReport, twinSnapshotDataUrl?: string | null): string {
  const vc = report.visualContext;
  const findings = vc
    ? deriveOrganFindings(vc.state, vc.liveOverride, report.kind === "patient-summary")
    : [];

  const findingsHtml = findings.length
    ? `<section><h2>3D Organ &amp; System Findings</h2>
        ${
          twinSnapshotDataUrl
            ? `<div class="twin-snap"><img src="${twinSnapshotDataUrl}" alt="3D Digital Twin showing highlighted organs" /></div>`
            : ""
        }
        <div class="findings-grid">
          ${findings
            .map(
              (f) => `<div class="finding finding--${f.status}">
                <div class="finding-top"><strong>${escapeHtml(f.organ)}</strong><span class="badge badge--${f.status}">${f.status.toUpperCase()}</span></div>
                <div class="finding-val">${escapeHtml(f.metricLabel)}: <strong>${escapeHtml(f.valueText)}</strong> <span class="muted">(${escapeHtml(f.baselineText)})</span></div>
                <p>${escapeHtml(f.summary)}</p>
              </div>`,
            )
            .join("")}
        </div></section>`
    : "";

  const chartsHtml =
    vc && vc.readings.length > 1
      ? `<section><h2>Vital Sign Trends</h2><div class="charts-grid">
          ${buildSvgSparkline(vc.readings, "heart_rate", "Heart Rate", "bpm", "#d9483b")}
          ${buildSvgSparkline(vc.readings, "spo2", "SpO₂", "%", "#1d70b8")}
          ${buildSvgSparkline(vc.readings, "respiratory_rate", "Respiratory Rate", "br/min", "#0f766e")}
          ${buildSvgSparkline(vc.readings, "temperature", "Temperature", "°C", "#c27803")}
        </div></section>`
      : "";

  const sectionsHtml = report.sections
    .map((sec) => {
      const ps = (sec.paragraphs ?? []).map((p) => `<p>${escapeHtml(p)}</p>`).join("");
      const bs =
        sec.bullets && sec.bullets.length
          ? `<ul>${sec.bullets.map((b) => `<li>${escapeHtml(b)}</li>`).join("")}</ul>`
          : "";
      const tbl =
        sec.table && sec.table.rows.length
          ? `<table><thead><tr>${sec.table.headers.map((h) => `<th>${escapeHtml(h)}</th>`).join("")}</tr></thead><tbody>${sec.table.rows
              .map((r) => `<tr>${r.map((c) => `<td>${escapeHtml(c)}</td>`).join("")}</tr>`)
              .join("")}</tbody></table>`
          : "";
      return `<section><h2>${escapeHtml(sec.title)}</h2>${ps}${bs}${tbl}</section>`;
    })
    .join("");

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>${escapeHtml(report.title)}</title>
<style>
  body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; color: #162229; background: #f4f7f8; margin: 0; padding: 24px; line-height: 1.5; }
  .sheet { max-width: 900px; margin: 0 auto; background: #fff; border: 1px solid #d5dee3; border-radius: 12px; padding: 28px; }
  header { display: flex; justify-content: space-between; gap: 16px; flex-wrap: wrap; border-bottom: 1px solid #d5dee3; padding-bottom: 14px; margin-bottom: 16px; }
  h1 { margin: 4px 0; font-size: 22px; }
  h2 { font-size: 16px; margin: 22px 0 8px; color: #0b2d3c; border-bottom: 1px solid #eef2f4; padding-bottom: 4px; }
  .eyebrow { font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #566973; }
  .meta { font-size: 12.5px; color: #465862; text-align: right; }
  .notice { background: #fff8e6; border: 1px solid #f0d78c; border-radius: 8px; padding: 10px 14px; font-size: 13px; margin-bottom: 16px; }
  .twin-snap { text-align: center; background: #0f1a20; border-radius: 10px; padding: 10px; margin-bottom: 12px; }
  .twin-snap img { max-height: 300px; max-width: 100%; border-radius: 6px; }
  .findings-grid, .charts-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; }
  .finding, .chart-card { border: 1px solid #d5dee3; border-radius: 8px; padding: 10px 12px; background: #fbfcfd; }
  .finding--watch { border-color: #e6a817; background: #fffbf0; }
  .finding--anomalous { border-color: #d9483b; background: #fff5f5; }
  .finding-top, .chart-head { display: flex; justify-content: space-between; align-items: center; font-size: 13px; margin-bottom: 4px; }
  .finding-val { font-size: 13px; margin-bottom: 4px; }
  .finding p { margin: 0; font-size: 12.5px; color: #465862; }
  .badge { font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 999px; }
  .badge--normal { background: #e3f5ec; color: #166534; }
  .badge--watch { background: #fef3c7; color: #92400e; }
  .badge--anomalous { background: #fee2e2; color: #991b1b; }
  .chart-range, .muted { font-size: 11px; color: #566973; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 8px; }
  th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid #e6edf0; }
  th { background: #f6f9fa; font-weight: 600; }
  ul { margin: 6px 0; padding-left: 20px; }
  li { margin: 3px 0; font-size: 13.5px; }
</style>
</head>
<body>
  <article class="sheet">
    <header>
      <div>
        <div class="eyebrow">PS-03 Post-Discharge Monitor · ${report.kind === "patient-summary" ? "Patient Copy" : "Clinical Copy"}</div>
        <h1>${escapeHtml(report.title)}</h1>
        <div class="muted">${escapeHtml(report.subtitle)}</div>
      </div>
      <div class="meta">
        <div><strong>Generated:</strong> ${escapeHtml(dateTime(report.generatedAt))} UTC</div>
        <div><strong>Prepared:</strong> ${escapeHtml(report.authorLabel)}</div>
      </div>
    </header>
    <div class="notice">${escapeHtml(report.disclaimer)}</div>
    ${findingsHtml}
    ${chartsHtml}
    ${sectionsHtml}
  </article>
</body>
</html>`;
}
