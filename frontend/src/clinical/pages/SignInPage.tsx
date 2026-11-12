import { useEffect, useState, type FormEvent } from "react";
import type { AuthSession } from "../auth";
import type { PatientRow } from "../types";
import { Panel, SCENARIO_LABEL, TIER_LABEL, TierBadge } from "../ui";

const FALLBACK_PATIENTS: Array<{ patient_id: string; display_name: string; scenario: string }> = [
  { patient_id: "p1_scale2_copd", display_name: "COPD / Hypercapnic Failure", scenario: "copd_exacerbation" },
  { patient_id: "p2_beta_blocker", display_name: "Beta-Blocker Patient", scenario: "sepsis_drift" },
  { patient_id: "p3_heart_failure", display_name: "Heart Failure Patient", scenario: "heart_failure" },
  { patient_id: "p4_diabetes", display_name: "Diabetes Recovery", scenario: "stable_recovery" },
  { patient_id: "p5_stable_recovery", display_name: "Stable Recovery", scenario: "stable_recovery" },
];

const CAPABILITIES = [
  {
    tag: "FHIR R4 + HAPI",
    title: "Standards-based ingestion",
    body: "LOINC-coded vital observations (HR, SpO₂, RR, Temp) persisted in HAPI FHIR R4.",
  },
  {
    tag: "Personalized NEWS2",
    title: "Profile + rolling baseline",
    body: "SpO₂ Scale 2 for hypercapnic failure, beta-blocker pulse adjustment, and robust-z drift.",
  },
  {
    tag: "Artifact Filter",
    title: "Suppresses false alarms",
    body: "Detects probe removal, flatlines, motion noise, and dropouts as sensor issues, not clinical panic.",
  },
  {
    tag: "3D Twin & WhatsApp",
    title: "Live alerts & reports",
    body: "Interactive 3D anatomical twin, smartwatch demo, Twilio WhatsApp alerts, and PDF/MD reports.",
  },
];

