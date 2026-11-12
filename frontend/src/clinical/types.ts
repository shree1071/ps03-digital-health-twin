/** Mirrors the PS-03 clinical API payloads (backend/app/services/clinical.py). */

export type Tier = "NORMAL" | "WATCH" | "REVIEW" | "URGENT";
export type AlertKind = "CLINICAL" | "SENSOR" | "DEVICE_OFFLINE";
export type ArtifactKind =
  | "PROBE_REMOVAL"
  | "FLATLINE"
  | "IMPLAUSIBLE"
  | "MOTION_NOISE"
  | "DROPOUT";

export interface Condition {
  code: string;
  display: string;
  system: string;
}

export interface Medication extends Condition {
  generic: string;
}

export interface PersonalizationRule {
  rule_id: string;
  title: string;
  profile_fact: string;
  effect: string;
}

export interface DischargeProfile {
  patient_id: string;
  display_name: string;
  age_years: number;
  sex: string;
  discharged_on: string;
  conditions: Condition[];
  medications: Medication[];
  hypercapnic_respiratory_failure: boolean;
  spo2_scale: number;
  spo2_target: number[];
  beta_blocked: boolean;
  pulse_correction_bpm: number;
  notes: string;
  source: string;
  scenario: string;
  rules: PersonalizationRule[];
}

export interface ParameterScore {
  parameter: string;
  label: string;
  value: number | string | null;
  unit: string;
  score: number;
  band: string;
  scale: number | null;
}

export interface News2Result {
  total: number;
  tier: Tier;
  risk_level: string;
  spo2_scale: number;
  single_parameter_three: boolean;
  complete: boolean;
  missing: string[];
  parameters: ParameterScore[];
}

export interface DriftFinding {
  metric: string;
  label: string;
  value: number;
  baseline_center: number;
  baseline_spread: number;
  robust_z: number;
  direction: number;
  sustained: number;
}

export interface ArtifactVerdict {
  timestamp: string;
  is_artifact: boolean;
  kinds: ArtifactKind[];
  reasons: string[];
  untrusted_metrics: string[];
  gap_seconds: number | null;
}

export interface ExplanationReading {
  metric: string;
  label: string;
  value: number | string | null;
  unit: string;
  news2_points: number;
  band: string;
  scale?: number | null;
  drift?: boolean;
}

export interface AlertExplanation {
  readings: ExplanationReading[];
  rules: PersonalizationRule[];
  profile_facts: string[];
  narrative: string;
}

export interface Observation {
  timestamp: string;
  heart_rate: number | null;
  spo2: number | null;
  respiratory_rate: number | null;
  temperature: number | null;
  systolic_bp: number | null;
  diastolic_bp: number | null;
  activity_intensity: number;
  source: string;
}

/** One scored reading, as stored in the alert history. */
export interface MonitorResult {
  patient_id: string;
  timestamp: string;
  tier: Tier;
  alert_kind: AlertKind;
  suppressed: boolean;
  baseline_ready: boolean;
  artifact: ArtifactVerdict;
  news2: News2Result | null;
  drift: DriftFinding[];
  explanation: AlertExplanation | null;
}

/** Live per-patient state pushed on the patient WebSocket channel. */
export interface PatientStatePayload {
  patient_id: string;
  profile: DischargeProfile;
  timestamp: string | null;
  tier: Tier;
  alert_kind: AlertKind | null;
  news2: News2Result | null;
  drift: DriftFinding[];
  baselines: Record<string, { center: number; spread: number }>;
  artifact: ArtifactVerdict | null;
  explanation: AlertExplanation | null;
  suppressed: boolean;
  baseline_ready: boolean;
  observation: Observation | null;
  stored_observations: number;
  suppressed_count: number;
  tier_counts: Record<string, number>;
  artifact_counts: Record<string, number>;
  disclaimer: string;
}

