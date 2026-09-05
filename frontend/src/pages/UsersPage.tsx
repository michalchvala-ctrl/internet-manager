import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, type Device, type User } from "../api";
import { useAuth } from "../auth";

export function UsersPage() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [access, setAccess] = useState<Record<number, number[]>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [savingUser, setSavingUser] = useState<number | null>(null);
  const [form, setForm] = useState({
    username: "",
    password: "",
    is_admin: false,
  });

  const load = useCallback(async () => {
    try {
      const [u, d] = await Promise.all([api.users(), api.devices()]);
      setUsers(u);
      setDevices(d);
      const map: Record<number, number[]> = {};
      await Promise.all(
        u
          .filter((x) => !x.is_admin)
          .map(async (x) => {
            const res = await api.getUserDevices(x.id);
            map[x.id] = res.device_ids;
          }),
      );
      setAccess(map);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Chyba");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.createUser({
        username: form.username.trim(),
        password: form.password,
        is_admin: form.is_admin,
      });
      setForm({ username: "", password: "", is_admin: false });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Chyba");
    } finally {
      setBusy(false);
    }
  }

  async function toggleActive(u: User) {
    try {
      await api.updateUser(u.id, { is_active: !u.is_active });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Chyba");
    }
  }

  async function onDelete(id: number) {
    if (!confirm("Naozaj zmazať používateľa?")) return;
    try {
      await api.deleteUser(id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Chyba");
    }
  }

  async function resetPassword(u: User) {
    const password = prompt(`Nové heslo pre ${u.username}:`);
    if (!password) return;
    try {
      await api.updateUser(u.id, { password });
      alert("Heslo zmenené");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Chyba");
    }
  }

  function toggleDevice(userId: number, deviceId: number) {
    setAccess((prev) => {
      const cur = new Set(prev[userId] ?? []);
      if (cur.has(deviceId)) cur.delete(deviceId);
      else cur.add(deviceId);
      return { ...prev, [userId]: [...cur] };
    });
  }

  async function saveAccess(userId: number) {
    setSavingUser(userId);
    setError(null);
    try {
      const res = await api.setUserDevices(userId, access[userId] ?? []);
      setAccess((prev) => ({ ...prev, [userId]: res.device_ids }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Chyba");
    } finally {
      setSavingUser(null);
    }
  }

  return (
    <>
      <div className="panel">
        <h2>Nový používateľ</h2>
        {error && <p className="error">{error}</p>}
        <form className="form-grid two" onSubmit={onCreate}>
          <div className="field">
            <label>Meno</label>
            <input
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
              required
            />
          </div>
          <div className="field">
            <label>Heslo</label>
            <input
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              required
              minLength={4}
            />
          </div>
          <div className="field">
            <label>
              <input
                type="checkbox"
                checked={form.is_admin}
                onChange={(e) => setForm({ ...form, is_admin: e.target.checked })}
              />{" "}
              Admin (správa zariadení a používateľov)
            </label>
          </div>
          <button className="btn" type="submit" disabled={busy}>
            {busy ? "Ukladám…" : "Vytvoriť"}
          </button>
        </form>
      </div>

      <div className="panel">
        <h2>Používatelia</h2>
        <table className="table">
          <thead>
            <tr>
              <th>Meno</th>
              <th>Rola</th>
              <th>Stav</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>{u.username}</td>
                <td>{u.is_admin ? "admin" : "user"}</td>
                <td>{u.is_active ? "aktívny" : "vypnutý"}</td>
                <td>
                  <div className="row-actions">
                    <button className="btn secondary" type="button" onClick={() => void resetPassword(u)}>
                      Heslo
                    </button>
                    {u.id !== me?.id && (
                      <>
                        <button className="btn secondary" type="button" onClick={() => void toggleActive(u)}>
                          {u.is_active ? "Deaktivovať" : "Aktivovať"}
                        </button>
                        <button className="btn danger" type="button" onClick={() => void onDelete(u.id)}>
                          Zmazať
                        </button>
                      </>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="panel">
        <h2>Viditeľnosť zariadení</h2>
        <p className="panel-note">
          Admin vidí všetko. Bežnému používateľovi zaškrtni zariadenia, ktoré má vidieť a ovládať.
        </p>
        {users.filter((u) => !u.is_admin).length === 0 ? (
          <p className="empty">Zatiaľ žiadni bežní používatelia</p>
        ) : (
          users
            .filter((u) => !u.is_admin)
            .map((u) => (
              <div key={u.id} className="access-card">
                <div className="access-head">
                  <strong>{u.username}</strong>
                  <button
                    type="button"
                    className="btn secondary"
                    disabled={savingUser === u.id}
                    onClick={() => void saveAccess(u.id)}
                  >
                    {savingUser === u.id ? "Ukladám…" : "Uložiť"}
                  </button>
                </div>
                <div className="access-grid">
                  {devices.map((d) => {
                    const checked = (access[u.id] ?? []).includes(d.id);
                    return (
                      <label key={d.id} className={`access-chip ${checked ? "on" : ""}`}>
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleDevice(u.id, d.id)}
                        />
                        {d.name}
                      </label>
                    );
                  })}
                </div>
              </div>
            ))
        )}
      </div>
    </>
  );
}
