// API client for the public booking chat (`/book`). Deliberately separate
// from `lib/api.ts`: that client attaches a Supabase session bearer token to
// every request, and this page has no session — the visitor hasn't logged in
// and never will. Identity here is a UUID the browser mints per visit, sent
// as `session_id` in the body/query instead of a header.

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";
const SESSION_KEY = "clinic_booking_session_id";
const TOUCHED_KEY = "clinic_booking_session_touched";

/** How long an idle booking conversation stays resumable. A visit to the
 * booking page is one errand: coming back hours later means booking something
 * else, not finishing last night's half-typed sentence. */
const IDLE_MS = 30 * 60 * 1000;

/** The visitor's session id, minting a fresh one for each new visit.
 *
 * `sessionStorage`, not `localStorage`: it dies with the tab, so a visitor who
 * returns tomorrow — or opens the page in a second tab — starts a clean
 * conversation instead of reading back a transcript they don't remember. That
 * also matters to the agent, not just the reader: `_history_for_session` in
 * `public_orchestrator.py` replays every run sharing this id as model context,
 * so a long-lived id feeds an old booking's details into a new request.
 *
 * A reload, or a trip elsewhere and back in the same tab, still resumes — that
 * is what keeps a mid-booking refresh from losing the thread — until `IDLE_MS`
 * of silence retires it.
 */
export function getSessionId(): string {
  if (typeof window === "undefined") return "";
  // One-time cleanup of the old forever-key: that value is exactly what made
  // every visit resume the same conversation.
  window.localStorage.removeItem(SESSION_KEY);
  const store = window.sessionStorage;
  const id = store.getItem(SESSION_KEY);
  const touched = Number(store.getItem(TOUCHED_KEY) ?? 0);
  if (!id || Date.now() - touched > IDLE_MS) return newSessionId();
  store.setItem(TOUCHED_KEY, String(Date.now()));
  return id;
}

/** Starts a fresh booking conversation and returns its id. The earlier runs
 * aren't deleted — staff still see them — they just stop being *this* chat. */
export function newSessionId(): string {
  const id = crypto.randomUUID();
  window.sessionStorage.setItem(SESSION_KEY, id);
  window.sessionStorage.setItem(TOUCHED_KEY, String(Date.now()));
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
export type Service = { id: string; name: string; duration_minutes: number; description: string | null };
export type Contact = { first_name: string; last_name: string; phone: string; email: string };

export const publicBookingApi = {
  listDoctors: () => request<Doctor[]>("/public/doctors"),
  listReasons: () => request<Reason[]>("/public/reasons"),
  listServices: () => request<Service[]>("/public/services"),
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

  // `contact` is whatever the visitor typed into the booking form, sent so the
  // patient record is written from the form rather than from the model's
  // re-typing of it. Omitted when they never opened the form.
  decide: (gateId: string, decision: "approved" | "rejected", contact?: Contact) =>
    request<any>(`/public/booking/confirm/${gateId}`, {
      method: "POST",
      body: JSON.stringify({ session_id: getSessionId(), decision, contact: contact ?? null }),
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
