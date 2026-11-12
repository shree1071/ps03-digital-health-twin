import type {
  FhirObservation,
  MonitorResult,
  Observation,
  PatientRow,
  PatientStatePayload,
  PerformanceReport,
  ReplayStatus,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep status text */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export const clinicalApi = {
  patients: () =>
    request<{ patients: PatientRow[]; disclaimer: string }>("/api/clinical/patients"),
  state: (id: string) => request<PatientStatePayload>(`/api/clinical/patients/${id}/state`),
  alerts: (id: string, limit = 60) =>
    request<{ alerts: MonitorResult[] }>(`/api/clinical/patients/${id}/alerts?limit=${limit}`),
  readings: (id: string, limit = 400) =>
    request<{ readings: Observation[] }>(`/api/clinical/patients/${id}/readings?limit=${limit}`),
  replayStatus: () => request<ReplayStatus>("/api/clinical/replay/status"),
  replayStart: (patientIds?: string[]) =>
    post<ReplayStatus>("/api/clinical/replay/start", { patient_ids: patientIds ?? null }),
  replayStop: () => post<ReplayStatus>("/api/clinical/replay/stop"),
  replayReset: () => post<ReplayStatus>("/api/clinical/replay/reset"),
  report: (refresh = false) =>
    request<PerformanceReport>(`/api/clinical/report${refresh ? "?refresh=true" : ""}`),
  hapiHealth: () =>
    request<{ healthy: boolean; base_url: string }>("/api/clinical/hapi/health"),
  hapiObservations: (id: string, loinc?: string, count = 20) => {
    const q = new URLSearchParams({ count: String(count) });
    if (loinc) q.set("loinc", loinc);
    return request<{ total: number | null; entries: FhirObservation[] }>(
      `/api/clinical/hapi/observations/${id}?${q}`,
    );
  },
};

/** The four LOINC codes PS-03 §3.1 mandates, plus the two NEWS2 also uses. */
export const LOINC: Record<string, { code: string; label: string; unit: string }> = {
  heart_rate: { code: "8867-4", label: "Heart rate", unit: "bpm" },
  spo2: { code: "2708-6", label: "SpO₂", unit: "%" },
  respiratory_rate: { code: "9279-1", label: "Respiratory rate", unit: "br/min" },
  temperature: { code: "8310-5", label: "Body temperature", unit: "°C" },
  systolic_bp: { code: "8480-6", label: "Systolic BP", unit: "mmHg" },
  diastolic_bp: { code: "8462-4", label: "Diastolic BP", unit: "mmHg" },
};

export const METRIC_ORDER = [
  "heart_rate",
  "spo2",
  "respiratory_rate",
  "temperature",
  "systolic_bp",
] as const;

export type Metric = (typeof METRIC_ORDER)[number];