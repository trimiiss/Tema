"use client";
import { useEffect, useRef, useState } from "react";
import toast from "react-hot-toast";
import { publicBookingApi, newSessionId, type Contact } from "@/lib/publicApi";
import { ReasonPicker, ServicePicker, DoctorPicker, SlotPicker, SlotOptionPicker, ConfirmationCard } from "./BookingCards";
import ContactForm from "./ContactForm";

// `run` is set only on messages rebuilt from history — see agent-chat's
// `messagesFromRuns` for the original version of this pattern; this is the
// same idea, minus the "restored from a staff account" framing.
type Message = { role: "user" | "agent"; text: string; runId?: string; run?: any };

const isFinished = (status?: string) => status === "completed" || status === "failed";

// How persistently to read a run back when its stream stops early — see
// `reconcile` in `BookingRunDetail`.
const RECONCILE_ATTEMPTS = 10;
const RECONCILE_DELAY_MS = 3000;

function messagesFromRuns(runs: any[]): Message[] {
  return [...runs].reverse().flatMap((run): Message[] => [
    { role: "user", text: run.input_text },
    { role: "agent", text: "", runId: run.id, run },
  ]);
}

/** Three bouncing dots — this surface never shows a raw run id or JSON trace
 * to a visitor, so "still working" needs its own quiet indicator. */
function Typing() {
  return (
    <div className="flex gap-1 py-1">
      {[0, 1, 2].map(i => (
        <span key={i} className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce"
          style={{ animationDelay: `${i * 120}ms` }} />
      ))}
    </div>
  );
}

// Tool steps that put buttons on screen, in the order the conversation walks
// through them. Everything else in a trace is bookkeeping the visitor never sees.
const PICKER_ACTIONS = new Set([
  "list_reasons", "list_services", "list_doctors", "find_specialty_for_reason",
  "list_available_slots", "find_earliest_slot", "propose_booking",
]);

/** The one picker the agent is actually asking about, out of a run's trace.
 *
 * A single turn routinely looks several things up on its way to an answer —
 * the catalogue on the way to a doctor, the doctors on the way to a day's
 * openings. Rendering every picker the trace touched drew the service cards
 * again underneath a reply about appointment times, which reads as the agent
 * having forgotten the service the visitor already picked. Only the *last*
 * picker-relevant step gets buttons: that one is the question being asked now.
 *
 * Steps stream in one at a time over SSE, so this recomputes on every render
 * rather than caching — the list is short and this is not a hot path. */
function pickerSteps(steps: any[] = []) {
  const byAction: Record<string, any> = {};
  let last = "";
  for (const s of steps) {
    byAction[s.action] = s; // later ones overwrite earlier
    if (PICKER_ACTIONS.has(s.action)) last = s.action;
  }
  // The output of `action`, but only while it is the turn's closing question.
  const asked = (action: string) => (action === last ? byAction[action]?.output : undefined);

  const earliest = asked("find_earliest_slot");
  // `propose_booking` is a picker step only when it refused: it hands back
  // either the catalogue (no service chosen yet) or this doctor's real
  // openings (the time had already passed), so the apology arrives with
  // pickable cards rather than asking the visitor to type a name or a time
  // they can't see.
  const refusal = asked("propose_booking");
  return {
    reasons: asked("list_reasons")?.reasons,
    services: asked("list_services")?.services ?? refusal?.services,
    // `find_specialty_for_reason` returns the doctors alongside the specialty,
    // so the visitor gets pickable names even on the turn where the agent only
    // worked out which specialty they need.
    doctors: asked("list_doctors")?.doctors ?? asked("find_specialty_for_reason")?.doctors,
    slots: asked("list_available_slots"),
    // `find_earliest_slot` offers several openings now, so the visitor picks
    // rather than accepting or re-typing. A run restored from history may
    // carry only the single earliest one, so fall back to that shape.
    earliestOptions: earliest?.options
      ?? (earliest?.found
        ? [{ staff_id: earliest.staff_id, staff_name: earliest.staff_name, slot: earliest.slot }]
        : []),
    refusedOptions: refusal?.options ?? [],
  };
}