export function SignInPage({
  rows,
  onSignIn,
}: {
  rows: PatientRow[];
  onSignIn: (session: AuthSession) => void;
}) {
  const [role, setRole] = useState<"doctor" | "patient">("doctor");

  // Doctor form state
  const [clinicianName, setClinicianName] = useState("Dr. Ananya Rao");
  const [clinicianId, setClinicianId] = useState("DOC-204 · Post-Discharge Team");
  const [doctorPin, setDoctorPin] = useState("2026");

  // Patient form state
  const [selectedPatientId, setSelectedPatientId] = useState("");
  const [patientPin, setPatientPin] = useState("1234");
  const [error, setError] = useState<string | null>(null);

  const patientOptions = rows.length > 0 ? rows : FALLBACK_PATIENTS;

  useEffect(() => {
    if (!selectedPatientId && patientOptions.length > 0) {
      setSelectedPatientId(patientOptions[0].patient_id);
    }
  }, [patientOptions, selectedPatientId]);

  const handleDoctorSubmit = (e: FormEvent) => {
    e.preventDefault();
    const name = clinicianName.trim();
    const id = clinicianId.trim();
    if (!name || !id) {
      setError("Please enter your clinician name and ID.");
      return;
    }
    if (!doctorPin.trim()) {
      setError("Please enter your access passcode.");
      return;
    }
    setError(null);
    onSignIn({ role: "doctor", clinicianName: name, clinicianId: id });
  };

  const handlePatientSubmit = (e: FormEvent) => {
    e.preventDefault();
    const match = patientOptions.find((p) => p.patient_id === selectedPatientId);
    if (!match) {
      setError("Please select your patient profile.");
      return;
    }
    if (!patientPin.trim()) {
      setError("Please enter your patient portal PIN.");
      return;
    }
    setError(null);
    onSignIn({
      role: "patient",
      patientId: match.patient_id,
      patientName: match.display_name,
    });
  };

  return (
    <div className="landing-grid">
      {/* Left: Project Hero & Relevant Capabilities */}
      <section className="landing-hero">
        <div className="row row--tight" style={{ flexWrap: "wrap" }}>
          <span className="chip">VitalTwin · PS-03</span>
          <span className="chip">MineArena 1.0 · Torque Titans, BIET Davangere</span>
        </div>

        <h1 className="landing-hero__title">
          Catch post-discharge deterioration early — without alert fatigue.
        </h1>
        <p className="landing-hero__lede">
          VitalTwin ingests continuous wearable vitals as <strong>FHIR R4 Observations</strong>,
          scores them with a <strong>personalized NEWS2 + rolling baseline</strong> engine, filters
          out sensor artifacts, and routes <strong>Watch / Review / Urgent</strong> alerts with
          live <strong>3D Digital Twin</strong> visuals and <strong>WhatsApp</strong> notifications.
        </p>

        <div className="landing-cards">
          {CAPABILITIES.map((c) => (
            <div className="landing-card" key={c.tag}>
              <span className="landing-card__tag">{c.tag}</span>
              <h3>{c.title}</h3>
              <p>{c.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Right: Patient & Doctor Login Card */}
      <section className="signin-card">
        <div className="signin__tabs" role="tablist" aria-label="Select portal role">
          <button
            type="button"
            role="tab"
            aria-selected={role === "doctor"}
            className={`signin__tab ${role === "doctor" ? "signin__tab--active" : ""}`}
            onClick={() => {
              setRole("doctor");
              setError(null);
            }}
          >
            <strong>Doctor Login</strong>
            <span className="small muted">Ward triage · NEWS2 · Reports</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={role === "patient"}
            className={`signin__tab ${role === "patient" ? "signin__tab--active" : ""}`}
            onClick={() => {
              setRole("patient");
              setError(null);
            }}
          >
            <strong>Patient Login</strong>
            <span className="small muted">Smartwatch · Graphs · 3D Twin</span>
          </button>
        </div>

        {error && (
          <div className="notice notice--warn" role="alert">
            {error}
          </div>
        )}

        {role === "doctor" ? (
          <Panel
            title="Clinician Portal Sign-In"
            hint="Full post-discharge ward & clinical report export"
          >
            <form className="signin__form" onSubmit={handleDoctorSubmit}>
              <label className="field">
                <span className="field__label">Clinician name</span>
                <input
                  className="input"
                  type="text"
                  value={clinicianName}
                  onChange={(e) => setClinicianName(e.target.value)}
                  placeholder="Dr. Ananya Rao"
                />
              </label>
              <label className="field">
                <span className="field__label">Clinician ID / Service</span>
                <input
                  className="input"
                  type="text"
                  value={clinicianId}
                  onChange={(e) => setClinicianId(e.target.value)}
                  placeholder="DOC-204"
                />
              </label>
              <label className="field">
                <span className="field__label">Access passcode (demo)</span>
                <input
                  className="input"
                  type="password"
                  value={doctorPin}
                  onChange={(e) => setDoctorPin(e.target.value)}
                  placeholder="Enter passcode"
                />
              </label>

              <div className="signin__features small muted">
                Includes: 5-patient ward census · Personalized NEWS2 &amp; drift · Artifact
                suppression log · HAPI FHIR explorer · Ward &amp; Patient PDF/MD reports
              </div>

              <button type="submit" className="btn btn--primary" style={{ width: "100%" }}>
                Sign in to Doctor Dashboard →
              </button>
            </form>
          </Panel>
        ) : (
          <Panel
            title="Patient Portal Sign-In"
            hint="Scoped to your own recovery & smartwatch"
          >
            <form className="signin__form" onSubmit={handlePatientSubmit}>
              <label className="field">
                <span className="field__label">Choose patient profile</span>
                <select
                  className="input"
                  value={selectedPatientId}
                  onChange={(e) => setSelectedPatientId(e.target.value)}
                >
                  {patientOptions.map((p) => (
                    <option key={p.patient_id} value={p.patient_id}>
                      {p.display_name} ({p.patient_id})
                    </option>
                  ))}
                </select>
              </label>

              {rows.length > 0 && (
                <div className="signin__roster">
                  {rows.map((p) => {
                    const active = p.patient_id === selectedPatientId;
                    return (
                      <button
                        type="button"
                        key={p.patient_id}
                        className={`signin__patient ${active ? "signin__patient--active" : ""}`}
                        onClick={() => setSelectedPatientId(p.patient_id)}
                      >
                        <div className="spread">
                          <strong>{p.display_name}</strong>
                          {p.timestamp ? (
                            <TierBadge tier={p.tier} kind={p.alert_kind} />
                          ) : (
                            <span className="small muted">Awaiting</span>
                          )}
                        </div>
                        <div className="small muted" style={{ marginTop: 4 }}>
                          {p.age_years}y {p.sex} · {SCENARIO_LABEL[p.scenario] ?? p.scenario}
                        </div>
                        {p.timestamp && (
                          <div className="small muted" style={{ marginTop: 2 }}>
                            Status: {TIER_LABEL[p.tier]}
                          </div>
                        )}
                      </button>
                    );
                  })}
                </div>
              )}

              <label className="field">
                <span className="field__label">Patient portal PIN (demo)</span>
                <input
                  className="input"
                  type="password"
                  value={patientPin}
                  onChange={(e) => setPatientPin(e.target.value)}
                  placeholder="1234"
                />
              </label>

              <div className="signin__features small muted">
                Includes: Smartwatch on wrist + live vital graphs · WhatsApp alert trigger ·
                3D Body Twin view · Medications &amp; Personal Health Summary report
              </div>

              <button type="submit" className="btn btn--primary" style={{ width: "100%" }}>
                Sign in to Patient Dashboard →
              </button>
            </form>
          </Panel>
        )}
      </section>
    </div>
  );
}
