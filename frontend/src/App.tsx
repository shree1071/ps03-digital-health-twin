import { useCallback, useEffect, useState } from "react";
import { api, setActiveTwin, TWIN_ID } from "./api/client";
import type { TwinInfo } from "./api/types";
import { ClinicalApp } from "./clinical/ClinicalApp";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { LiveControls } from "./components/LiveControls";
import { PhoneWaiting } from "./components/PhoneWaiting";
import { PipelineStrip } from "./components/PipelineStrip";
import { useLiveTwin } from "./hooks/useLiveTwin";
import { fmtDateTime, setDisplayOffset, utcLabel } from "./lib/format";
import { AnalyticsPage } from "./pages/AnalyticsPage";
import { HistoryPage } from "./pages/HistoryPage";
import { MyDataPage } from "./pages/MyDataPage";
import { Overview } from "./pages/Overview";
import { SimulationPage } from "./pages/SimulationPage";
import { SystemPage } from "./pages/SystemPage";
import { TwinPage } from "./pages/TwinPage";

const PAGES = [
  { key: "overview", label: "Overview" },
  { key: "twin", label: "Twin" },
  { key: "history", label: "History" },
  { key: "analytics", label: "Analytics" },
  { key: "simulation", label: "Simulation" },
  { key: "mydata", label: "My data" },
  { key: "system", label: "System" },
] as const;

