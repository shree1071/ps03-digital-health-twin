import { useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { clinicalApi } from "./api";
import { ClinicalTwin3D } from "./ClinicalTwin3D";
import { VitalsCharts } from "./VitalsCharts";
import {
  buildDoctorPatientReport,
  buildPatientHealthReport,
  deriveOrganFindings,
  reportToHtml,
  reportToMarkdown,
  type GeneratedReport,
} from "./reportGenerator";
import { dateTime } from "./ui";

export function ReportModal({
  report,
  onClose,
}: {
  report: GeneratedReport;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [waPhone, setWaPhone] = useState("");
  const [waStatus, setWaStatus] = useState<string | null>(null);
  const [waSending, setWaSending] = useState(false);
  const [audience, setAudience] = useState<"doctor" | "patient">(
    report.kind === "patient-summary" ? "patient" : "doctor",
  );
  const twinWrapRef = useRef<HTMLDivElement | null>(null);

  const activeReport = useMemo(() => {
    const vc = report.visualContext;
    if (!vc || report.kind === "doctor-ward") return report;
    if (audience === "patient") {
      return buildPatientHealthReport(vc.state, vc.alerts, vc.readings, vc.liveOverride);
    }
    return buildDoctorPatientReport(vc.state, vc.alerts, vc.readings, vc.session);
  }, [audience, report]);

  const vc = activeReport.visualContext;
  const findings = useMemo(
    () =>
      vc
        ? deriveOrganFindings(vc.state, vc.liveOverride, activeReport.kind === "patient-summary")
        : [],
    [vc, activeReport.kind],
  );
  const flaggedCount = findings.filter((f) => f.status !== "normal").length;

  const handleDownload = () => {
    const md = reportToMarkdown(activeReport);
    const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = activeReport.filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleDownloadHtml = () => {
    let snapshot: string | null = null;
    try {
      const canvas = twinWrapRef.current?.querySelector("canvas");
      if (canvas) snapshot = canvas.toDataURL("image/png");
    } catch {
      snapshot = null;
    }
    const html = reportToHtml(activeReport, snapshot);
    const blob = new Blob([html], { type: "text/html;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = activeReport.filename.replace(/\.md$/, ".html");
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(reportToMarkdown(activeReport));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      /* ignore clipboard error */
    }
  };

  const handleSendWhatsApp = async () => {
    setWaSending(true);
    setWaStatus("Sending report via WhatsApp...");
    const summary = reportToMarkdown(activeReport).slice(0, 1400);
    try {
      const res = await clinicalApi.sendWhatsappReport({
        title: activeReport.title,
        summary_text: summary,
        whatsapp_to: waPhone.trim() || null,
      });
      if (res.job_id) {
        for (let i = 0; i < 20; i += 1) {
          await new Promise((r) => window.setTimeout(r, 1000));
          const st = await clinicalApi.whatsappStatus(res.job_id);
          if (!st.queued) {
            setWaStatus(
              st.sent
                ? `Report sent via WhatsApp to ${st.recipients?.join(", ")}.`
                : `WhatsApp failed: ${st.error ?? "unknown error"}`,
            );
            break;
          }
        }
      }
    } catch (e) {
      setWaStatus(e instanceof Error ? e.message : "Failed to send report via WhatsApp");
    } finally {
      setWaSending(false);
    }
  };

  const mailtoHref = useMemo(() => {
    const subject = encodeURIComponent(activeReport.title);
    const body = encodeURIComponent(reportToMarkdown(activeReport).slice(0, 1800));
    return `mailto:?subject=${subject}&body=${body}`;
  }, [activeReport]);

  const statusTone = !vc
    ? "normal"
    : vc.state.suppressed
      ? "watch"
      : vc.state.tier === "URGENT" || vc.state.tier === "REVIEW"
        ? "anomalous"
        : vc.state.tier === "WATCH"
          ? "watch"
          : flaggedCount > 0
            ? "watch"
            : "normal";

  return createPortal(
    <div className="report-modal" role="dialog" aria-modal="true" aria-label={activeReport.title}>
      <div className="report-modal__backdrop" onClick={onClose} />
      <div className="report-modal__dialog">
        <div className="report-modal__toolbar">
          <div className="row row--tight" style={{ alignItems: "center" }}>
            <span className="small muted">
              Report preview · ready to print/save PDF or send
            </span>
            {vc && (
              <div className="seg" role="group" aria-label="Report audience">
                <button
                  type="button"
                  className={audience === "doctor" ? "seg__btn is-active" : "seg__btn"}
                  onClick={() => setAudience("doctor")}
                >
                  Doctor Copy
                </button>
                <button
                  type="button"
                  className={audience === "patient" ? "seg__btn is-active" : "seg__btn"}
                  onClick={() => setAudience("patient")}
                >
                  Patient Copy
                </button>
              </div>
            )}
          </div>
          <div className="row row--tight">
            <button className="btn btn--small" onClick={handleCopy}>
              {copied ? "Copied!" : "Copy Markdown"}
            </button>
            <button className="btn btn--small" onClick={handleDownload}>
              Download .md
            </button>
            <button className="btn btn--small" onClick={handleDownloadHtml}>
              Download HTML
            </button>
            <a className="btn btn--small" href={mailtoHref}>
              Email Summary
            </a>
            <input
              type="tel"
              className="input"
              style={{ width: 145, padding: "4px 8px", fontSize: 12 }}
              placeholder="WhatsApp # (or w.env)"
              value={waPhone}
              onChange={(e) => setWaPhone(e.target.value)}
            />
            <button
              className="btn btn--small btn--primary"
              onClick={handleSendWhatsApp}
              disabled={waSending}
            >
              {waSending ? "Sending..." : "Send via WhatsApp"}
            </button>
            <button className="btn btn--small btn--primary" onClick={() => window.print()}>
              Print / Save PDF
            </button>
            <button className="btn btn--small" onClick={onClose} aria-label="Close report">
              Close
            </button>
          </div>
        </div>
        {waStatus && (
          <div className="notice" style={{ margin: "0 20px 10px" }}>
            {waStatus}
          </div>
        )}

        <article className="report-sheet">
          <header className="report-sheet__header">
            <div>
              <div className="small muted" style={{ textTransform: "uppercase", letterSpacing: "0.06em" }}>
                PS-03 Post-Discharge Monitor · {activeReport.kind === "patient-summary" ? "Patient Copy" : "Clinical Copy"}
              </div>
              <h2 className="report-sheet__title">{activeReport.title}</h2>
              <p className="small muted" style={{ margin: "2px 0 0" }}>
                {activeReport.subtitle}
              </p>
            </div>
            <div className="report-sheet__meta">
              <div>
                <strong>Generated:</strong> {dateTime(activeReport.generatedAt)} UTC
              </div>
              <div>
                <strong>Prepared:</strong> {activeReport.authorLabel}
              </div>
            </div>
          </header>

          <div className="report-sheet__notice">{activeReport.disclaimer}</div>

          {vc && (
            <>
              <div className={`report-finding report-finding--${statusTone}`} style={{ marginBottom: 14 }}>
                <div className="spread" style={{ alignItems: "center", gap: 8 }}>
                  <strong>
                    {vc.state.suppressed
                      ? "Sensor Signal Unreliable — Clinical Scoring Withheld"
                      : flaggedCount > 0
                        ? `${flaggedCount} Physiological System${flaggedCount > 1 ? "s" : ""} Flagged on 3D Twin`
                        : "All Monitored Systems Within Expected Range"}
                  </strong>
                  <span className={`report-badge report-badge--${statusTone}`}>
                    {vc.state.suppressed
                      ? "SENSOR"
                      : `${vc.state.tier}${vc.state.news2 ? ` · NEWS2 ${vc.state.news2.total}` : ""}`}
                  </span>
                </div>
                <p className="small" style={{ margin: "6px 0 0" }}>
                  {vc.state.explanation?.narrative ??
                    (activeReport.kind === "patient-summary"
                      ? flaggedCount > 0
                        ? "One or more vital signs are outside your usual range. Review the highlighted 3D organs below and contact your care team if you feel unwell."
                        : "Your vital signs look steady relative to your personal post-discharge baseline."
                      : "See 3D anatomical twin, baseline drift, and NEWS2 breakdown below.")}
                </p>
              </div>

              <section className="report-sheet__section">
                <h3>
                  3D Anatomical Twin &amp; What Is Flagged{" "}
                  <span className="small muted" style={{ fontWeight: 500 }}>
                    ({flaggedCount > 0 ? `${flaggedCount} system${flaggedCount > 1 ? "s" : ""} flagged` : "all systems steady"})
                  </span>
                </h3>
                <div className="report-twin-grid">
                  <div className="report-twin-box" ref={twinWrapRef}>
                    <ClinicalTwin3D
                      state={vc.state}
                      liveOverride={vc.liveOverride}
                      height="340px"
                    />
                  </div>
                  <div className="report-findings">
                    {findings.map((f) => (
                      <div key={f.organ} className={`report-finding report-finding--${f.status}`}>
                        <div className="spread" style={{ alignItems: "center" }}>
                          <strong>{f.organ}</strong>
                          <span className={`report-badge report-badge--${f.status}`}>
                            {f.status.toUpperCase()}
                          </span>
                        </div>
                        <div className="small" style={{ marginTop: 3 }}>
                          {f.metricLabel}: <strong>{f.valueText}</strong>{" "}
                          <span className="muted">({f.baselineText})</span>
                        </div>
                        <p className="small muted" style={{ margin: "4px 0 0" }}>
                          {f.summary}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              </section>

              <section className="report-sheet__section">
                <h3>Vital Sign Trend Graphs</h3>
                <VitalsCharts
                  readings={vc.readings}
                  baselines={vc.state.baselines}
                  untrustedMetrics={vc.state.artifact?.untrusted_metrics ?? []}
                />
              </section>
            </>
          )}

          {activeReport.sections.map((sec) => (
            <section className="report-sheet__section" key={sec.title}>
              <h3>{sec.title}</h3>
              {sec.paragraphs?.map((p, idx) => (
                <p key={idx}>{p}</p>
              ))}
              {sec.bullets && sec.bullets.length > 0 && (
                <ul>
                  {sec.bullets.map((b, idx) => (
                    <li key={idx}>{b}</li>
                  ))}
                </ul>
              )}
              {sec.table && sec.table.rows.length > 0 && (
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        {sec.table.headers.map((h) => (
                          <th key={h}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {sec.table.rows.map((r, rIdx) => (
                        <tr key={rIdx}>
                          {r.map((cell, cIdx) => (
                            <td key={cIdx}>{cell}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          ))}
        </article>
      </div>
    </div>,
    document.body,
  );
}
