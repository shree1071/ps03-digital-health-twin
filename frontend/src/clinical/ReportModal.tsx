import { useState } from "react";
import { reportToMarkdown, type GeneratedReport } from "./reportGenerator";
import { dateTime } from "./ui";

export function ReportModal({
  report,
  onClose,
}: {
  report: GeneratedReport;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);

  const handleDownload = () => {
    const md = reportToMarkdown(report);
    const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = report.filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(reportToMarkdown(report));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      /* ignore clipboard error */
    }
  };

  return (
    <div className="report-modal" role="dialog" aria-modal="true" aria-label={report.title}>
      <div className="report-modal__backdrop" onClick={onClose} />
      <div className="report-modal__dialog">
        <div className="report-modal__toolbar">
          <div className="small muted">
            Report preview · ready to print/save PDF or download
          </div>
          <div className="row row--tight">
            <button className="btn btn--small" onClick={handleCopy}>
              {copied ? "Copied!" : "Copy Markdown"}
            </button>
            <button className="btn btn--small" onClick={handleDownload}>
              Download .md
            </button>
            <button className="btn btn--small btn--primary" onClick={() => window.print()}>
              Print / Save PDF
            </button>
            <button className="btn btn--small" onClick={onClose} aria-label="Close report">
              Close
            </button>
          </div>
        </div>

        <article className="report-sheet">
          <header className="report-sheet__header">
            <div>
              <div className="small muted" style={{ textTransform: "uppercase", letterSpacing: "0.06em" }}>
                PS-03 Post-Discharge Monitor · {report.kind === "patient-summary" ? "Patient Copy" : "Clinical Copy"}
              </div>
              <h2 className="report-sheet__title">{report.title}</h2>
              <p className="small muted" style={{ margin: "2px 0 0" }}>
                {report.subtitle}
              </p>
            </div>
            <div className="report-sheet__meta">
              <div>
                <strong>Generated:</strong> {dateTime(report.generatedAt)} UTC
              </div>
              <div>
                <strong>Prepared:</strong> {report.authorLabel}
              </div>
            </div>
          </header>

          <div className="report-sheet__notice">{report.disclaimer}</div>

          {report.sections.map((sec) => (
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
    </div>
  );
}