const pageFromHash = () => {
  const key = window.location.hash.replace(/^#\/?(legacy\/)?/, "");
  return PAGES.some((p) => p.key === key) ? key : "overview";
};

const STORAGE_KEY = "hhdt.twin";
const savedTwin = () => {
  try {
    return window.localStorage.getItem(STORAGE_KEY) ?? TWIN_ID;
  } catch {
    return TWIN_ID;
  }
};

export default function App() {
  return <ClinicalApp digitalTwin={<LegacyApp />} />;
}

function LegacyApp() {
  const [twinId, setTwinId] = useState(savedTwin);
  const [twins, setTwins] = useState<TwinInfo[]>([]);
  const [page, setPage] = useState(pageFromHash);

  const refreshTwins = useCallback(() => {
    api
      .twins()
      .then((list) => {
        setTwins(list);
        if (!list.some((t) => t.id === twinId)) setTwinId(TWIN_ID);
      })
      .catch(() => undefined);
  }, [twinId]);

  useEffect(() => {
    refreshTwins();
    const onHash = () => setPage(pageFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [refreshTwins]);

  const choose = (id: string) => {
    setTwinId(id);
    try {
      window.localStorage.setItem(STORAGE_KEY, id);
    } catch {
      /* storage unavailable: selection lasts for this visit */
    }
  };

  const navigate = (key: string) => {
    window.location.hash = `/legacy/${key}`;
    window.scrollTo({ top: 0 });
  };

  // Every twin-scoped API call defaults to the selected twin.
  setActiveTwin(twinId);
  return (
    <ErrorBoundary key={twinId} onReset={() => choose(TWIN_ID)}>
    <TwinApp
      key={twinId}
      twinId={twinId}
      twins={twins}
      page={page}
      onChooseTwin={(id) => {
        choose(id);
        navigate("overview");
      }}
      onTwinsChanged={refreshTwins}
      navigate={navigate}
    />
    </ErrorBoundary>
  );
}

function TwinApp({
  twinId,
  twins,
  page,
  onChooseTwin,
  onTwinsChanged,
  navigate,
}: {
  twinId: string;
  twins: TwinInfo[];
  page: string;
  onChooseTwin: (id: string) => void;
  onTwinsChanged: () => void;
  navigate: (key: string) => void;
}) {
  const twin = useLiveTwin(twinId);
  const [info, setInfo] = useState<TwinInfo | null>(null);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    api
      .twin(twinId)
      .then((t) => {
        setDisplayOffset(t.subject.utc_offset_hours);
        setInfo(t);
      })
      .catch(() => undefined);
    return () => window.clearInterval(tick);
  }, [twinId]);

  const phone = !!info?.subject.data_source?.startsWith("Android phone");
  // A phone twin grows with every sync: refresh its counts when new state arrives.
  useEffect(() => {
    if (!phone) return;
    api
      .twin(twinId)
      .then(setInfo)
      .catch(() => undefined);
  }, [phone, twinId, twin.state?.twin_time]);

  // A paired phone with no readings yet: poll until the first sync lands.
  const waiting = phone && !!info && info.observation_count === 0;
  useEffect(() => {
    if (!waiting) return;
    const id = window.setInterval(() => {
      api
        .twin(twinId)
        .then((t) => {
          if (t.observation_count > 0) window.location.reload();
        })
        .catch(() => undefined);
    }, 4000);
    return () => window.clearInterval(id);
  }, [waiting, twinId]);

  const recorded = !!info && !info.subject.is_synthetic;
  const freshness = twin.lastMessageAt
    ? Math.max(0, (now - twin.lastMessageAt) / 1000)
    : null;
  const streaming =
    !recorded && twin.connection === "live" && twin.live?.running;
  const pill =
    twin.connection === "offline"
      ? "Backend unreachable"
      : phone
        ? "Phone connected"
        : recorded
          ? "Real recorded data"
          : streaming
            ? "Live (simulated)"
            : twin.live?.running === false
              ? "Paused"
              : "Connecting";
  const version = twin.state?.twin_time ?? "";

  return (
    <section className="legacy-twin">
      <header className="topbar">
        <div className="brand">
          <svg viewBox="0 0 32 32" width="34" height="34" aria-hidden>
            <rect width="32" height="32" rx="9" fill="#1D2B36" />
            <circle cx="16" cy="9" r="4" fill="#EEF2F4" />
            <path
              d="M10 27c0-7 2-12 6-12s6 5 6 12"
              fill="none"
              stroke="#EEF2F4"
              strokeWidth="3"
              strokeLinecap="round"
            />
            <circle cx="17.5" cy="18" r="2" fill="#E0636B" />
          </svg>
          <div>
            <h2>Synthetic twin sandbox</h2>
            <label className="twin-switch">
              <span className="sr-only">Twin shown</span>
              <select
                value={twinId}
                onChange={(e) => onChooseTwin(e.target.value)}
              >
                {(twins.length ? twins : info ? [info] : []).map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.subject.pseudonym} (
                    {t.subject.is_synthetic
                      ? "simulated"
                      : t.subject.data_source?.startsWith("Android phone")
                        ? "phone"
                        : "real data"}
                    )
                  </option>
                ))}
              </select>
              {info && (
                <small>
                  {Math.round(info.subject.age_years)} years,{" "}
                  {utcLabel(info.subject.utc_offset_hours)}
                </small>
              )}
            </label>
          </div>
        </div>
        <nav className="tabs" aria-label="Twin sections">
          {PAGES.map((p) => (
            <a
              key={p.key}
              href={`#/legacy/${p.key}`}
              className={page === p.key ? "tab tab--on" : "tab"}
              aria-current={page === p.key ? "page" : undefined}
            >
              {p.label}
            </a>
          ))}
        </nav>
        <div
          className={`pill pill--${streaming ? "live" : twin.connection === "offline" ? "off" : recorded ? "real" : "idle"}`}
          role="status"
        >
          <span className="pill__dot" aria-hidden />
          {pill}
          {streaming && freshness !== null && (
            <small>
              {freshness < 1.5
                ? "updated now"
                : `${freshness.toFixed(0)} s ago`}
            </small>
          )}
        </div>
      </header>

      {recorded ? (
        <section
          className="live-controls recorded-bar"
          aria-label="Data source"
        >
          {phone ? (
            <p>
              <strong>Your phone:</strong>{" "}
              {info?.observation_count ? (
                <>
                  {info.observation_count.toLocaleString()} minutes synced from
                  Health Connect
                  {info.last_observation_at
                    ? `, latest ${fmtDateTime(info.last_observation_at)}`
                    : ""}
                  . The twin updates by itself after each sync (every minute
                  while the app is open).
                </>
              ) : (
                <>
                  paired, waiting for the first sync. Open the Health Twin app,
                  allow the permissions and tap <strong>Sync now</strong>.
                </>
              )}
            </p>
          ) : (
            <p>
              <strong>Real data:</strong>{" "}
              {info?.subject.data_source ?? "uploaded export"},{" "}
              {info?.observation_count.toLocaleString()} readings
              {info?.last_observation_at
                ? `, last reading ${fmtDateTime(info.last_observation_at)}`
                : ""}
              . The twin shows the state at the last reading; use{" "}
              <a href="#/legacy/twin">Twin → Play back</a> to watch any day on the
              body.
            </p>
          )}
          <button className="btn" onClick={() => onChooseTwin(TWIN_ID)}>
            Switch to live demo twin
          </button>
        </section>
      ) : (
        <LiveControls live={twin.live} onStatus={twin.setLive} />
      )}

      <div className="main">
        {twin.connection === "offline" && !twin.state && (
          <section className="panel error">
            The twin's backend isn't reachable. Start it with{" "}
            <code>python -m uvicorn backend.app.main:app --port 8000</code>,
            then this page reconnects by itself.
          </section>
        )}
        {waiting && page !== "mydata" && page !== "system" && (
          <PhoneWaiting twinId={twinId} onBack={() => onChooseTwin(TWIN_ID)} />
        )}
        {!waiting && page === "overview" && (
          <>
            <PipelineStrip
              state={twin.state}
              live={twin.live}
              connection={twin.connection}
              freshness={freshness}
              onNavigate={navigate}
              recorded={recorded}
            />
            <Overview twin={twin} recorded={recorded} />
          </>
        )}
        {!waiting && page === "twin" && <TwinPage twin={twin} twinId={twinId} />}
        {!waiting && page === "history" && <HistoryPage version={version} />}
        {!waiting && page === "analytics" && (
          <AnalyticsPage version={version.slice(0, 15)} />
        )}
        {!waiting && page === "simulation" && <SimulationPage />}
        {page === "mydata" && (
          <MyDataPage
            onOpenTwin={onChooseTwin}
            onTwinsChanged={onTwinsChanged}
          />
        )}
        {page === "system" && <SystemPage onStatus={twin.setLive} />}
      </div>
    </section>
  );
}
