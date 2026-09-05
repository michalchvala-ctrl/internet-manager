import { useCallback, useEffect, useMemo, useState } from "react";
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
  { id: "0", short: "Po", full: "Pondelok" },
  { id: "1", short: "Ut", full: "Utorok" },
  { id: "2", short: "St", full: "Streda" },
  { id: "3", short: "Št", full: "Štvrtok" },
  { id: "4", short: "Pi", full: "Piatok" },
  { id: "5", short: "So", full: "Sobota" },
  { id: "6", short: "Ne", full: "Nedeľa" },
];

const ACTION_LABEL: Record<string, string> = {
  internet_on: "Internet ON",
  internet_off: "Internet OFF",
  social_on: "Sociálne ON",
  social_slow: "Sociálne SLOW",
  social_off: "Sociálne OFF",
};

const ACTION_SHORT: Record<string, string> = {
  internet_on: "Net ON",
  internet_off: "Net OFF",
  social_on: "Soc ON",
  social_slow: "SLOW",
  social_off: "Soc OFF",
};

const ACTION_CLASS: Record<string, string> = {
  internet_on: "act-inet-on",
  internet_off: "act-inet-off",
  social_on: "act-soc-on",
  social_slow: "act-soc-slow",
  social_off: "act-soc-off",
};

type Preset = {
  id: string;
  label: string;
  hint: string;
  rules: { days: string; time: string; action: ScheduleAction }[];
};

const PRESETS: Preset[] = [
  {
    id: "school-evening",
    label: "Večer Po–Pi",
    hint: "20:00 sociálne OFF · 21:00 internet OFF · 07:00 všetko ON",
    rules: [
      { days: "0,1,2,3,4", time: "20:00", action: "social_off" },
      { days: "0,1,2,3,4", time: "21:00", action: "internet_off" },
      { days: "0,1,2,3,4", time: "07:00", action: "internet_on" },
      { days: "0,1,2,3,4", time: "07:00", action: "social_on" },
    ],
  },
  {
    id: "weekend",
    label: "Víkend",
    hint: "So–Ne 21:00 sociálne OFF · 22:00 internet OFF · 09:00 ON",
    rules: [
      { days: "5,6", time: "21:00", action: "social_off" },
      { days: "5,6", time: "22:00", action: "internet_off" },
      { days: "5,6", time: "09:00", action: "internet_on" },
      { days: "5,6", time: "09:00", action: "social_on" },
    ],
  },
];

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

