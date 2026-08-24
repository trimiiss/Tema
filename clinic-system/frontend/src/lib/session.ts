import { supabase } from "./supabase";

const SESSION_START_KEY = "clinic_agent_session_start";

/** When the current login began, as an ISO instant.
 *
 * Agent history is scoped to one login: replaying a run from three days ago
 * presents it as part of the current conversation, so the user reads answers
 * to questions they don't remember asking — and the agent is fed that stale
 * transcript back as context.
 *
 * Prefers Supabase's own `last_sign_in_at`, which is the actual sign-in and so
 * is right even in a fresh tab that never saw the login happen. Falls back to
 * stamping `sessionStorage` on first use, for a session whose user object
 * doesn't carry the field. `sessionStorage` rather than `localStorage`
 * deliberately: it dies with the tab, and a new tab re-derives from Supabase.
 */
export async function sessionStart(): Promise<string> {
  const cached = sessionStorage.getItem(SESSION_START_KEY);
  if (cached) return cached;
  let start: string | null = null;
  try {
    const { data } = await supabase.auth.getSession();
    start = (data.session?.user as any)?.last_sign_in_at ?? null;
  } catch {
    /* fall through to "now" */
  }
  const value = start ?? new Date().toISOString();
  sessionStorage.setItem(SESSION_START_KEY, value);
  return value;
}

/** Called on sign-out so signing back in on the same tab starts a clean thread. */
export function clearSessionStart() {
  sessionStorage.removeItem(SESSION_START_KEY);
}

const CHAT_ID_KEY = "clinic_agent_chat_id";

/** Identifies one Agent Chat conversation.
 *
 * Sent as `session_id` on every `/agent/run` so the backend can replay this
 * conversation's earlier turns back to the model as history (the same
 * mechanism the public booking chat already uses) and so `GET /agent/runs`
 * can restore exactly this thread rather than every run from the current
 * login. Kept in `sessionStorage` — like `sessionStart`, tab-scoped — so
 * navigating away and back keeps the same conversation; "New Chat" and
 * sign-out both mint a fresh one.
 */
export function chatId(): string {
  let id = sessionStorage.getItem(CHAT_ID_KEY);
  if (!id) {
    id = crypto.randomUUID();
    sessionStorage.setItem(CHAT_ID_KEY, id);
  }
  return id;
}

/** Starts a new conversation and returns its id. */
export function newChatId(): string {
  const id = crypto.randomUUID();
  sessionStorage.setItem(CHAT_ID_KEY, id);
  return id;
}

/** Called on sign-out alongside `clearSessionStart`. */
export function clearChatId() {
  sessionStorage.removeItem(CHAT_ID_KEY);
}
