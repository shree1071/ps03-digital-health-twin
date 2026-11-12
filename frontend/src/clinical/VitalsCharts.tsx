import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { LOINC, type Metric } from "./api";
import type { Observation } from "./types";
import { clock } from "./ui";

const PANEL_METRICS: Metric[] = ["heart_rate", "spo2", "respiratory_rate", "temperature"];

const DIGITS: Record<string, number> = {
  heart_rate: 0,
  spo2: 1,
  respiratory_rate: 1,
  temperature: 2,
  systolic_bp: 0,
};

interface Point {
  t: number;
  value: number | null;
}

function toPoints(readings: Observation[], metric: Metric): Point[] {
  return readings.map((r) => {
    const raw = r[metric];
    const timestamp = r.timestamp.endsWith("Z") || /[+-]\d\d:\d\d$/.test(r.timestamp)
      ? r.timestamp
      : `${r.timestamp}Z`;
    return {
      t: new Date(timestamp).getTime(),
      value: raw === null || raw === undefined || !Number.isFinite(raw) ? null : raw,
    };
  }).filter((point) => Number.isFinite(point.t)).sort((a, b) => a.t - b.t);
}

function VitalChart({
  metric,
  readings,
  baseline,
  untrusted,
  height = 132,
}: {
  metric: Metric;
  readings: Observation[];
  baseline?: { center: number; spread: number };
  untrusted: boolean;
  height?: number;
}) {
  const meta = LOINC[metric];
  const data = toPoints(readings, metric);
  const present = data.filter((d) => d.value !== null);
  const digits = DIGITS[metric] ?? 1;

  return (
    <figure style={{ margin: 0, minWidth: 0 }}>
      <figcaption
        className="spread"
        style={{ marginBottom: 4, alignItems: "baseline" }}
      >
        <span style={{ fontSize: 12.5, fontWeight: 640 }}>
          {meta.label}
          <span className="muted" style={{ fontWeight: 500 }}>
            {" "}
            ({meta.unit})
          </span>
        </span>
        {untrusted ? (
          <span className="chip chip--sensor">latest signal untrusted</span>
        ) : baseline ? (
          <span className="muted" style={{ fontSize: 11 }}>
            baseline {baseline.center.toFixed(digits)} ±{(3 * baseline.spread).toFixed(digits)}
          </span>
        ) : (
          <span className="muted" style={{ fontSize: 11 }}>
            baseline learning
          </span>
        )}
      </figcaption>
      {present.length === 0 ? (
        <div
          className="empty"
          style={{ height, display: "grid", placeItems: "center", padding: 0 }}
        >
          {readings.length === 0 ? "No readings available yet" : "No values reported in the available readings"}
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={height}>
          <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
            <CartesianGrid stroke="#D5DEE3" strokeDasharray="2 4" vertical={false} />
            <XAxis
              dataKey="t"
              type="number"
              scale="time"
              domain={["dataMin", "dataMax"]}
              tickFormatter={(t) => clock(new Date(Number(t)).toISOString(), false)}
              tick={{ fontSize: 10, fill: "#5B6F7C" }}
              minTickGap={44}
            />
            <YAxis
              domain={["auto", "auto"]}
              tick={{ fontSize: 10, fill: "#5B6F7C" }}
              width={46}
              tickFormatter={(v) => Number(v).toFixed(digits)}
            />
            <Tooltip
              labelFormatter={(t) => `${clock(new Date(Number(t)).toISOString())} UTC`}
              formatter={(v) => [`${Number(v).toFixed(digits)} ${meta.unit}`, meta.label]}
            />
            {baseline && (
              <ReferenceArea
                y1={baseline.center - 3 * baseline.spread}
                y2={baseline.center + 3 * baseline.spread}
                fill="#0E7C7B"
                fillOpacity={0.09}
                stroke="none"
                ifOverflow="extendDomain"
              />
            )}
            <Line
              dataKey="value"
              stroke={untrusted ? "#4B57A6" : "#1D2B36"}
              strokeWidth={1.7}
              dot={present.length === 1 ? { r: 3 } : false}
              connectNulls={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </figure>
  );
}

/**
 * Small multiples on a shared time axis. Seeing the vitals together is the
 * point: genuine deterioration moves several channels coherently, a sensor
 * fault moves one impossibly.
 */
export function VitalsCharts({
  readings,
  baselines,
  untrustedMetrics = [],
  includeBp = false,
}: {
  readings: Observation[];
  baselines: Record<string, { center: number; spread: number }>;
  untrustedMetrics?: string[];
  includeBp?: boolean;
}) {
  const metrics: Metric[] = includeBp ? [...PANEL_METRICS, "systolic_bp"] : PANEL_METRICS;
  return (
    <div className="grid grid--2">
      {metrics.map((metric) => (
        <VitalChart
          key={metric}
          metric={metric}
          readings={readings}
          baseline={baselines[metric]}
          untrusted={untrustedMetrics.includes(metric)}
        />
      ))}
    </div>
  );
}