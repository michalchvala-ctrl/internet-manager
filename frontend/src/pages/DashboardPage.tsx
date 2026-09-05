import { useCallback, useEffect, useState } from "react";
import { api, type Device, type Status } from "../api";
import { useAuth } from "../auth";

const CATEGORY_LABEL: Record<string, string> = {
  child: "Dieťa",
  pc: "PC",
  tv: "TV",
  other: "Iné",
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

  async function toggleInternet(device: Device, blocked: boolean) {
    const key = `${device.id}-inet`;
    if (busyId) return;
    setBusyId(key);

    // Optimistic UI – mobile musí vidieť zmenu hneď
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
      // Sync zo servera – request mohol stihnúť uložiť stav aj pri timeout chybe
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

  async function toggleSocial(device: Device, blocked: boolean) {
    const key = `${device.id}-soc`;
    if (busyId) return;
    setBusyId(key);

    const prev = device;
    setDevices((list) =>
      list.map((d) =>
        d.id === device.id
          ? {
              ...d,
              social_blocked: blocked,
              social_blocked_since: blocked ? new Date().toISOString() : null,
            }
          : d,
      ),
    );

    try {
      const updated = await api.toggleSocial(device.id, blocked);
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
        <div className="pill">
          <span className="dot" />
          {user?.username}
        </div>
      </div>

      {(status?.mikrotik_error || status?.adguard_error) && (
        <div className="error" style={{ marginBottom: 14 }}>
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
            const socialOn = !device.social_blocked;
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
                      <span title="Download">↓ {formatBytes(device.traffic_download_bytes)}</span>
                      <span title="Upload">↑ {formatBytes(device.traffic_upload_bytes)}</span>
                      <button
                        type="button"
                        className="traffic-reset"
                        disabled={busyId === `${device.id}-traf`}
                        onClick={() => {
                          const key = `${device.id}-traf`;
                          setBusyId(key);
                          void api
                            .resetTraffic(device.id)
                            .then((updated) => {
                              setDevices((list) =>
                                list.map((d) => (d.id === updated.id ? { ...d, ...updated } : d)),
                              );
                            })
                            .catch((err) =>
                              showToast(err instanceof Error ? err.message : "Chyba"),
                            )
                            .finally(() => setBusyId(null));
                        }}
                      >
                        Reset
                      </button>
                    </div>
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

                  <div className={`toggle-row ${socialOn ? "on" : "off"}${socBusy ? " busy" : ""}`}>
                    <div className="toggle-label">
                      <strong>Sociálne {socialOn ? "ON" : "OFF"}</strong>
                      <small>
                        {socBusy
                          ? "Ukladám…"
                          : socialOn
                            ? "TikTok / IG / Snap povolené"
                            : sinceSoc
                              ? `Sociálne blokované od ${sinceSoc}`
                              : "Sociálne siete blokované"}
                      </small>
                    </div>
                    <Switch
                      checked={socialOn}
                      label={`Sociálne ${device.name}`}
                      pending={socBusy}
                      disabled={Boolean(busyId) && !socBusy}
                      onChange={(next) => void toggleSocial(device, !next)}
                    />
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
