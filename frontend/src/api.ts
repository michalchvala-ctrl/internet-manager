export type User = {
  id: number;
  username: string;
  is_admin: boolean;
  is_active: boolean;
  created_at: string;
};

export type Device = {
  id: number;
  name: string;
  mac: string;
  address_list: string;
  category: "child" | "pc" | "tv" | "other" | string;
  sort_order: number;
  notes: string | null;
  owner_id: number | null;
  internet_blocked: boolean;
  social_blocked: boolean;
  internet_blocked_since: string | null;
  social_blocked_since: string | null;
  created_at: string;
};

export type Status = {
  mikrotik_configured: boolean;
  mikrotik_ok: boolean | null;
  mikrotik_error: string | null;
  adguard_configured: boolean;
  adguard_ok: boolean | null;
  adguard_error: string | null;
};

const TOKEN_KEY = "im_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let res: Response;
  try {
    res = await fetch(path, { ...options, headers });
  } catch {
    throw new Error("Sieťová chyba – skús to znova (alebo otvor appku cez LAN IP Unraidu).");
  }

  if (res.status === 204) return undefined as T;

  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }

  if (!res.ok) {
    if (res.status === 502 || res.status === 504) {
      throw new Error(
        "Server nestihl odpovedať (502). Skús cez LAN: http://192.168.1.10:8624 alebo skontroluj Cloudflare timeout.",
      );
    }
    const detail =
      typeof data === "object" && data && "detail" in data
        ? String((data as { detail: unknown }).detail)
        : text.trim().startsWith("<")
          ? `Chyba ${res.status} od proxy`
          : text.trim().slice(0, 200) || `Chyba ${res.status}`;
    throw new Error(detail);
  }
  return data as T;
}

export const api = {
  login: async (username: string, password: string) => {
    const body = new URLSearchParams();
    body.set("username", username);
    body.set("password", password);
    return request<{ access_token: string }>("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
  },
  me: () => request<User>("/api/auth/me"),
  users: () => request<User[]>("/api/auth/users"),
  createUser: (data: { username: string; password: string; is_admin: boolean }) =>
    request<User>("/api/auth/users", { method: "POST", body: JSON.stringify(data) }),
  updateUser: (id: number, data: Partial<{ password: string; is_admin: boolean; is_active: boolean }>) =>
    request<User>(`/api/auth/users/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  deleteUser: (id: number) => request<void>(`/api/auth/users/${id}`, { method: "DELETE" }),
  getUserDevices: (id: number) =>
    request<{ user_id: number; device_ids: number[] }>(`/api/auth/users/${id}/devices`),
  setUserDevices: (id: number, device_ids: number[]) =>
    request<{ user_id: number; device_ids: number[] }>(`/api/auth/users/${id}/devices`, {
      method: "PUT",
      body: JSON.stringify({ device_ids }),
    }),

  status: () => request<Status>("/api/status"),
  devices: () => request<Device[]>("/api/devices"),
  createDevice: (data: {
    name: string;
    mac: string;
    address_list: string;
    category: string;
    sort_order?: number;
    notes?: string;
  }) => request<Device>("/api/devices", { method: "POST", body: JSON.stringify(data) }),
  updateDevice: (id: number, data: Record<string, unknown>) =>
    request<Device>(`/api/devices/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  deleteDevice: (id: number) => request<void>(`/api/devices/${id}`, { method: "DELETE" }),
  toggleInternet: (id: number, blocked: boolean) =>
    request<Device>(`/api/devices/${id}/internet`, {
      method: "POST",
      body: JSON.stringify({ blocked }),
    }),
  toggleSocial: (id: number, blocked: boolean) =>
    request<Device>(`/api/devices/${id}/social`, {
      method: "POST",
      body: JSON.stringify({ blocked }),
    }),
};
