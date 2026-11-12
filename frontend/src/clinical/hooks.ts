import { useCallback, useEffect, useRef, useState } from "react";
import { clinicalApi } from "./api";
import type {
  MonitorResult,
  Observation,
  PatientRow,
  PatientStatePayload,
  ReplayStatus,
} from "./types";

export type Connection = "connecting" | "live" | "offline";

const CHART_BUFFER = 240;

interface SocketOptions {
  path: string;
  onMessage: (type: string, data: unknown) => void;
  /** REST fallback while the socket is down. */
  poll: () => void;
  pollMs?: number;
}

/**
 * One reconnecting WebSocket with a REST fallback. The clinical backend pushes
 * every scored reading, so the dashboard updates within a tick; if the socket
 * drops the polling keeps the ward usable instead of freezing.
 */
function useClinicalSocket({ path, onMessage, poll, pollMs = 3000 }: SocketOptions) {
  const [connection, setConnection] = useState<Connection>("connecting");
  const [lastMessageAt, setLastMessageAt] = useState<number | null>(null);
  const handlers = useRef({ path, onMessage, poll });
  handlers.current = { path, onMessage, poll };

  useEffect(() => {
    setConnection("connecting");
    setLastMessageAt(null);
    let ws: WebSocket | null = null;
    let closed = false;
    let retry: number | undefined;
    let poller: number | undefined;

    const startPolling = () => {
      if (poller) return;
      poller = window.setInterval(() => {
        if (!closed && handlers.current.path === path) handlers.current.poll();
      }, pollMs);
    };
    const stopPolling = () => {
      if (poller) window.clearInterval(poller);
      poller = undefined;
    };

    const connect = () => {
      if (closed || handlers.current.path !== path) return;
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${window.location.host}${path}`);
      ws.onopen = () => {
        if (closed || handlers.current.path !== path) return;
        setConnection("live");
        stopPolling();
      };
      ws.onmessage = (msg) => {
        if (closed || handlers.current.path !== path) return;
        setLastMessageAt(Date.now());
        try {
          const payload = JSON.parse(msg.data) as { type: string; data: unknown };
          handlers.current.onMessage(payload.type, payload.data);
        } catch {
          /* a malformed frame must not kill the socket */
        }
      };
      ws.onclose = () => {
        if (closed || handlers.current.path !== path) return;
        setConnection("offline");
        startPolling();
        retry = window.setTimeout(connect, 2500);
      };
      ws.onerror = () => ws?.close();
    };

    handlers.current.poll();
    connect();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      stopPolling();
      ws?.close();
    };
  }, [path, pollMs]);

  return { connection, lastMessageAt };
}

export interface Ward {
  rows: PatientRow[];
  replay: ReplayStatus | null;
  connection: Connection;
  lastMessageAt: number | null;
  refresh: () => void;
}

/** Ward overview: every patient's row, live. */
export function useWard(): Ward {
  const [rows, setRows] = useState<PatientRow[]>([]);
  const [replay, setReplay] = useState<ReplayStatus | null>(null);
  const rowsVersion = useRef(0);
  const replayVersion = useRef(0);

  const merge = useCallback((next: PatientRow[]) => {
    setRows((old) => {
      const byId = new Map(old.map((r) => [r.patient_id, r]));
      for (const row of next) byId.set(row.patient_id, row);
      return [...byId.values()];
    });
  }, []);

  const refresh = useCallback(() => {
    const rowsRequest = ++rowsVersion.current;
    const replayRequest = ++replayVersion.current;
    clinicalApi.patients().then((r) => {
      if (rowsVersion.current === rowsRequest) merge(r.patients);
    }).catch(() => undefined);
    clinicalApi.replayStatus().then((r) => {
      if (replayVersion.current === replayRequest) setReplay(r);
    }).catch(() => undefined);
  }, [merge]);

  const onMessage = useCallback(
    (type: string, data: unknown) => {
      if (type === "ward_snapshot" || type === "ward") {
        rowsVersion.current += 1;
        merge(type === "ward_snapshot" ? (data as { patients: PatientRow[] }).patients : [data as PatientRow]);
      } else if (type === "replay") {
        replayVersion.current += 1;
        setReplay(data as ReplayStatus);
      }
    },
    [merge],
  );

  const { connection, lastMessageAt } = useClinicalSocket({
    path: "/ws/clinical/ward",
    onMessage,
    poll: refresh,
  });

  return { rows, replay, connection, lastMessageAt, refresh };
}

export interface PatientView {
  state: PatientStatePayload | null;
  alerts: MonitorResult[];
  readings: Observation[];
  replay: ReplayStatus | null;
  connection: Connection;
  lastMessageAt: number | null;
  error: string | null;
}

/** One patient: live state, alert history and a rolling chart buffer. */
export function usePatient(patientId: string | null): PatientView {
  const [state, setState] = useState<PatientStatePayload | null>(null);
  const [alerts, setAlerts] = useState<MonitorResult[]>([]);
  const [readings, setReadings] = useState<Observation[]>([]);
  const [replay, setReplay] = useState<ReplayStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const lastStamp = useRef<string>("");
  const lastAlertFetch = useRef(0);
  const currentPatientId = useRef(patientId);
  currentPatientId.current = patientId;
  const generation = useRef(0);
  const stateVersion = useRef(0);
  const alertsVersion = useRef(0);
  const replayVersion = useRef(0);

  const fetchAlerts = useCallback(() => {
    if (!patientId) return;
    const epoch = generation.current;
    const version = ++alertsVersion.current;
    clinicalApi
      .alerts(patientId)
      .then((r) => {
        if (
          currentPatientId.current === patientId &&
          generation.current === epoch &&
          alertsVersion.current === version
        ) setAlerts(r.alerts);
      })
      .catch(() => undefined);
  }, [patientId]);

  const accept = useCallback((next: PatientStatePayload) => {
    if (!patientId || next.patient_id !== patientId || currentPatientId.current !== patientId) return;
    stateVersion.current += 1;
    setState(next);
    setError(null);
    const stamp = next.timestamp ?? "";
    const backwards = lastStamp.current !== "" && stamp < lastStamp.current;
    if (!stamp || backwards) {
      generation.current += 1;
      alertsVersion.current += 1;
      lastAlertFetch.current = 0;
      setReadings([]);
      setAlerts([]);
    }
    if (stamp && stamp === lastStamp.current) return;
    lastStamp.current = stamp;
    if (!stamp) return;
    if (next.observation) {
      const point = next.observation;
      setReadings((old) => {
        const base = old.filter((reading) => reading.timestamp !== point.timestamp);
        return [...base.slice(-(CHART_BUFFER - 1)), point];
      });
    }
    // Alerts are not pushed frame-by-frame; refetch when something alertable
    // happens, throttled so a deteriorating patient does not hammer the API.
    const alertable = next.tier !== "NORMAL" || next.alert_kind !== "CLINICAL";
    const now = Date.now();
    if (alertable && now - lastAlertFetch.current > 2000) {
      lastAlertFetch.current = now;
      fetchAlerts();
    }
  }, [patientId, fetchAlerts]);

  const refresh = useCallback(() => {
    if (!patientId) return;
    const epoch = generation.current;
    const version = ++stateVersion.current;
    const replayRequest = ++replayVersion.current;
    const isCurrent = () => currentPatientId.current === patientId && generation.current === epoch;
    clinicalApi.state(patientId)
      .then((r) => {
        if (isCurrent() && stateVersion.current === version) accept(r);
      })
      .catch((e: Error) => {
        if (isCurrent() && stateVersion.current === version) setError(e.message);
      });
    clinicalApi.replayStatus()
      .then((r) => {
        if (isCurrent() && replayVersion.current === replayRequest) setReplay(r);
      })
      .catch(() => undefined);
  }, [patientId, accept]);

  const onMessage = useCallback(
    (type: string, data: unknown) => {
      if (currentPatientId.current !== patientId) return;
      if (type === "clinical") accept(data as PatientStatePayload);
      else if (type === "alerts" && patientId) {
        const next = (data as { alerts: MonitorResult[] }).alerts;
        if (next.some((alert) => alert.patient_id !== patientId)) return;
        alertsVersion.current += 1;
        setAlerts(next);
      } else if (type === "replay") {
        replayVersion.current += 1;
        setReplay(data as ReplayStatus);
      } else if (type === "error") setError(String((data as { detail?: string })?.detail ?? data));
    },
    [patientId, accept],
  );

  // Prime the chart and alert history from REST so the page is never empty.
  useEffect(() => {
    const epoch = ++generation.current;
    lastStamp.current = "";
    lastAlertFetch.current = 0;
    setState(null);
    setAlerts([]);
    setReadings([]);
    setReplay(null);
    setError(null);
    if (patientId) {
      clinicalApi
        .readings(patientId)
        .then((r) => {
          if (currentPatientId.current !== patientId || generation.current !== epoch) return;
          setReadings((old) => {
            const byStamp = new Map(r.readings.map((point) => [point.timestamp, point]));
            for (const point of old) byStamp.set(point.timestamp, point);
            return [...byStamp.values()]
              .sort((a, b) => a.timestamp.localeCompare(b.timestamp))
              .slice(-CHART_BUFFER);
          });
        })
        .catch(() => undefined);
      fetchAlerts();
    }
    return () => { generation.current += 1; };
  }, [patientId, fetchAlerts]);

  const { connection, lastMessageAt } = useClinicalSocket({
    path: patientId ? `/ws/clinical/${patientId}` : "/ws/clinical/ward",
    onMessage,
    poll: refresh,
  });

  return { state, alerts, readings, replay, connection, lastMessageAt, error };
}