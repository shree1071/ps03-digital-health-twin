import type { PatientRow, ReplayStream } from "./types";
import { Empty, SCENARIO_LABEL, TIER_RANK, TierBadge, clock } from "./ui";

function streamFor(streams: ReplayStream[], id: string): ReplayStream | undefined {
  return streams.find((s) => s.patient_id === id);
}

export function WardTable({
  rows,
  streams,
  selected,
  onSelect,
}: {
  rows: PatientRow[];
  streams: ReplayStream[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  if (!rows.length) {
    return (
      <Empty>
        No discharge profiles loaded. Generate them with{" "}
        <code className="code">python scripts/generate_profiles.py</code>.
      </Empty>
    );
  }

  const ordered = [...rows].sort((a, b) => {
    const rank = TIER_RANK[b.tier] - TIER_RANK[a.tier];
    if (rank !== 0) return rank;
    return (b.news2_total ?? -1) - (a.news2_total ?? -1);
  });

  return (
    <div style={{ overflowX: "auto" }}>
      <table className="ward">
        <caption className="sr-only">
          Discharged patients, most severe first. Select a row to open the patient.
        </caption>
        <thead>
          <tr>
            <th scope="col">Patient</th>
            <th scope="col">Scenario</th>
            <th scope="col">Tier</th>
            <th scope="col" className="ward__num">NEWS2</th>
            <th scope="col">Personalization</th>
            <th scope="col">Baseline drift</th>
            <th scope="col" className="ward__num">Alerts</th>
            <th scope="col" className="ward__num">Suppressed</th>
            <th scope="col" className="ward__num">FHIR obs.</th>
            <th scope="col">Last reading</th>
          </tr>
        </thead>
        <tbody>
          {ordered.map((row) => {
            const stream = streamFor(streams, row.patient_id);
            return (
              <tr
                key={row.patient_id}
                data-tier={row.tier}
                data-selected={selected === row.patient_id}
                onClick={() => onSelect(row.patient_id)}
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelect(row.patient_id);
                  }
                }}
              >
                <td>
                  <div className="ward__name">{row.display_name}</div>
                  <div className="ward__meta">
                    {row.age_years} y · {row.sex} · discharged profile
                  </div>
                </td>
                <td>
                  <div>{SCENARIO_LABEL[row.scenario] ?? row.scenario}</div>
                  {stream && (
                    <div className="ward__meta">
                      {stream.finished && stream.last_error
                        ? "finished with replay errors"
                        : stream.finished
                          ? "complete"
                          : `${stream.elapsed_hours.toFixed(1)} h simulated`}
                      {stream.active_events.length > 0 && (
                        <>
                          {" · "}
                          {stream.active_events.map((event) => (
                            <span className="chip chip--event" key={event} style={{ marginRight: 4 }}>
                              {event}
                            </span>
                          ))}
                        </>
                      )}
                    </div>
                  )}
                </td>
                <td>
                  {row.timestamp ? (
                    <TierBadge tier={row.tier} kind={row.alert_kind} />
                  ) : (
                    <span className="muted small">Awaiting reading</span>
                  )}
                </td>
                <td className="ward__num">
                  <strong style={{ fontSize: 16 }}>{row.news2_total ?? "—"}</strong>
                </td>
                <td>
                  <div className="ward__flags">
                    {row.spo2_scale === 2 ? (
                      <span className="chip chip--scale2">SpO₂ Scale 2</span>
                    ) : (
                      <span className="chip">SpO₂ Scale 1</span>
                    )}
                    {row.beta_blocked && (
                      <span className="chip chip--beta">β-blocker therapy</span>
                    )}
                  </div>
                </td>
                <td>
                  {row.drift_metrics.length ? (
                    <div className="ward__flags">
                      {row.drift_metrics.map((metric) => (
                        <span className="chip chip--drift" key={metric}>
                          {metric.replace(/_/g, " ")}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <span className="muted small">
                      {!row.timestamp
                        ? "awaiting readings"
                        : row.suppressed
                          ? "reading untrusted"
                          : row.baseline_ready ? "no sustained drift" : "learning baseline"}
                    </span>
                  )}
                </td>
                <td className="ward__num">{row.alert_count}</td>
                <td className="ward__num">
                  {row.suppressed_count > 0 ? (
                    <span className="chip chip--sensor">{row.suppressed_count}</span>
                  ) : (
                    <span className="muted">0</span>
                  )}
                </td>
                <td className="ward__num muted">{row.stored_observations}</td>
                <td className="nowrap muted small">
                  {row.timestamp ? `${clock(row.timestamp, false)} UTC` : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}