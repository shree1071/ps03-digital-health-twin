import { useCallback, useEffect, useState, type ReactNode } from "react";
import "./clinical.css";
import { useWard } from "./hooks";
import { FhirPage } from "./pages/FhirPage";
import { PatientPage } from "./pages/PatientPage";
import { ReportPage } from "./pages/ReportPage";
import { WardPage } from "./pages/WardPage";
import { Disclaimer } from "./ui";

type Route =
  | { page: "ward" }
  | { page: "patient"; id: string }
  | { page: "report" }
  | { page: "fhir"; id: string | null }
  | { page: "twin" };

function parseHash(): Route {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const [head, tail] = raw.split("/");
  if (head === "patient" && tail) return { page: "patient", id: tail };
  if (head === "report") return { page: "report" };
  if (head === "fhir") return { page: "fhir", id: tail || null };
  if (head === "legacy") return { page: "twin" };
  return { page: "ward" };
}

const NAV = [
  { key: "ward", label: "Ward", href: "#/ward" },
  { key: "report", label: "Performance", href: "#/report" },
  { key: "fhir", label: "FHIR storage", href: "#/fhir" },
  { key: "twin", label: "Twin sandbox", href: "#/legacy/overview" },
] as const;

export function ClinicalApp({ digitalTwin }: { digitalTwin: ReactNode }) {
  const [route, setRoute] = useState<Route>(parseHash);
  const ward = useWard();
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    const onHash = () => setRoute(parseHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const go = useCallback((hash: string) => {
    window.location.hash = hash;
    window.scrollTo({ top: 0 });
  }, []);

  const openPatient = useCallback(
    (id: string) => {
      setSelected(id);
      go(`#/patient/${id}`);
    },
    [go],
  );

  const activeNav = route.page === "patient" ? "ward" : route.page;
  const urgent = ward.rows.filter(
    (r) => r.timestamp && r.alert_kind === "CLINICAL" && !r.suppressed && r.tier === "URGENT",
  ).length;

  return (
    <div className="clinical">
      <header className="clinical__header">
        <div className="clinical__bar">
          <div className="clinical__brand">
            <span className="clinical__mark" aria-hidden>
              <svg width="20" height="20" viewBox="0 0 24 24">
                <path
                  d="M2 12h4l2.5-6 3.5 12 3-9 2 3h5"
                  fill="none"
                  stroke="#EEF2F4"
                  strokeWidth="1.9"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </span>
            <div>
              <div className="clinical__title">Post-Discharge Deterioration Monitor</div>
              <div className="clinical__subtitle">
                MineArena 1.0 · PS-03 · Torque Titans, BIET Davangere
              </div>
            </div>
          </div>

          <nav className="clinical__nav" aria-label="Sections">
            {NAV.map((item) => (
              <a
                key={item.key}
                href={item.href}
                aria-current={activeNav === item.key ? "page" : undefined}
              >
                {item.label}
                {item.key === "ward" && urgent > 0 && (
                  <span className="tier tier--URGENT" style={{ marginLeft: 7 }}>
                    {urgent}
                  </span>
                )}
              </a>
            ))}
          </nav>

          <span
            className={`pill pill--${ward.connection === "live" ? "live" : ward.connection === "offline" ? "off" : ""}`}
            role="status"
          >
            <span className="pill__dot" aria-hidden />
            {ward.connection === "live"
              ? ward.replay?.running
                ? "Streaming"
                : "Connected"
              : ward.connection === "offline"
                ? "Reconnecting"
                : "Connecting"}
          </span>
        </div>
        <Disclaimer />
      </header>

      <main className="clinical__main">
        {ward.connection === "offline" && ward.rows.length === 0 && (
          <section className="panel error" style={{ marginBottom: 16 }}>
            The backend isn't reachable. Start it with{" "}
            <code className="code">python -m uvicorn backend.app.main:app --port 8000</code>{" "}
            and the FHIR server with <code className="code">scripts/start_hapi.bat</code>;
            this page reconnects by itself.
          </section>
        )}

        {route.page === "ward" && (
          <WardPage
            rows={ward.rows}
            replay={ward.replay}
            selected={selected}
            onSelect={openPatient}
            onRefresh={ward.refresh}
          />
        )}
        {route.page === "patient" && (
          <PatientPage
            patientId={route.id}
            onBack={() => go("#/ward")}
            onOpenFhir={(id) => go(`#/fhir/${id}`)}
          />
        )}
        {route.page === "twin" && (
          <div className="col">
            <div className="notice">
              Synthetic physiology sandbox. This separate demo does not calculate the
              ward patients’ clinical scores; use Ward for PS-03 monitoring.
            </div>
            {digitalTwin}
          </div>
        )}
        {route.page === "report" && <ReportPage />}
        {route.page === "fhir" && (
          <FhirPage
            rows={ward.rows}
            patientId={route.id ?? selected}
            onSelect={(id) => go(`#/fhir/${id}`)}
          />
        )}
      </main>

      <footer className="clinical__footer">
        <span>
          Synthea synthetic discharge profiles · virtual wearable simulation · public
          BIDMC recordings (recorded clinical data, not synthetic).
        </span>
        <span>·</span>
        <span>
          Built on the MIT-licensed{" "}
          <a href="https://github.com/Baranidharan16/Digital-Health-Twin">
            Digital Health Twin
          </a>{" "}
          (see NOTICE.md). 3D anatomy: BodyParts3D © DBCLS, CC BY-SA 2.1 JP.
        </span>
      </footer>
    </div>
  );
}