/** One row of the ward overview. */
export interface PatientRow {
  patient_id: string;
  display_name: string;
  age_years: number;
  sex: string;
  scenario: string;
  conditions: string[];
  medications: string[];
  spo2_scale: number;
  beta_blocked: boolean;
  timestamp: string | null;
  tier: Tier;
  news2_total: number | null;
  alert_kind: AlertKind | null;
  suppressed: boolean;
  drift_metrics: string[];
  baseline_ready: boolean;
  alert_count: number;
  suppressed_count: number;
  stored_observations: number;
}

export interface BidmcProgress {
  source: string;
  record_id: string | null;
  record_index: number;
  records_total: number;
  sample_index: number;
  samples_total: number;
  emitted: number;
  speed: number;
  finished: boolean;
}

export interface ReplayStream {
  patient_id: string;
  scenario: string;
  source: string;
  elapsed_s: number;
  elapsed_hours: number;
  emitted: number;
  dropouts: number;
  finished: boolean;
  last_error: string | null;
  active_events: string[];
  deterioration_onset_s: number | null;
  first_alert_elapsed_s: number | null;
  first_alert_at: string | null;
  first_alert_tier: Tier | null;
  detection_delay_s: number | null;
  detection_delay_hours: number | null;
  bidmc: BidmcProgress | null;
}

export interface ScenarioInfo {
  key: string;
  label: string;
  description: string;
}

export interface ReplayStatus {
  running: boolean;
  ticks: number;
  started_at: string | null;
  tick_seconds: number;
  sim_seconds_per_tick: number;
  time_acceleration: number;
  scenarios: ScenarioInfo[];
  streams: ReplayStream[];
}

export interface SystemRun {
  first_alert_hours: number | null;
  detection_delay_hours: number | null;
  lead_time_hours: number | null;
  alerts: number;
  false_alerts: number;
  alerts_per_patient_day: number | null;
}

export interface ScenarioEvaluation {
  patient_id: string;
  display_name: string;
  scenario: string;
  source: string;
  readings: number;
  span_hours: number;
  patient_days: number;
  onset_hours: number | null;
  severity_at_hours: number | null;
  proposed: SystemRun & { suppressed: number; max_tier: Tier };
  naive: SystemRun & { alerting_before_onset: boolean };
  lead_time_gain_hours: number | null;
  artifact_counts: Record<string, number>;
  tier_counts: Record<string, number>;
}

export interface ReportTotals {
  patients: number;
  readings: number;
  patient_days: number;
  proposed_alerts: number;
  naive_alerts: number;
  proposed_alerts_per_patient_day: number;
  naive_alerts_per_patient_day: number;
  alert_reduction_percent: number | null;
  proposed_false_alerts: number;
  naive_false_alerts: number;
  false_alert_reduction_percent: number | null;
  suppressed_readings: number;
  artifact_counts: Record<string, number>;
  mean_lead_time_hours: number | null;
  naive_mean_lead_time_hours: number | null;
  mean_detection_delay_hours: number | null;
  naive_mean_detection_delay_hours: number | null;
}

export interface PerformanceReport {
  scenarios: ScenarioEvaluation[];
  recorded: ScenarioEvaluation[];
  totals: ReportTotals;
  recorded_totals: ReportTotals | null;
  method: Record<string, unknown>;
  errors: { patient_id: string; detail: string }[];
  generated_at: string;
  disclaimer: string;
}

export interface FhirObservation {
  resourceType: string;
  id?: string;
  status?: string;
  meta?: { profile?: string[] };
  category?: { coding?: { system?: string; code?: string; display?: string }[] }[];
  code?: { coding?: { system?: string; code?: string; display?: string }[]; text?: string };
  subject?: { reference?: string };
  effectiveDateTime?: string;
  valueQuantity?: { value?: number; unit?: string; system?: string; code?: string };
  dataAbsentReason?: { coding?: { code?: string; display?: string }[] };
  device?: { reference?: string };
}

export const DISCLAIMER = "Clinical decision support, not medical diagnosis.";