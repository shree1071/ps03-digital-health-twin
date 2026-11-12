import { useCallback, useEffect, useRef, useState } from "react";
import { LOINC, clinicalApi } from "../api";
import type { FhirObservation, PatientRow } from "../types";
import { Empty, Panel, dateTime } from "../ui";

const MANDATORY = ["heart_rate", "spo2", "respiratory_rate", "temperature"] as const;

function ObservationJson({ observation }: { observation: FhirObservation }) {
  return <pre className="pre">{JSON.stringify(observation, null, 2)}</pre>;
}

/** FHIR R4 compliance evidence (§3.1, evaluation criterion 4). */
export function FhirPage({
  rows,
  patientId,
  onSelect,
}: {
  rows: PatientRow[];
  patientId: string | null;
  onSelect: (id: string) => void;
}) {
  const [health, setHealth] = useState<{ healthy: boolean; base_url: string } | null>(null);
  const [observations, setObservations] = useState<FhirObservation[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [loinc, setLoinc] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const requestVersion = useRef(0);
  const selected = rows.find((r) => r.patient_id === patientId) ?? rows[0] ?? null;
  const activeId = selected?.patient_id ?? null;

  useEffect(() => {
    let cancelled = false;
    clinicalApi.hapiHealth()
      .then((r) => { if (!cancelled) setHealth(r); })
      .catch(() => { if (!cancelled) setHealth({ healthy: false, base_url: "" }); });
    return () => { cancelled = true; };
  }, []);

  const load = useCallback(
    (id: string, code: string) => {
      const version = ++requestVersion.current;
      setBusy(true);
      setError(null);
      setObservations([]);
      setTotal(null);
      clinicalApi
        .hapiObservations(id, code || undefined, 12)
        .then((r) => {
          if (requestVersion.current !== version) return;
          setObservations(r.entries);
          setTotal(r.total);
        })
        .catch((e: Error) => {
          if (requestVersion.current === version) setError(e.message);
        })
        .finally(() => {
          if (requestVersion.current === version) setBusy(false);
        });
    },
    [],
  );

  useEffect(() => {
    if (activeId) load(activeId, loinc);
    else {
      setObservations([]);
      setTotal(null);
      setError(null);
      setBusy(false);
    }
    return () => { requestVersion.current += 1; };
  }, [activeId, loinc, load]);

  return (
    <div className="col">
      <div className="spread">
        <div>
          <h1 className="section-title">FHIR R4 storage</h1>
          <p className="lede">
            Every accepted reading is stored in the HAPI FHIR server as a Vital Signs
            Observation before it is scored, so the clinical record and the score always
            agree. These are the resources as HAPI returned them.
          </p>
        </div>
        <span className={`pill pill--${health?.healthy ? "live" : "off"}`}>
          <span className="pill__dot" aria-hidden />
          {health === null
            ? "Checking HAPI…"
            : health.healthy
              ? `HAPI connected · ${health.base_url}`
              : "HAPI unreachable"}
        </span>
      </div>

      {health !== null && !health.healthy && (
        <div className="notice notice--warn">
          The FHIR server is not reachable. Start it with{" "}
          <span className="code">scripts/start_hapi.bat</span> (no Docker required), then
          reload this page.
        </div>
      )}

      <Panel title="Mandated LOINC coding" hint="PS-03 §3.1 · UCUM units · vitalsigns profile">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Vital</th>
              <th scope="col">LOINC</th>
              <th scope="col">Display</th>
              <th scope="col">Unit</th>
              <th scope="col">Source</th>
            </tr>
          </thead>
          <tbody>
            {MANDATORY.map((metric) => (
              <tr key={metric}>
                <td style={{ fontWeight: 600 }}>{LOINC[metric].label}</td>
                <td>
                  <span className="code">{LOINC[metric].code}</span>
                </td>
                <td className="muted small">
                  {metric === "spo2"
                    ? "Oxygen saturation in Arterial blood"
                    : LOINC[metric].label}
                </td>
                <td className="code">{LOINC[metric].unit}</td>
                <td className="small muted">
                  {metric === "temperature"
                    ? "virtual wearable (BIDMC has none)"
                    : "wearable + BIDMC replay"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="small muted" style={{ marginTop: 10 }}>
          Systolic and diastolic blood pressure (8480-6, 8462-4) are also stored when the
          source supplies them; NEWS2 scores systolic pressure. A missing reading is encoded
          as <span className="code">dataAbsentReason</span> — a SpO₂ of 0% is a sensor
          state, not a quantity, and must not pollute the record.
        </p>
      </Panel>

      <Panel title="Stored observations" hint="search HAPI by patient and LOINC">
        <div className="row row--tight" style={{ marginBottom: 12, alignItems: "center" }}>
          <label className="small muted" htmlFor="fhir-patient">
            Patient
          </label>
          <select
            id="fhir-patient"
            className="btn btn--small"
            value={activeId ?? ""}
            onChange={(e) => onSelect(e.target.value)}
          >
            {rows.map((row) => (
              <option key={row.patient_id} value={row.patient_id}>
                {row.display_name}
              </option>
            ))}
          </select>
          <label className="small muted" htmlFor="fhir-loinc">
            LOINC
          </label>
          <select
            id="fhir-loinc"
            className="btn btn--small"
            value={loinc}
            onChange={(e) => setLoinc(e.target.value)}
          >
            <option value="">All vitals</option>
            {Object.entries(LOINC).map(([metric, meta]) => (
              <option key={metric} value={meta.code}>
                {meta.label} ({meta.code})
              </option>
            ))}
          </select>
          <button
            className="btn btn--small"
            onClick={() => activeId && load(activeId, loinc)}
            disabled={busy || !activeId}
          >
            {busy ? "Searching…" : "Refresh"}
          </button>
          {total !== null && (
            <span className="chip">
              {total.toLocaleString()} matching resources in HAPI
            </span>
          )}
        </div>

        {error && <div className="notice notice--warn">{error}</div>}
        {!error && observations.length === 0 && (
          <Empty>
            {busy
              ? "Searching stored observations…"
              : "No stored observations for this selection yet. Start the scenario replay to stream readings through the ingestion API."}
          </Empty>
        )}
        <div className="col scroll" style={{ gap: 10 }}>
          {observations.map((observation, i) => (
            <div key={observation.id ?? i}>
              <div className="spread small muted" style={{ marginBottom: 4 }}>
                <span>
                  <span className="code">{observation.id ?? "—"}</span>{" "}
                  {observation.code?.text ?? ""}
                </span>
                <span>{dateTime(observation.effectiveDateTime)} UTC</span>
              </div>
              <ObservationJson observation={observation} />
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}