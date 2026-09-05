import { useCallback, useEffect, useState } from "react";
import { api, type Device, type Status } from "../api";
import { useAuth } from "../auth";

const CATEGORY_LABEL: Record<string, string> = {
  child: "Dieťa",
  pc: "PC",
  tv: "TV",
  other: "Iné",
};

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
  onChange,
  label,
}: {
  checked: boolean;
  disabled?: boolean;
  onChange: (next: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      className="switch"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
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

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [devs, st] = await Promise.all([api.devices(), api.status()]);
      setDevices(devs);
      setStatus(st);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Načítanie zlyhalo");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function toggleInternet(device: Device, blocked: boolean) {
    const key = `${device.id}-inet`;
    setBusyId(key);
    try {
      const updated = await api.toggleInternet(device.id, blocked);
      setDevices((prev) => prev.map((d) => (d.id === updated.id ? updated : d)));
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Chyba");
    } finally {
      setBusyId(null);
    }
  }

  async function toggleSocial(device: Device, blocked: boolean) {
    const key = `${device.id}-soc`;
    setBusyId(key);
    try {
      const updated = await api.toggleSocial(device.id, blocked);
      setDevices((prev) => prev.map((d) => (d.id === updated.id ? updated : d)));
    } catch (err) {
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
                  </div>
                  <span className="cat">{CATEGORY_LABEL[device.category] ?? device.category}</span>
                </div>

                <div className="toggles">
                  <div className={`toggle-row ${inetOn ? "on" : "off"}`}>
                    <div className="toggle-label">
                      <strong>Internet {inetOn ? "ON" : "OFF"}</strong>
                      <small>
                        {inetOn
                          ? "Plný prístup do internetu"
                          : sinceInet
                            ? `Blokované od ${sinceInet}`
                            : "Internet blokovaný (LAN ostáva)"}
                      </small>
                    </div>
                    <Switch
                      checked={inetOn}
                      label={`Internet ${device.name}`}
                      disabled={busyId === `${device.id}-inet`}
                      onChange={(next) => void toggleInternet(device, !next)}
                    />
                  </div>

                  <div className={`toggle-row ${socialOn ? "on" : "off"}`}>
                    <div className="toggle-label">
                      <strong>Sociálne {socialOn ? "ON" : "OFF"}</strong>
                      <small>
                        {socialOn
                          ? "TikTok / IG / Snap povolené"
                          : sinceSoc
                            ? `Sociálne blokované od ${sinceSoc}`
                            : "Sociálne siete blokované"}
                      </small>
                    </div>
                    <Switch
                      checked={socialOn}
                      label={`Sociálne ${device.name}`}
                      disabled={busyId === `${device.id}-soc`}
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
