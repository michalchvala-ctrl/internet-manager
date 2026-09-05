import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, type Device } from "../api";

const CATEGORIES = [
  { value: "child", label: "Dieťa" },
  { value: "pc", label: "PC" },
  { value: "tv", label: "TV" },
  { value: "other", label: "Iné" },
];

export function DevicesPage() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    name: "",
    mac: "",
    address_list: "",
    category: "child",
    notes: "",
  });

  const load = useCallback(async () => {
    try {
      setDevices(await api.devices());
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
      await api.createDevice({
        name: form.name.trim(),
        mac: form.mac.trim(),
        address_list: form.address_list.trim() || `kids-${form.name.trim().toLowerCase().replace(/\s+/g, "-")}`,
        category: form.category,
        notes: form.notes.trim() || undefined,
      });
      setForm({ name: "", mac: "", address_list: "", category: "child", notes: "" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Chyba");
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(id: number) {
    if (!confirm("Naozaj zmazať zariadenie?")) return;
    try {
      await api.deleteDevice(id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Chyba");
    }
  }

  return (
    <>
      <div className="panel">
        <h2>Nové zariadenie</h2>
        {error && <p className="error">{error}</p>}
        <form className="form-grid two" onSubmit={onCreate}>
          <div className="field">
            <label>Názov</label>
            <input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="Anička"
              required
            />
          </div>
          <div className="field">
            <label>MAC</label>
            <input
              value={form.mac}
              onChange={(e) => setForm({ ...form, mac: e.target.value })}
              placeholder="AA:BB:CC:DD:EE:FF"
              required
            />
          </div>
          <div className="field">
            <label>Address-list (MikroTik)</label>
            <input
              value={form.address_list}
              onChange={(e) => setForm({ ...form, address_list: e.target.value })}
              placeholder="kids-anicka"
            />
          </div>
          <div className="field">
            <label>Kategória</label>
            <select
              value={form.category}
              onChange={(e) => setForm({ ...form, category: e.target.value })}
            >
              {CATEGORIES.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>
          <div className="field" style={{ gridColumn: "1 / -1" }}>
            <label>Poznámka</label>
            <input
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
            />
          </div>
          <button className="btn" type="submit" disabled={busy}>
            {busy ? "Ukladám…" : "Pridať zariadenie"}
          </button>
        </form>
      </div>

      <div className="panel">
        <h2>Zoznam</h2>
        {devices.length === 0 ? (
          <p className="empty">Žiadne zariadenia</p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Názov</th>
                <th>MAC</th>
                <th>List</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {devices.map((d) => (
                <tr key={d.id}>
                  <td>{d.name}</td>
                  <td>{d.mac}</td>
                  <td>{d.address_list}</td>
                  <td>
                    <div className="row-actions">
                      <button className="btn danger" type="button" onClick={() => void onDelete(d.id)}>
                        Zmazať
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
