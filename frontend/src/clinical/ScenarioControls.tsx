import { useRef, useState } from "react";
import { clinicalApi } from "./api";
import type { PatientRow, ReplayStatus } from "./types";
import { Panel, SCENARIO_LABEL, hours } from "./ui";

/** Scenario replay control and per-patient progress (§3.2, §3.7). */
export function ScenarioControls({
  replay,
  rows,
  onChanged,
}: {
  replay: ReplayStatus | null;
  rows: PatientRow[];
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  const running = replay?.running ?? false;
  const streams = replay?.streams ?? [];
  const restarting = streams.length > 0;

  const run = async (action: "start" | "stop" | "reset") => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(action);
    setError(null);
    try {
      if (action === "start") {
        // Check fresh status: the socket/REST snapshot may lag behind a command.
        const current = await clinicalApi.replayStatus();
        if (current.running) return;
        if (current.streams.length > 0) {
          // Start rebuilds sources from time zero; it cannot resume a stopped run.
          await clinicalApi.replayReset();
          onChanged();
        }
        await clinicalApi.replayStart();
      } else if (action === "stop") {
        await clinicalApi.replayStop();
      } else {
        await clinicalApi.replayReset();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      // Also refresh explicit resets and a reset followed by a failed start.
      onChanged();
      inFlight.current = false;
      setBusy(null);
    }
  };

  return (
    <Panel
      title="Scenario replay"
      hint={
        replay
          ? `Virtual wearable: ${replay.time_acceleration}× accelerated · ${replay.sim_seconds_per_tick / 60} min of patient time per tick`
          : undefined
      }
      action={
        <span className="row row--tight">
          <button
            className="btn btn--primary btn--small"
            onClick={() => run("start")}
            disabled={busy !== null || !replay || running || rows.length === 0}
          >
            {busy === "start"
              ? restarting ? "Restarting…" : "Starting…"
              : running
                ? "Streaming"
                : restarting ? "Restart all scenarios" : "Start all scenarios"}
          </button>
          <button
            className="btn btn--small"
            onClick={() => run("stop")}
            disabled={busy !== null || !running}
          >
            {busy === "stop" ? "Stopping…" : "Stop"}
          </button>
          <button
            className="btn btn--danger btn--small"
            onClick={() => run("reset")}
            disabled={busy !== null}
          >
            {busy === "reset" ? "Resetting…" : "Reset"}
          </button>
        </span>
      }
    >
      {error && (
        <p className="notice notice--warn" style={{ marginBottom: 10 }}>
          {error}
        </p>
      )}
      <p className="lede" style={{ marginBottom: 12 }}>
        Every reading below travels the same path an external device would use:
        FHIR R4 Observation → HAPI storage → personalized NEWS2 → artifact check →
        explained alert. Simulated time is accelerated so a two-day deterioration
        unfolds in minutes. BIDMC recordings loop until stopped.
      </p>
      <p className="small muted" style={{ marginBottom: 12 }}>
        Stop preserves the current results but does not support resume. Restart
        resets all monitors, baselines, and alert history before replaying from
        time zero. Reset clears those results without starting; existing HAPI
        observations are not deleted.
      </p>

      {streams.length === 0 ? (
        <p className="small muted">
          {!replay ? (
            "Loading replay status…"
          ) : rows.length === 0 ? (
            "No discharge profiles loaded. Load profiles before starting replay."
          ) : (
            <>
              Press <strong>Start all scenarios</strong> to begin streaming for the{" "}
              {rows.length} discharged patients.
            </>
          )}
        </p>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Patient</th>
                <th scope="col">Source</th>
                <th scope="col">Simulated</th>
                <th scope="col">Progress</th>
                <th scope="col" className="num">
                  Readings
                </th>
                <th scope="col" className="num">
                  Dropouts
                </th>
                <th scope="col">First clinical alert</th>
                <th scope="col">Active event</th>
              </tr>
            </thead>
            <tbody>
              {streams.map((stream) => {
                const row = rows.find((r) => r.patient_id === stream.patient_id);
                const total =
                  stream.scenario === "slow_deterioration" || stream.scenario === "stable_recovery"
                    ? 48
                    : stream.scenario === "sensor_artifact"
                      ? 12
                      : null;
                const pct = total
                  ? Math.max(0, Math.min(100, (stream.elapsed_hours / total) * 100))
                  : null;
                return (
                  <tr key={stream.patient_id}>
                    <td>{row?.display_name ?? stream.patient_id}</td>
                    <td>
                      <span className="chip">
                        {stream.source === "bidmc" ? "BIDMC recording" : "Virtual wearable"}
                      </span>
                      <div className="small muted">
                        {SCENARIO_LABEL[stream.scenario] ?? stream.scenario}
                      </div>
                    </td>
                    <td className="mono nowrap">{hours(stream.elapsed_hours)}</td>
                    <td style={{ minWidth: 90 }}>
                      {stream.bidmc ? (
                        <span className="small muted">
                          {stream.bidmc.record_id} · {stream.bidmc.sample_index}/
                          {stream.bidmc.samples_total}
                        </span>
                      ) : pct === null ? (
                        <span className="muted small">—</span>
                      ) : (
                        <span
                          className="progress"
                          style={{ display: "block" }}
                          role="progressbar"
                          aria-label={`${row?.display_name ?? stream.patient_id} scenario progress`}
                          aria-valuemin={0}
                          aria-valuemax={100}
                          aria-valuenow={Math.round(pct)}
                        >
                          <span
                            className={`progress__bar${stream.finished && !stream.last_error ? " progress__bar--done" : ""}`}
                            style={{ display: "block", width: `${pct}%` }}
                          />
                        </span>
                      )}
                    </td>
                    <td className="num">{stream.emitted}</td>
                    <td className="num">{stream.dropouts}</td>
                    <td className="nowrap">
                      {stream.first_alert_tier ? (
                        <>
                          <span className={`tier tier--${stream.first_alert_tier}`}>
                            {stream.first_alert_tier}
                          </span>
                          {stream.first_alert_elapsed_s !== null && (
                            <span className="small muted">
                              {" "}
                              at {hours(stream.first_alert_elapsed_s / 3600)}
                            </span>
                          )}
                        </>
                      ) : (
                        <span className="muted small">
                          {stream.last_error
                            ? "not recorded — replay error"
                            : stream.finished ? "none — no clinical alert" : "not yet"}
                        </span>
                      )}
                    </td>
                    <td>
                      {stream.last_error ? (
                        <span className="chip chip--event" title={stream.last_error}>
                          {stream.finished ? "finished with replay errors" : "replay error"}: {stream.last_error}
                        </span>
                      ) : stream.active_events.length ? (
                        stream.active_events.map((event) => (
                          <span className="chip chip--event" key={event} style={{ marginRight: 4 }}>
                            {event}
                          </span>
                        ))
                      ) : (
                        <span className="muted small">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {replay?.scenarios && (
        <div className="grid grid--3" style={{ marginTop: 14 }}>
          {replay.scenarios.map((scenario) => (
            <div className="evidence__block" key={scenario.key}>
              <div className="evidence__rule-title" style={{ fontSize: 12.5 }}>
                {scenario.label}
              </div>
              <div className="evidence__effect small">{scenario.description}</div>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}