/** Every doctor id -> name a run's steps reveal, read from all of them.
 *
 * Deliberately not taken from `pickerSteps`: a slot picker labels its header
 * with the doctor's name but only knows a `staff_id`, and the step that named
 * that doctor is by then no longer the turn's question. */
function doctorNamesFromSteps(steps: any[] = []): Record<string, string> {
  const names: Record<string, string> = {};
  for (const step of steps) {
    const out = step?.output ?? {};
    for (const d of out.doctors ?? []) names[d.id] = d.full_name;
    for (const o of out.options ?? []) if (o?.staff_id) names[o.staff_id] = o.staff_name;
    if (out.staff_id && out.staff_name) names[out.staff_id] = out.staff_name;
  }
  return names;
}

function BookingRunDetail({
  runId, initialRun, doctorNames, contact, isLast, onDoctorsSeen, onSend, onPickSlot,
}: {
  runId: string; initialRun?: any;
  doctorNames: Record<string, string>;
  contact?: Contact;
  isLast: boolean;
  onDoctorsSeen: (names: Record<string, string>) => void;
  onSend: (text: string) => void;
  onPickSlot: (text: string) => void;
}) {
  const [run, setRun] = useState<any>(initialRun ?? null);
  const [deciding, setDeciding] = useState(false);
  const settled = isFinished(initialRun?.status);

  useEffect(() => {
    if (!runId || settled) return;
    setRun(initialRun ?? null);
    const controller = new AbortController();
    let cancelled = false;
    // Tracked alongside the state so `reconcile` can tell a stream that ended
    // because the run finished from one that ended for any other reason.
    let status: string | undefined = initialRun?.status;

    const apply = (event: any) => {
      if (event.type === "snapshot") status = event.run?.status;
      if (event.type === "status" && event.status) status = event.status;
      setRun((prev: any) => {
        if (event.type === "snapshot") return { ...event.run, steps: event.steps, gates: event.gates };
        if (!prev) return prev;
        if (event.type === "step") {
          // The snapshot is read after the subscription opens (see
          // `api/public.stream_run`), so a step can arrive in both. Same row,
          // same id — keep the one already listed.
          const steps = prev.steps ?? [];
          if (steps.some((s: any) => s.id === event.step.id)) return prev;
          return { ...prev, steps: [...steps, event.step] };
        }
        if (event.type === "gate") {
          const gates = prev.gates ?? [];
          const idx = gates.findIndex((g: any) => g.id === event.gate.id);
          const nextGates = idx === -1 ? [...gates, event.gate] : gates.map((g: any, i: number) => (i === idx ? { ...g, ...event.gate } : g));
          return { ...prev, gates: nextGates };
        }
        if (event.type === "status") {
          const { type, ...fields } = event;
          return { ...prev, ...fields };
        }
        return prev;
      });
    };

    /** Read this run back until it settles.
     *
     * A stream that ends before its run does leaves the visitor watching three
     * bouncing dots forever — and on this surface the thing stranded on the
     * other side of that dead connection is the confirmation card they have to
     * tap to actually get an appointment. There is no single-run endpoint
     * here, so the session's own run list stands in for one. */
    async function reconcile() {
      for (let attempt = 0; attempt < RECONCILE_ATTEMPTS && !cancelled; attempt++) {
        await new Promise(r => setTimeout(r, RECONCILE_DELAY_MS));
        if (cancelled) return;
        try {
          const latest = (await publicBookingApi.listRuns()).find((r: any) => r.id === runId);
          if (latest) {
            setRun((prev: any) => ({ ...(prev ?? {}), ...latest }));
            if (latest.status !== "running") return;
          }
        } catch { /* a later attempt can still succeed */ }
      }
    }

    publicBookingApi.streamRun(runId, apply, controller.signal)
      .catch((e: any) => { if (e?.name !== "AbortError") console.error(e); })
      .finally(() => { if (!cancelled && !isFinished(status)) reconcile(); });

    return () => { cancelled = true; controller.abort(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, settled]);

  // Any doctor names this run's steps reveal get shared upward so a slot
  // picker (which only knows a staff_id) can label its buttons with a name.
  useEffect(() => {
    const names = doctorNamesFromSteps(run?.steps);
    if (Object.keys(names).length) onDoctorsSeen(names);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.steps]);

  async function decide(gateId: string, decision: "approved" | "rejected") {
    setDeciding(true);
    try {
      // The form's own values travel with the confirmation, so the patient
      // record is written from what the visitor typed rather than from the
      // model's re-typing of it.
      await publicBookingApi.decide(gateId, decision, contact);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setDeciding(false);
    }
  }

  if (!run) return <Typing />;

  const pending = run.gates?.find((g: any) => g.status === "pending");
  const decided = run.gates?.filter((g: any) => g.status !== "pending") ?? [];
  const { reasons, services, doctors, slots, earliestOptions, refusedOptions } = pickerSteps(run.steps);

  // Pickers belong to the turn the visitor is answering right now — the latest
  // one, and only until it has moved to a confirmation card. They used to be
  // hidden as soon as `run.result` arrived, which is the exact moment the
  // agent finishes asking its question: the buttons flashed past while the
  // answer streamed and were gone by the time there was anything to answer,
  // so the visitor had to type a doctor's name or a time by hand. An earlier
  // turn in the scrollback keeps its text but loses its buttons, so a resolved
  // turn still does not invite re-picking.
  const showPickers = isLast && !pending && !(run.gates?.length > 0);

  return (
    <div className="space-y-2">
      {!run.result && !pending && <Typing />}
      {run.result?.message && <p className="whitespace-pre-wrap">{run.result.message}</p>}

      {/* Interactive pickers, built straight from the agent's own tool
          results — `showPickers` decides whether this turn keeps them on
          screen, `pickerSteps` decides which single one of them belongs to it. */}
      {showPickers && reasons && <ReasonPicker reasons={reasons} onPick={onSend} />}
      {/* The opening screen offers these too, but the agent looks the
          catalogue up again whenever the visitor hasn't settled on a service —
          without this they are asked to choose from a list only the model can
          see, and have to type the name back exactly. */}
      {showPickers && services && <ServicePicker services={services} onPick={onSend} />}
      {showPickers && doctors && <DoctorPicker doctors={doctors} onPick={onSend} />}
      {showPickers && slots?.slots && (
        <SlotPicker slots={slots.slots} doctorName={doctorNames[slots.staff_id]} onPick={onPickSlot} />
      )}
      {showPickers && earliestOptions.length > 0 && (
        <SlotOptionPicker options={earliestOptions} onPick={onPickSlot} />
      )}
      {showPickers && refusedOptions.length > 0 && (
        <SlotOptionPicker options={refusedOptions} onPick={onPickSlot} />
      )}

      {pending && <ConfirmationCard gate={pending} contact={contact} onDecide={decide} deciding={deciding} />}
      {decided.map((g: any) => <ConfirmationCard key={g.id} gate={g} onDecide={decide} deciding={false} />)}
    </div>
  );
}

export default function BookingChat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [doctorNames, setDoctorNames] = useState<Record<string, string>>({});
  const [services, setServices] = useState<any[]>([]);
  // What the visitor typed into the contact form, kept verbatim so it can be
  // sent with the confirmation instead of the model's version of it.
  const [contact, setContact] = useState<Contact | undefined>();
  // Whether the contact form is expanded. Lifted out of `ContactForm` so that
  // picking a time can open it — see `pickSlot`.
  const [contactOpen, setContactOpen] = useState(false);
  // Bumped by "New chat" purely to remount `ContactForm`, which keeps the
  // typed name and phone in its own state and would otherwise carry the last
  // visitor's details into the new conversation.
  const [chatKey, setChatKey] = useState(0);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  useEffect(() => {
    let cancelled = false;
    publicBookingApi.listRuns()
      .then(runs => { if (!cancelled) setMessages(prev => (prev.length ? prev : messagesFromRuns(runs))); })
      .catch(() => { /* a fresh conversation is a fine fallback */ })
      .finally(() => { if (!cancelled) setLoadingHistory(false); });
    return () => { cancelled = true; };
  }, []);

  // The opening menu is the clinic's own service catalogue, so a visitor picks
  // something the clinic actually offers instead of guessing what to type. The
  // agent's mid-conversation pickers (reasons, doctors, slots) still come from
  // its own tool results — see `pickerSteps`.
  useEffect(() => {
    let cancelled = false;
    publicBookingApi.listServices()
      .then(rows => { if (!cancelled) setServices(rows); })
      .catch(() => { /* the free-text box below still works */ });
    return () => { cancelled = true; };
  }, []);

  function mergeDoctorNames(names: Record<string, string>) {
    setDoctorNames(prev => ({ ...prev, ...names }));
  }

  /** Starts a fresh conversation mid-visit: a new session id, so the next
   * message carries no memory of this one and `GET /public/booking/runs`
   * won't restore it either. Everything derived from the old thread —
   * doctor names, contact details — goes with it. */
  function startNewChat() {
    if (sending) return;
    newSessionId();
    setMessages([]);
    setInput("");
    setDoctorNames({});
    setContact(undefined);
    setContactOpen(false);
    setChatKey(k => k + 1);
  }

  /** Tapping a time is the point where the clinic needs to know who is
   * booking, so the contact form opens with it rather than waiting to be
   * found at the bottom of the page. */
  function pickSlot(text: string) {
    setContactOpen(true);
    send(text);
  }

  async function send(text?: string) {
    const msg = (text ?? input).trim();
    if (!msg || sending) return;
    setInput("");
    setSending(true);
    setMessages(m => [...m, { role: "user", text: msg }]);
    try {
      const run = await publicBookingApi.sendMessage(msg);
      setMessages(m => [...m, { role: "agent", text: "", runId: run.id }]);
    } catch (e: any) {
      toast.error(e.message);
      setMessages(m => [...m, { role: "agent", text: `Sorry — something went wrong: ${e.message}` }]);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="flex flex-col h-[calc(100vh-9rem)] max-w-2xl mx-auto w-full">
      {messages.length > 0 && (
        <div className="flex justify-end pb-2">
          <button onClick={startNewChat} disabled={sending}
            className="px-3 py-1.5 text-xs font-medium text-gray-500 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 hover:text-gray-900 transition disabled:opacity-40">
            + New chat
          </button>
        </div>
      )}

      <div className="flex-1 overflow-y-auto space-y-4 pb-4 px-1">
        {loadingHistory && messages.length === 0 && (
          <p className="text-sm text-gray-400">Loading…</p>
        )}
        {!loadingHistory && messages.length === 0 && (
          <div className="space-y-3">
            <div>
              <p className="text-gray-700 text-sm font-medium">
                Hi! What would you like to book?
              </p>
              <p className="text-gray-400 text-xs mt-0.5">
                Pick a service to get started — or just type below.
              </p>
            </div>
            <ServicePicker services={services} onPick={send} />
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-lg ${m.role === "user"
              ? "bg-primary-600 text-white rounded-2xl rounded-tr-sm px-4 py-3 text-sm"
              : "bg-white border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-3 text-sm text-gray-800 w-full"
            }`}>
              {m.role === "user" ? m.text : (
                m.runId
                  ? <BookingRunDetail
                      runId={m.runId} initialRun={m.run}
                      doctorNames={doctorNames}
                      contact={contact}
                      isLast={i === messages.length - 1}
                      onDoctorsSeen={mergeDoctorNames}
                      onSend={send}
                      onPickSlot={pickSlot}
                    />
                  : m.text
              )}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <div className="border-t border-gray-200 pt-3 space-y-2">
        <ContactForm key={chatKey} open={contactOpen} setOpen={setContactOpen}
          onSend={send} onContact={setContact} disabled={sending} />
        <div className="flex gap-3">
          <input
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === "Enter" && !e.shiftKey && (e.preventDefault(), send())}
            placeholder="Type a message…"
            className="flex-1 px-4 py-3 border border-gray-300 rounded-xl text-sm focus:ring-2 focus:ring-primary-500 outline-none"
            disabled={sending}
          />
          <button onClick={() => send()} disabled={sending || !input.trim()}
            className="px-5 py-3 bg-primary-600 hover:bg-primary-700 text-white font-semibold rounded-xl transition disabled:opacity-40 text-sm">
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
