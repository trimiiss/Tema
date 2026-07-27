"use client";
import { useState } from "react";

// A real form for contact details rather than free text, because a phone
// number or email is exactly the kind of thing a model re-typing what it
// heard is most likely to get wrong. Filling this in and sending it composes
// one plain sentence into the chat — the conversation stays the single
// channel `booking_agent.propose_booking` reads from, this just guarantees
// what lands in it is typed correctly.
export default function ContactForm({ onSend, disabled }: { onSend: (text: string) => void; disabled: boolean }) {
  const [open, setOpen] = useState(false);
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");

  const field = "w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 outline-none";
  const label = "block text-xs font-medium text-gray-500 mb-1";

  const canSend = lastName.trim() && (phone.trim() || email.trim());

  function send() {
    const parts = [`My name is ${firstName.trim()} ${lastName.trim()}`.trim() + "."];
    if (phone.trim()) parts.push(`My phone number is ${phone.trim()}.`);
    if (email.trim()) parts.push(`My email is ${email.trim()}.`);
    onSend(parts.join(" "));
    setOpen(false);
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        disabled={disabled}
        className="text-xs font-medium text-primary-600 hover:text-primary-700 disabled:opacity-40 px-1"
      >
        📇 Fill in my contact details
      </button>
    );
  }

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-3">
      <p className="text-sm font-semibold text-gray-700">Your contact details</p>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={label}>First name</label>
          <input className={field} value={firstName} onChange={e => setFirstName(e.target.value)} />
        </div>
        <div>
          <label className={label}>Last name *</label>
          <input className={field} value={lastName} onChange={e => setLastName(e.target.value)} />
        </div>
      </div>
      <div>
        <label className={label}>Phone</label>
        <input className={field} value={phone} onChange={e => setPhone(e.target.value)} placeholder="+383 44 ..." />
      </div>
      <div>
        <label className={label}>Email</label>
        <input className={field} type="email" value={email} onChange={e => setEmail(e.target.value)} />
      </div>
      <p className="text-xs text-gray-400">* Last name and at least a phone or email are required.</p>
      <div className="flex gap-2">
        <button
          onClick={send}
          disabled={!canSend || disabled}
          className="px-4 py-2 bg-primary-600 hover:bg-primary-700 disabled:opacity-40 text-white text-sm font-medium rounded-lg transition"
        >
          Send my details
        </button>
        <button
          onClick={() => setOpen(false)}
          className="px-4 py-2 text-sm font-medium text-gray-600 hover:bg-gray-50 rounded-lg transition"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
