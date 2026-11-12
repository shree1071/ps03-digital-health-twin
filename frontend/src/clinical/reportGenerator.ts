import type { AuthSession } from "./auth";
import type { MonitorResult, Observation, PatientRow, PatientStatePayload } from "./types";
import { SCENARIO_LABEL, TIER_LABEL, dateTime, fmtValue } from "./ui";

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
      ["Heart rate", `${fmtValue("heart_rate", o.heart_rate)} bpm`, "—", "unscored"],
      ["SpO₂", `${fmtValue("spo2", o.spo2)} %`, "—", "unscored"],
      ["Respiratory rate", `${fmtValue("respiratory_rate", o.respiratory_rate)} br/min`, "—", "unscored"],
      ["Temperature", `${fmtValue("temperature", o.temperature)} °C`, "—", "unscored"],
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
): GeneratedReport {
  const p = state.profile;
  const generatedAt = nowIso();
  const obs = state.observation;

  const statusText = !state.timestamp
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
          `Last reading: ${state.timestamp ? `${dateTime(state.timestamp)} UTC` : "Waiting for first reading"}`,
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
                  `${fmtValue("heart_rate", obs.heart_rate)} bpm`,
                  p.beta_blocked ? "Adjusted for your beta-blocker medication" : "Resting pulse",
                ],
                ["Oxygen saturation (SpO₂)", `${fmtValue("spo2", obs.spo2)} %`, `Target: ${targetSpo2}`],
                ["Breathing rate", `${fmtValue("respiratory_rate", obs.respiratory_rate)} breaths/min`, ""],
                ["Body temperature", `${fmtValue("temperature", obs.temperature)} °C`, ""],
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
  };
}
