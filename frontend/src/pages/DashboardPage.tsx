import { useCallback, useEffect, useState } from "react";
import {
  api,
  type Device,
  type ScheduleAction,
  type ScheduleRule,
  type SocialMode,
  type Status,
  type TrafficDay,
} from "../api";
import { useAuth } from "../auth";

const CATEGORY_LABEL: Record<string, string> = {
  child: "Dieťa",
  pc: "PC",
  tv: "TV",
  other: "Iné",
};

const DAY_LABELS = [
  { id: "0", short: "Po" },
  { id: "1", short: "Ut" },
  { id: "2", short: "St" },
  { id: "3", short: "Št" },
  { id: "4", short: "Pi" },
  { id: "5", short: "So" },
  { id: "6", short: "Ne" },
];

const ACTION_LABEL: Record<string, string> = {
  internet_on: "Internet ON",
  internet_off: "Internet OFF",
  social_on: "Sociálne ON",
  social_slow: "Sociálne SLOW",
  social_off: "Sociálne OFF",
};

function formatBytes(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = Math.max(0, n);
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  const digits = i === 0 ? 0 : i >= 3 ? 2 : 1;
  return `${v.toFixed(digits)} ${units[i]}`;
}

function formatSince(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString("sk-SK", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function socialModeOf(device: Device): SocialMode {
  if (device.social_blocked) return "off";
  if (device.social_slow) return "slow";
  return "on";
}

function formatDays(days: string): string {
  const set = new Set(days.split(",").map((d) => d.trim()).filter(Boolean));
  if (set.size === 7) return "Každý deň";
  if (["0", "1", "2", "3", "4"].every((d) => set.has(d)) && set.size === 5) return "Po–Pi";
  return DAY_LABELS.filter((d) => set.has(d.id))
    .map((d) => d.short)
    .join(" ");
}

function Switch({
  checked,
  disabled,
  pending,
  onChange,
  label,
}: {
  checked: boolean;
  disabled?: boolean;
  pending?: boolean;
  onChange: (next: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      className={`switch${pending ? " pending" : ""}`}
      role="switch"
      aria-checked={checked}
      aria-busy={pending || undefined}
      aria-label={label}
      disabled={disabled}
      onClick={() => {
        if (disabled || pending) return;
        onChange(!checked);
      }}
    />
  );
}

export function DashboardPage() {
  const { user } = useAuth();
  const [devices, setDevices] = useState<Device[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState<number | null>(null);
  const [history, setHistory] = useState<Record<number, TrafficDay[]>>({});
  const [historyLoading, setHistoryLoading] = useState<number | null>(null);
  const [schedOpen, setSchedOpen] = useState<number | null>(null);
  const [schedules, setSchedules] = useState<Record<number, ScheduleRule[]>>({});
  const [schedLoading, setSchedLoading] = useState<number | null>(null);
  const [newTime, setNewTime] = useState("20:00");
  const [newAction, setNewAction] = useState<ScheduleAction>("social_off");
  const [newDays, setNewDays] = useState<string[]>(["0", "1", "2", "3", "4", "5", "6"]);

  const showToast = (msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast(null), 3200);
  };

  const load = useCallback(async (opts?: { quiet?: boolean }) => {
    if (!opts?.quiet) setLoading(true);
    try {
      const [devs, st] = await Promise.all([api.devices(), api.status()]);
      setDevices(devs);
      setStatus(st);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Načítanie zlyhalo");
    } finally {
      if (!opts?.quiet) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function openHistory(deviceId: number) {
    if (historyOpen === deviceId) {
      setHistoryOpen(null);
      return;
    }
    setHistoryOpen(deviceId);
    if (history[deviceId]) return;
    setHistoryLoading(deviceId);
    try {
      const res = await api.trafficHistory(deviceId, 14);
      setHistory((prev) => ({ ...prev, [deviceId]: res.days }));
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Chyba histórie");
    } finally {
      setHistoryLoading(null);
    }
  }

  async function openSchedules(deviceId: number) {
    if (schedOpen === deviceId) {
      setSchedOpen(null);
      return;
    }
    setSchedOpen(deviceId);
    setSchedLoading(deviceId);
    try {
      const rows = await api.schedules(deviceId);
      setSchedules((prev) => ({ ...prev, [deviceId]: rows }));
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Chyba rozvrhu");
    } finally {
      setSchedLoading(null);
    }
  }

  async function addSchedule(deviceId: number) {
    if (newDays.length === 0) {
      showToast("Vyber aspoň jeden deň");
      return;
    }
    const key = `${deviceId}-sched-add`;
    setBusyId(key);
    try {
      const rule = await api.createSchedule(deviceId, {
        days: newDays.join(","),
        time: newTime,
        action: newAction,
        enabled: true,
      });
      setSchedules((prev) => ({
        ...prev,
        [deviceId]: [...(prev[deviceId] ?? []), rule].sort((a, b) =>
          a.time.localeCompare(b.time),
        ),
      }));
      showToast("Rozvrh uložený – beží na pozadí v Dockeri");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Chyba");
    } finally {
      setBusyId(null);
    }
  }

  async function removeSchedule(deviceId: number, ruleId: number) {
    setBusyId(`${deviceId}-sched-${ruleId}`);
    try {
      await api.deleteSchedule(ruleId);
      setSchedules((prev) => ({
        ...prev,
        [deviceId]: (prev[deviceId] ?? []).filter((r) => r.id !== ruleId),
      }));
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Chyba");
    } finally {
      setBusyId(null);
    }
  }

  async function toggleInternet(device: Device, blocked: boolean) {
    const key = `${device.id}-inet`;
    if (busyId) return;
    setBusyId(key);

    const prev = device;
    setDevices((list) =>
      list.map((d) =>
        d.id === device.id
          ? {
              ...d,
              internet_blocked: blocked,
              internet_blocked_since: blocked ? new Date().toISOString() : null,
            }
          : d,
      ),
    );

    try {
      const updated = await api.toggleInternet(device.id, blocked);
      setDevices((list) => list.map((d) => (d.id === updated.id ? { ...d, ...updated } : d)));
    } catch (err) {
      try {
        await load({ quiet: true });
      } catch {
        setDevices((list) => list.map((d) => (d.id === prev.id ? prev : d)));
      }
      showToast(err instanceof Error ? err.message : "Chyba");
    } finally {
      setBusyId(null);
    }
  }

  async function setSocial(device: Device, mode: SocialMode) {
    const key = `${device.id}-soc`;
    if (busyId) return;
    setBusyId(key);

    const prev = device;
    setDevices((list) =>
      list.map((d) =>
        d.id === device.id
          ? {
              ...d,
              social_blocked: mode === "off",
              social_slow: mode === "slow",
              social_blocked_since: mode === "off" ? new Date().toISOString() : null,
            }
          : d,
      ),
    );

    try {
      const updated = await api.setSocialMode(device.id, mode);
      setDevices((list) => list.map((d) => (d.id === updated.id ? { ...d, ...updated } : d)));
    } catch (err) {
      try {
        await load({ quiet: true });
      } catch {
        setDevices((list) => list.map((d) => (d.id === prev.id ? prev : d)));
      }
      showToast(err instanceof Error ? err.message : "Chyba");
    } finally {
      setBusyId(null);
    }
  }

  const slowKbps = status?.social_slow_limit_kbps ?? 256;

  return (
    <>
      <div className="status-row">
        <div
          className={`pill ${
            !status?.mikrotik_configured ? "warn" : status.mikrotik_ok ? "ok" : "bad"
          }`}
          title={status?.mikrotik_error ?? undefined}
        >
          <span className="dot" />
          MikroTik{" "}
          {!status?.mikrotik_configured
            ? "nenastavený"
            : status.mikrotik_ok
              ? "OK"
              : "chyba"}
        </div>
        <div
          className={`pill ${
            !status?.adguard_configured ? "warn" : status.adguard_ok ? "ok" : "bad"
          }`}
          title={status?.adguard_error ?? undefined}
        >
          <span className="dot" />
          AdGuard{" "}
          {!status?.adguard_configured
            ? "nenastavený"
            : status.adguard_ok
              ? "OK"
              : "chyba"}
        </div>
        {status?.mikrotik_webfig_url && (
          <a
            className="pill"
            href={status.mikrotik_webfig_url}
            target="_blank"
            rel="noreferrer"
            title="Grafy queue v Winbox/WebFig (Tools → Graphing)"
          >
            MikroTik grafy
          </a>
        )}
      </div>

      {(status?.mikrotik_error || status?.adguard_error) && (
        <div className="error-box">
          {status.mikrotik_error && (
            <div>
              <strong>MikroTik:</strong> {status.mikrotik_error}
            </div>
          )}
          {status.adguard_error && (
            <div style={{ marginTop: status.mikrotik_error ? 6 : 0 }}>
              <strong>AdGuard:</strong> {status.adguard_error}
            </div>
          )}
        </div>
      )}

      {loading ? (
        <p className="empty">Načítavam zariadenia…</p>
      ) : devices.length === 0 ? (
        <p className="empty">
          Zatiaľ žiadne zariadenia.
          {user?.is_admin ? " Pridaj ich v sekcii Zariadenia." : ""}
        </p>
      ) : (
        <div className="device-list">
          {devices.map((device, i) => {
            const inetOn = !device.internet_blocked;
            const mode = socialModeOf(device);
            const sinceInet = formatSince(device.internet_blocked_since);
            const sinceSoc = formatSince(device.social_blocked_since);
            const inetBusy = busyId === `${device.id}-inet`;
            const socBusy = busyId === `${device.id}-soc`;
            return (
              <article
                key={device.id}
                className="device-card"
                style={{ animationDelay: `${i * 40}ms` }}
              >
                <div className="device-head">
                  <div>
                    <h2>{device.name}</h2>
                    <div className="device-meta">{device.mac}</div>
                    <div className="traffic-row">
                      <span
                        className="traffic-today"
                        title="Od polnoci (Europe/Bratislava) doteraz"
                      >
                        Dnes ↓ {formatBytes(device.traffic_today_download_bytes)} · ↑{" "}
                        {formatBytes(device.traffic_today_upload_bytes)}
                      </span>
                      <button
                        type="button"
                        className="traffic-reset"
                        onClick={() => void openHistory(device.id)}
                      >
                        {historyOpen === device.id ? "Skryť" : "14 dní"}
                      </button>
                      <button
                        type="button"
                        className="traffic-reset"
                        onClick={() => void openSchedules(device.id)}
                      >
                        {schedOpen === device.id ? "Skryť čas" : "Rozvrh"}
                      </button>
                    </div>
                    {historyOpen === device.id && (
                      <div className="traffic-history">
                        {historyLoading === device.id ? (
                          <div className="device-meta">Načítavam…</div>
                        ) : (history[device.id] ?? []).length === 0 ? (
                          <div className="device-meta">Zatiaľ žiadne denné dáta</div>
                        ) : (
                          <table className="traffic-table">
                            <thead>
                              <tr>
                                <th>Deň</th>
                                <th>↓</th>
                                <th>↑</th>
                              </tr>
                            </thead>
                            <tbody>
                              {(history[device.id] ?? []).map((row) => (
                                <tr key={row.day}>
                                  <td>{row.day}</td>
                                  <td>{formatBytes(row.download_bytes)}</td>
                                  <td>{formatBytes(row.upload_bytes)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        )}
                        <div className="device-meta" style={{ marginTop: 8 }}>
                          Denne od polnoci do polnoci (Bratislava). „Dnes“ = od polnoci
                          doteraz. Graf v Winboxe: Tools → Graphing → Queue.
                        </div>
                      </div>
                    )}
                    {schedOpen === device.id && (
                      <div className="schedule-panel">
                        <div className="device-meta" style={{ marginBottom: 8 }}>
                          Beží na pozadí v kontajneri ({status?.timezone ?? "Europe/Bratislava"}) –
                          appka nemusí byť otvorená.
                        </div>
                        {schedLoading === device.id ? (
                          <div className="device-meta">Načítavam…</div>
                        ) : (
                          <ul className="schedule-list">
                            {(schedules[device.id] ?? []).map((rule) => (
                              <li key={rule.id}>
                                <span>
                                  <strong>{rule.time}</strong> · {formatDays(rule.days)} ·{" "}
                                  {ACTION_LABEL[rule.action] ?? rule.action}
                                </span>
                                <button
                                  type="button"
                                  className="traffic-reset"
                                  disabled={busyId === `${device.id}-sched-${rule.id}`}
                                  onClick={() => void removeSchedule(device.id, rule.id)}
                                >
                                  Zmazať
                                </button>
                              </li>
                            ))}
                            {(schedules[device.id] ?? []).length === 0 && (
                              <li className="device-meta">Zatiaľ žiadne pravidlá</li>
                            )}
                          </ul>
                        )}
                        <div className="schedule-form">
                          <div className="day-picks">
                            {DAY_LABELS.map((d) => (
                              <button
                                key={d.id}
                                type="button"
                                className={`day-chip${newDays.includes(d.id) ? " on" : ""}`}
                                onClick={() =>
                                  setNewDays((prev) =>
                                    prev.includes(d.id)
                                      ? prev.filter((x) => x !== d.id)
                                      : [...prev, d.id],
                                  )
                                }
                              >
                                {d.short}
                              </button>
                            ))}
                          </div>
                          <div className="schedule-row">
                            <input
                              type="time"
                              value={newTime}
                              onChange={(e) => setNewTime(e.target.value)}
                            />
                            <select
                              value={newAction}
                              onChange={(e) => setNewAction(e.target.value as ScheduleAction)}
                            >
                              {Object.entries(ACTION_LABEL).map(([k, label]) => (
                                <option key={k} value={k}>
                                  {label}
                                </option>
                              ))}
                            </select>
                            <button
                              type="button"
                              className="btn"
                              disabled={busyId === `${device.id}-sched-add`}
                              onClick={() => void addSchedule(device.id)}
                            >
                              Pridať
                            </button>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                  <span className="cat">{CATEGORY_LABEL[device.category] ?? device.category}</span>
                </div>

                <div className="toggles">
                  <div className={`toggle-row ${inetOn ? "on" : "off"}${inetBusy ? " busy" : ""}`}>
                    <div className="toggle-label">
                      <strong>Internet {inetOn ? "ON" : "OFF"}</strong>
                      <small>
                        {inetBusy
                          ? "Ukladám…"
                          : inetOn
                            ? "Plný prístup do internetu"
                            : sinceInet
                              ? `Blokované od ${sinceInet}`
                              : "Internet blokovaný (LAN ostáva)"}
                      </small>
                    </div>
                    <Switch
                      checked={inetOn}
                      label={`Internet ${device.name}`}
                      pending={inetBusy}
                      disabled={Boolean(busyId) && !inetBusy}
                      onChange={(next) => void toggleInternet(device, !next)}
                    />
                  </div>

                  <div
                    className={`toggle-row social-mode-row ${
                      mode === "on" ? "on" : mode === "slow" ? "slow" : "off"
                    }${socBusy ? " busy" : ""}`}
                  >
                    <div className="toggle-label">
                      <strong>
                        Sociálne{" "}
                        {mode === "on" ? "ON" : mode === "slow" ? "SLOW" : "OFF"}
                      </strong>
                      <small>
                        {socBusy
                          ? "Ukladám…"
                          : mode === "on"
                            ? "TikTok / IG / Snap naplno"
                            : mode === "slow"
                              ? `Spomalené ~${slowKbps} kbit/s (chat OK, video slabé)`
                              : sinceSoc
                                ? `Blokované od ${sinceSoc}`
                                : "Sociálne siete blokované"}
                      </small>
                    </div>
                    <div className="seg" role="group" aria-label={`Sociálne ${device.name}`}>
                      {(["on", "slow", "off"] as SocialMode[]).map((m) => (
                        <button
                          key={m}
                          type="button"
                          className={`seg-btn${mode === m ? " active" : ""}`}
                          disabled={Boolean(busyId) && !socBusy}
                          onClick={() => {
                            if (socBusy || mode === m) return;
                            void setSocial(device, m);
                          }}
                        >
                          {m === "on" ? "ON" : m === "slow" ? "SLOW" : "OFF"}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {toast && <div className="toast">{toast}</div>}
    </>
  );
}