function rulesForDay(rules: ScheduleRule[], dayId: string): ScheduleRule[] {
  return rules
    .filter((r) =>
      r.days
        .split(",")
        .map((d) => d.trim())
        .includes(dayId),
    )
    .sort((a, b) => a.time.localeCompare(b.time));
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
  const [newDays, setNewDays] = useState<string[]>(["0", "1", "2", "3", "4"]);
  const [focusDay, setFocusDay] = useState<string | null>(null);

  const showToast = (msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast(null), 3600);
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

  const todayWeekday = useMemo(() => String(new Date().getDay() === 0 ? 6 : new Date().getDay() - 1), []);

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
    setFocusDay(null);
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
      showToast("Vyber aspoň jeden deň v kalendári");
      return;
    }
    const key = `${deviceId}-sched-add`;
    setBusyId(key);
    try {
      const rule = await api.createSchedule(deviceId, {
        days: [...newDays].sort().join(","),
        time: newTime,
        action: newAction,
        enabled: true,
      });
      setSchedules((prev) => ({
        ...prev,
        [deviceId]: [...(prev[deviceId] ?? []), rule].sort((a, b) => a.time.localeCompare(b.time)),
      }));
      showToast("Uložené – beží v Dockeri aj bez appky");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Chyba");
    } finally {
      setBusyId(null);
    }
  }

  async function applyPreset(deviceId: number, preset: Preset) {
    const key = `${deviceId}-preset`;
    setBusyId(key);
    try {
      const created: ScheduleRule[] = [];
      for (const r of preset.rules) {
        created.push(
          await api.createSchedule(deviceId, {
            days: r.days,
            time: r.time,
            action: r.action,
            enabled: true,
          }),
        );
      }
      setSchedules((prev) => ({
        ...prev,
        [deviceId]: [...(prev[deviceId] ?? []), ...created].sort((a, b) =>
          a.time.localeCompare(b.time),
        ),
      }));
      showToast(`Preset „${preset.label}“ pridaný`);
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
            title="Tools → Graphing → Queue"
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
            const liveDown = device.traffic_download_bytes ?? 0;
            const liveUp = device.traffic_upload_bytes ?? 0;
            const liveTotal = liveDown + liveUp;
            const todayDown = device.traffic_today_download_bytes ?? 0;
            const todayUp = device.traffic_today_upload_bytes ?? 0;
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
                        Dnes ↓ {formatBytes(todayDown)} · ↑ {formatBytes(todayUp)}
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
                    <div className="device-meta traffic-live" title="Živé počítadlo MikroTik queue">
                      MikroTik queue Σ {formatBytes(liveTotal)}
                      {liveTotal === 0
                        ? " · ešte 0 – po update otvor stránku a chvíľu používaj net"
                        : ""}
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
                          Appka si sama vytvorí mangle + queue podľa MAC. Vo Winboxe:
                          IP → Firewall → Mangle (`internet-manager-traffic:…`) a Queues →
                          Simple (`im-traffic-…`). Fasttrack musí mať connection-mark=no-mark
                          (appka to nastaví).
                        </div>
                      </div>
                    )}
                    {schedOpen === device.id && (
                      <div className="schedule-panel">
                        <div className="device-meta" style={{ marginBottom: 10 }}>
                          Týždenný rozvrh · {status?.timezone ?? "Europe/Bratislava"} · beží v
                          Dockeri bez appky
                        </div>

                        <div className="preset-row">
                          {PRESETS.map((p) => (
                            <button
                              key={p.id}
                              type="button"
                              className="preset-btn"
                              title={p.hint}
                              disabled={busyId === `${device.id}-preset`}
                              onClick={() => void applyPreset(device.id, p)}
                            >
                              {p.label}
                            </button>
                          ))}
                        </div>

                        {schedLoading === device.id ? (
                          <div className="device-meta">Načítavam…</div>
                        ) : (
                          <div className="week-cal" role="grid" aria-label="Týždenný rozvrh">
                            {DAY_LABELS.map((d) => {
                              const dayRules = rulesForDay(schedules[device.id] ?? [], d.id);
                              const selected = newDays.includes(d.id);
                              const isToday = d.id === todayWeekday;
                              return (
                                <div
                                  key={d.id}
                                  className={`week-col${selected ? " selected" : ""}${
                                    isToday ? " today" : ""
                                  }${focusDay === d.id ? " focus" : ""}`}
                                >
                                  <button
                                    type="button"
                                    className="week-col-head"
                                    onClick={() => {
                                      setFocusDay(d.id);
                                      setNewDays((prev) =>
                                        prev.length === 1 && prev[0] === d.id
                                          ? prev
                                          : [d.id],
                                      );
                                    }}
                                    onDoubleClick={() =>
                                      setNewDays((prev) =>
                                        prev.includes(d.id)
                                          ? prev.filter((x) => x !== d.id)
                                          : [...prev, d.id],
                                      )
                                    }
                                    title="Klik = tento deň · Dvojklik = pridať/odoberať zo výberu"
                                  >
                                    {d.short}
                                  </button>
                                  <div className="week-col-body">
                                    {dayRules.map((rule) => (
                                      <button
                                        key={`${rule.id}-${d.id}`}
                                        type="button"
                                        className={`sched-chip ${ACTION_CLASS[rule.action] ?? ""}`}
                                        title={`${ACTION_LABEL[rule.action]} · ťukni = zmazať`}
                                        disabled={busyId === `${device.id}-sched-${rule.id}`}
                                        onClick={() => {
                                          if (
                                            window.confirm(
                                              `Zmazať ${rule.time} ${ACTION_LABEL[rule.action]}?`,
                                            )
                                          ) {
                                            void removeSchedule(device.id, rule.id);
                                          }
                                        }}
                                      >
                                        <span>{rule.time}</span>
                                        <span>{ACTION_SHORT[rule.action] ?? rule.action}</span>
                                      </button>
                                    ))}
                                    {dayRules.length === 0 && (
                                      <span className="week-empty">—</span>
                                    )}
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        )}

                        <div className="schedule-form">
                          <div className="day-picks">
                            <button
                              type="button"
                              className="day-chip"
                              onClick={() => setNewDays(["0", "1", "2", "3", "4"])}
                            >
                              Po–Pi
                            </button>
                            <button
                              type="button"
                              className="day-chip"
                              onClick={() => setNewDays(["5", "6"])}
                            >
                              So–Ne
                            </button>
                            <button
                              type="button"
                              className="day-chip"
                              onClick={() => setNewDays(["0", "1", "2", "3", "4", "5", "6"])}
                            >
                              Každý deň
                            </button>
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
                          <div className="device-meta">
                            Tip: preset „Večer Po–Pi“ = sociálne 20:00 OFF, internet 21:00 OFF,
                            ráno 07:00 ON.
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
