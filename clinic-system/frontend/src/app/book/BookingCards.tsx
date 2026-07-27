"use client";
import { format } from "date-fns";

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

export function SlotPicker({
  slots, doctorName, onPick,
}: { slots: string[]; doctorName?: string; onPick: (text: string) => void }) {
  if (!slots?.length) {
    return <p className="text-xs text-gray-400 mt-2">No open slots there — try another day or doctor.</p>;
  }
  return (
    <div className="flex flex-wrap gap-2 mt-2">
      {slots.map(s => (
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

export function ConfirmationCard({
  gate, onDecide, deciding,
}: { gate: any; onDecide: (gateId: string, d: "approved" | "rejected") => void; deciding: boolean }) {
  if (gate.status !== "pending") {
    return (
      <div className={`rounded-lg px-4 py-3 text-sm font-medium ${
        gate.status === "approved" ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"
      }`}>
        {gate.status === "approved"
          ? "Request submitted to the clinic."
          : "No problem — that request was cancelled."}
      </div>
    );
  }
  return (
    <div className="bg-amber-50 border border-amber-300 rounded-xl p-4 space-y-3 mt-2">
      <p className="text-sm font-semibold text-amber-900">Please confirm your request</p>
      <p className="text-sm text-amber-800">{gate.action_description}</p>
      <p className="text-xs text-amber-700">
        This submits a request — the clinic still needs to confirm it before it's a booked appointment.
      </p>
      <div className="flex gap-3">
        <button
          disabled={deciding}
          onClick={() => onDecide(gate.id, "approved")}
          className="px-4 py-2 bg-primary-600 hover:bg-primary-700 disabled:opacity-50 text-white text-sm font-medium rounded-lg transition"
        >
          Confirm request
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
