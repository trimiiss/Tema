"use client";
import { useState } from "react";
import type { Contact } from "@/lib/publicApi";

// A real form for contact details rather than free text, because a phone
// number or email is exactly the kind of thing a model re-typing what it
// heard is most likely to get wrong. Filling this in and sending it composes
// one plain sentence into the chat — the conversation stays the single
// channel `propose_booking` reads from, so the agent can still reason about
// what it was told.
//
// The typed values are also handed straight up via `onContact`, and the chat
// sends them with the confirmation so the *patient record* is written from
// this form rather than from the model's transcription of the sentence. What
// the visitor typed here is what a receptionist ends up reading.
// `open` is the parent's state rather than this component's, so picking a time
// can open the form: contact details are the one thing in this conversation a
// visitor genuinely has to type, and a collapsed link at the bottom of the page
// is easy to miss when the agent asks for them.
export default function ContactForm({
  open, setOpen, onSend, onContact, disabled,
}: {
  open: boolean;
  setOpen: (open: boolean) => void;
  onSend: (text: string) => void;
  onContact: (contact: Contact) => void;
  disabled: boolean;
}) {
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");

  const field = "w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 outline-none";
  const label = "block text-xs font-medium text-gray-500 mb-1";

  const canSend = lastName.trim() && (phone.trim() || email.trim());

  function send() {
    onContact({
      first_name: firstName.trim(),
      last_name: lastName.trim(),
      phone: phone.trim(),
      email: email.trim(),
    });
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
