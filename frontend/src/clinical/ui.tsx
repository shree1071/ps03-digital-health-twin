import type { ReactNode } from "react";
import type { AlertKind, Tier } from "./types";
import { DISCLAIMER } from "./types";

export const TIER_LABEL: Record<Tier, string> = {
  NORMAL: "Normal",
  WATCH: "Watch",
  REVIEW: "Review",
  URGENT: "Urgent",
};

export const TIER_RANK: Record<Tier, number> = {
  NORMAL: 0,
  WATCH: 1,
  REVIEW: 2,
  URGENT: 3,
};

export const ALERT_KIND_LABEL: Record<AlertKind, string> = {
  CLINICAL: "Clinical",
  SENSOR: "Sensor issue",
  DEVICE_OFFLINE: "Device offline",
};

/** Clinical tiers and device problems are visually distinct on purpose. */
export function tierClass(tier: Tier, kind?: AlertKind | null): string {
  if (kind && kind !== "CLINICAL") return `tier--${kind}`;
  return `tier--${tier}`;
}

export function tierText(tier: Tier, kind?: AlertKind | null): string {
  if (kind === "SENSOR") return "Sensor";
  if (kind === "DEVICE_OFFLINE") return "Offline";
  return TIER_LABEL[tier];
}

export function TierBadge({
  tier,
  kind,
  large = false,
}: {
  tier: Tier;
  kind?: AlertKind | null;
  large?: boolean;
}) {
  const suppressed = kind != null && kind !== "CLINICAL";
  return (
    <span className={`tier ${tierClass(tier, kind)}${large ? " tier--lg" : ""}`}>
      <span className="tier__dot" aria-hidden />
      {tierText(tier, kind)}
      {suppressed && <span className="sr-only"> — clinical alert suppressed</span>}
    </span>
  );
}

export function Score({ value, big = false }: { value: number | null; big?: boolean }) {
  const level = value === null ? 0 : Math.min(Math.max(value, 0), 3);
  return (
    <span className={`score score--${level}${big ? " score--big" : ""}`}>
      {value === null ? "–" : value}
    </span>
  );
}

export function Panel({
  title,
  hint,
  action,
  children,
  flush = false,
  className = "",
}: {
  title?: string;
  hint?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  flush?: boolean;
  className?: string;
}) {
  return (
    <section className={`panel${flush ? " panel--flush" : ""} ${className}`.trim()}>
      {(title || action) && (
        <div className="panel__head" style={flush ? { padding: "14px 16px 0" } : undefined}>
          {title && <h2 className="panel__title">{title}</h2>}
          {hint && <span className="panel__hint">{hint}</span>}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function Metric({
  label,
  value,
  unit,
  delta,
  good,
  note,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  delta?: string;
  good?: boolean;
  note?: string;
}) {
  return (
    <div className="metric">
      <div className="metric__label">{label}</div>
      <div className="metric__value">
        {value}
        {unit && <small>{unit}</small>}
      </div>
      {delta && (
        <div className={`metric__delta metric__delta--${good ? "good" : "bad"}`}>{delta}</div>
      )}
      {note && <div className="metric__note">{note}</div>}
    </div>
  );
}

export function Disclaimer({ text = DISCLAIMER }: { text?: string }) {
  return (
    <div className="disclaimer" role="note">
      <svg width="15" height="15" viewBox="0 0 20 20" aria-hidden>
        <path
          d="M10 2.6 18.4 17H1.6L10 2.6Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
        <path d="M10 7.6v4.1" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        <circle cx="10" cy="14.3" r="0.95" fill="currentColor" />
      </svg>
      <span>{text}</span>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

/** Backend timestamps are naive UTC; render them as such, never as local time. */
export function clock(iso: string | null | undefined, seconds = true): string {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || /[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: seconds ? "2-digit" : undefined,
    timeZone: "UTC",
  });
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || /[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
  });
}

export function hours(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(digits)} h`;
}

export function num(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  return value.toFixed(digits);
}

export const SCENARIO_LABEL: Record<string, string> = {
  stable_recovery: "Stable recovery",
  slow_deterioration: "Slow deterioration",
  sensor_artifact: "Sensor artifact",
  bidmc_replay: "BIDMC replay",
};

export const ARTIFACT_LABEL: Record<string, string> = {
  PROBE_REMOVAL: "Probe removal",
  FLATLINE: "Flatlined signal",
  IMPLAUSIBLE: "Implausible value",
  MOTION_NOISE: "Motion noise",
  DROPOUT: "Reading dropout",
};