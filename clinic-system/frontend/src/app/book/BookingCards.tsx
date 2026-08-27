"use client";
import { format } from "date-fns";
import type { Contact } from "@/lib/publicApi";

// These cards are read straight off the booking agent's own tool results
// (`agent_steps.output`), never off separately-fetched state — so what a
// visitor taps always matches exactly what the agent just said. Tapping one
// doesn't call any API directly; it hands a plain-language sentence back up
// to the chat, because the conversation itself stays the single source of
// truth (the agent re-validates everything server-side regardless of how the
// text arrived — see `booking_agent.propose_booking`).

export function ReasonPicker({ reasons, onPick }: { reasons: any[]; onPick: (text: string) => void }) {
  if (!reasons?.length) return null;
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">
      {reasons.map(r => (
        <button
          key={r.reason}
          onClick={() => onPick(r.label)}
          className="text-left px-3 py-2.5 bg-white border border-gray-200 rounded-xl text-sm text-gray-700 hover:border-primary-400 hover:bg-primary-50 transition"
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}

/** The clinic's service catalogue — what the booking page opens with.
 *
 * Read from `/public/services` rather than from a tool result, because this is
 * shown before the agent has been asked anything at all. Tapping one sends the
 * service by name, so the agent's own `list_services` lookup resolves it back
 * to the same row and can pass its id (and therefore its duration) through to
 * `propose_booking`. */
export function ServicePicker({ services, onPick }: { services: any[]; onPick: (text: string) => void }) {
  if (!services?.length) return null;
  return (
    <div className="grid grid-cols-1 gap-2">
      {services.map(s => (
        <button
          key={s.id}
          onClick={() => onPick(`I'd like to book a ${s.name}.`)}
          className="group text-left px-4 py-3 bg-white border border-gray-200 rounded-xl hover:border-primary-400 hover:bg-primary-50 transition"
        >
          <div className="flex items-center justify-between gap-3">
            <span className="font-medium text-sm text-gray-800">{s.name}</span>
            <span className="text-xs text-gray-400 shrink-0">{s.duration_minutes} min</span>
          </div>
          {s.description && (
            <p className="text-xs text-gray-500 mt-0.5">{s.description}</p>
          )}
        </button>
      ))}
    </div>
  );
}

export function DoctorPicker({ doctors, onPick }: { doctors: any[]; onPick: (text: string) => void }) {
  if (!doctors?.length) {
    return <p className="text-xs text-gray-400 mt-2">No doctors available for that right now.</p>;
  }
  return (
    <div className="grid grid-cols-1 gap-2 mt-2">
      {doctors.map(d => (
        <button
          key={d.id}
          onClick={() => onPick(`I'd like to see ${d.full_name}.`)}
          className="text-left px-3 py-2.5 bg-white border border-gray-200 rounded-xl text-sm hover:border-primary-400 hover:bg-primary-50 transition"
        >
          <span className="font-medium text-gray-800">{d.full_name}</span>
          {d.specialty && <span className="text-gray-500"> — {d.specialty}</span>}
          {d.bio && <p className="text-xs text-gray-400 mt-0.5">{d.bio}</p>}
        </button>
      ))}
    </div>
  );
}

function formatSlot(iso: string): string {
  try {
    return format(new Date(iso), "EEEE, MMM d 'at' HH:mm");
  } catch {
    return iso;
  }
}

/** Drop slots that have already started.
 *
 * The tools behind these cards already filter the past out server-side, but a
 * turn stays on screen after it was answered — a conversation left open over
 * lunch would otherwise still be offering this morning's buttons, and tapping
 * one only earns a refusal. Compared as instants (`getTime`), never as
 * strings: the same moment has several valid spellings. An unparseable value
 * is left alone rather than silently dropped. */
function stillOpen(slots: string[]): string[] {
  const now = Date.now();
  return (slots ?? []).filter(s => {
    const t = new Date(s).getTime();
    return Number.isNaN(t) || t > now;
  });
}

export function SlotPicker({
  slots, doctorName, onPick,
}: { slots: string[]; doctorName?: string; onPick: (text: string) => void }) {
  const open = stillOpen(slots);
  if (!open.length) {
    return <p className="text-xs text-gray-400 mt-2">No open slots left there — try another day or doctor.</p>;
  }
  return (
    <div className="flex flex-wrap gap-2 mt-2">
      {open.map(s => (
        <button
          key={s}
          onClick={() => onPick(
            `I'll take ${formatSlot(s)}${doctorName ? ` with ${doctorName}` : ""}.`
          )}
          className="px-3 py-1.5 bg-white border border-gray-200 rounded-lg text-xs font-medium text-gray-700 hover:border-primary-400 hover:bg-primary-50 transition"
        >
          {formatSlot(s)}
        </button>
      ))}
    </div>
  );
}

/** The soonest openings across several doctors — `find_earliest_slot`'s own
 * `options`, and the same list a refused `propose_booking` hands back.
 *
 * One earliest slot on its own is a take-it-or-leave-it offer: a visitor who
 * can't make it has to type a counter-offer and wait for the agent to go
 * looking again. A handful of real openings, each labelled with the doctor who
 * has it, is answered with one tap. */
export function SlotOptionPicker({
  options, onPick,
}: { options: any[]; onPick: (text: string) => void }) {
  const open = (options ?? []).filter(o => stillOpen([o.slot]).length);
  if (!open.length) {
    return <p className="text-xs text-gray-400 mt-2">Those times have gone — ask for the next opening.</p>;
  }
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">
      {open.map(o => (
        <button
          key={`${o.staff_id}-${o.slot}`}
          onClick={() => onPick(`I'll take ${formatSlot(o.slot)} with ${o.staff_name}.`)}
          className="text-left px-3 py-2.5 bg-white border border-gray-200 rounded-xl hover:border-primary-400 hover:bg-primary-50 transition"
        >
          <span className="block text-sm font-medium text-gray-800">{formatSlot(o.slot)}</span>
          <span className="block text-xs text-gray-500">{o.staff_name}</span>
        </button>
      ))}
    </div>
  );
}

export function ConfirmationCard({
  gate, contact, onDecide, deciding,
}: {
  gate: any;
  contact?: Contact;
  onDecide: (gateId: string, d: "approved" | "rejected") => void;
  deciding: boolean;
}) {
  if (gate.status !== "pending") {
    return (
      <div className={`rounded-lg px-4 py-3 text-sm font-medium ${
        gate.status === "approved" ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"
      }`}>
        {gate.status === "approved"
          ? "Appointment confirmed."
          : "No problem — that request was cancelled."}
      </div>
    );
  }
  return (
    <div className="bg-amber-50 border border-amber-300 rounded-xl p-4 space-y-3 mt-2">
      <p className="text-sm font-semibold text-amber-900">Please confirm your request</p>
      <p className="text-sm text-amber-800">{gate.action_description}</p>

      {/* The details exactly as typed into the contact form — these are what
          get saved, so the visitor confirms the real values rather than the
          agent's paraphrase of them. */}
      {contact && (
        <div className="bg-white/60 border border-amber-200 rounded-lg px-3 py-2 space-y-0.5">
          <p className="text-xs font-medium text-amber-900">We'll save these details:</p>
          <p className="text-xs text-amber-800">
            {[contact.first_name, contact.last_name].filter(Boolean).join(" ")}
          </p>
          {contact.phone && <p className="text-xs text-amber-800">{contact.phone}</p>}
          {contact.email && <p className="text-xs text-amber-800">{contact.email}</p>}
        </div>
      )}

      <p className="text-xs text-amber-700">
        Confirming books this appointment right away — no extra wait on our end.
      </p>
      <div className="flex gap-3">
        <button
          disabled={deciding}
          onClick={() => onDecide(gate.id, "approved")}
          className="px-4 py-2 bg-primary-600 hover:bg-primary-700 disabled:opacity-50 text-white text-sm font-medium rounded-lg transition"
        >
          Confirm booking
        </button>
        <button
          disabled={deciding}
          onClick={() => onDecide(gate.id, "rejected")}
          className="px-4 py-2 text-sm font-medium text-gray-600 hover:bg-gray-100 disabled:opacity-50 rounded-lg transition"
        >
          Change my mind
        </button>
      </div>
    </div>
  );
}
