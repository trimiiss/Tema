// API client for the public booking chat (`/book`). Deliberately separate
// from `lib/api.ts`: that client attaches a Supabase session bearer token to
// every request, and this page has no session — the visitor hasn't logged in
// and never will. Identity here is a UUID the browser mints once and keeps in
// localStorage, sent as `session_id` in the body/query instead of a header.

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";
const SESSION_KEY = "clinic_booking_session_id";

/** The visitor's session id, creating one on first visit. */
export function getSessionId(): string {
  if (typeof window === "undefined") return "";
  let id = window.localStorage.getItem(SESSION_KEY);
  if (!id) {
    id = crypto.randomUUID();
    window.localStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers ?? {}) },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    const detail = err.detail;
    throw new Error(
      Array.isArray(detail)
        ? detail.map((d: any) => d.msg).join(", ")
        : typeof detail === "string" ? detail : "Request failed"
    );
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export type Doctor = { id: string; full_name: string; specialty: string | null; bio: string | null };
export type Reason = { reason: string; label: string; specialty: string };

export const publicBookingApi = {
  listDoctors: () => request<Doctor[]>("/public/doctors"),
  listReasons: () => request<Reason[]>("/public/reasons"),
  slots: (staffId: string, date: string, durationMin = 30) => {
    const qs = new URLSearchParams({ staff_id: staffId, date, duration_min: String(durationMin) });
    return request<{ staff_id: string; date: string; slots: string[] }>(`/public/slots?${qs}`);
  },

  sendMessage: (message: string) =>
    request<any>("/public/booking/chat", {
      method: "POST",
      body: JSON.stringify({ session_id: getSessionId(), message }),
    }),

  listRuns: () =>
    request<any[]>(`/public/booking/runs?${new URLSearchParams({ session_id: getSessionId() })}`),

  decide: (gateId: string, decision: "approved" | "rejected") =>
    request<any>(`/public/booking/confirm/${gateId}`, {
      method: "POST",
      body: JSON.stringify({ session_id: getSessionId(), decision }),
    }),

  // Server-Sent Events over fetch, same frame parser as `lib/api.ts` — no
  // Authorization header here, but EventSource still can't easily be aborted
  // via AbortSignal, so the manual reader loop is kept for consistency and
  // for the `signal` support RunDetail's cleanup relies on.
  streamRun: async (runId: string, onEvent: (event: any) => void, signal?: AbortSignal) => {
    const qs = new URLSearchParams({ session_id: getSessionId() });
    const res = await fetch(`${BASE}/public/booking/runs/${runId}/stream?${qs}`, { signal });
    if (!res.ok || !res.body) throw new Error("Failed to open booking stream");

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let sep;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const rawEvent = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        for (const line of rawEvent.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          try {
            onEvent(JSON.parse(line.slice(6)));
          } catch {}
        }
      }
    }
  },
};
