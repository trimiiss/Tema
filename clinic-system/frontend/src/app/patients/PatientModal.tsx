"use client";
import { useState } from "react";
import { patientsApi } from "@/lib/api";
import toast from "react-hot-toast";

/**
 * Create or edit a patient. Receptionist-only — the front desk owns the
 * patient register, so the page never renders this for other roles.
 */
export default function PatientModal({
  patient,
  suggestedCode,
  onClose,
  onSaved,
}: {
  patient?: any;
  suggestedCode: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isEdit = Boolean(patient);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    code: patient?.code ?? suggestedCode,
    first_name: patient?.first_name ?? "",
    last_name: patient?.last_name ?? "",
    dob: patient?.dob ?? "",
    gender: patient?.gender ?? "",
    phone: patient?.phone ?? "",
    email: patient?.email ?? "",
    address: patient?.address ?? "",
    notes: patient?.notes ?? "",
  });

  function set(key: string, value: string) {
    setForm(f => ({ ...f, [key]: value }));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.first_name.trim() || !form.last_name.trim()) {
      return toast.error("First and last name are required");
    }
    setSaving(true);
    try {
      // Empty strings would fail the date/enum column types — send nulls.
      const body: Record<string, any> = {
        first_name: form.first_name.trim(),
        last_name: form.last_name.trim(),
        dob: form.dob || null,
        gender: form.gender || null,
        phone: form.phone.trim() || null,
        email: form.email.trim() || null,
        address: form.address.trim() || null,
        notes: form.notes.trim() || null,
      };
      if (isEdit) {
        await patientsApi.update(patient.id, body);
        toast.success("Patient updated");
      } else {
        await patientsApi.create({ ...body, code: form.code.trim() });
        toast.success(`Patient ${form.code} added`);
      }
      onSaved();
      onClose();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setSaving(false);
    }
  }

  const field = "w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 outline-none";
  const label = "block text-xs font-medium text-gray-500 mb-1";

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50" onClick={onClose}>
      <div className="bg-white rounded-xl w-full max-w-lg max-h-[90vh] overflow-y-auto"
        onClick={e => e.stopPropagation()}>
        <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
          <h2 className="font-bold text-gray-900">{isEdit ? "Edit Patient" : "New Patient"}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
        </div>

        <form onSubmit={submit} className="p-5 space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className={label}>First name *</label>
              <input className={field} value={form.first_name}
                onChange={e => set("first_name", e.target.value)} placeholder="Alban" />
            </div>
            <div>
              <label className={label}>Last name *</label>
              <input className={field} value={form.last_name}
                onChange={e => set("last_name", e.target.value)} placeholder="Krasniqi" />
            </div>
          </div>

          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className={label}>ID {isEdit && <span className="text-gray-400">(fixed)</span>}</label>
              <input className={field + (isEdit ? " bg-gray-50 text-gray-400" : "")}
                value={form.code} disabled={isEdit}
                onChange={e => set("code", e.target.value)} />
            </div>
            <div>
              <label className={label}>Date of birth</label>
              <input type="date" className={field} value={form.dob}
                onChange={e => set("dob", e.target.value)} />
            </div>
            <div>
              <label className={label}>Gender</label>
              <select className={field} value={form.gender} onChange={e => set("gender", e.target.value)}>
                <option value="">—</option>
                <option value="M">M</option>
                <option value="F">F</option>
                <option value="Other">Other</option>
              </select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className={label}>Phone</label>
              <input className={field} value={form.phone}
                onChange={e => set("phone", e.target.value)} placeholder="+383-44-100001" />
            </div>
            <div>
              <label className={label}>Email</label>
              <input type="email" className={field} value={form.email}
                onChange={e => set("email", e.target.value)} placeholder="name@demo.test" />
            </div>
          </div>

          <div>
            <label className={label}>Address</label>
            <input className={field} value={form.address}
              onChange={e => set("address", e.target.value)} placeholder="Prishtina, Kosovo" />
          </div>

          <div>
            <label className={label}>Notes</label>
            <textarea className={field} rows={2} value={form.notes}
              onChange={e => set("notes", e.target.value)}
              placeholder="Administrative notes (no medical content)" />
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose}
              className="px-4 py-2.5 text-sm font-medium text-gray-600 hover:bg-gray-50 rounded-lg transition">
              Cancel
            </button>
            <button type="submit" disabled={saving}
              className="px-5 py-2.5 bg-primary-600 text-white rounded-lg text-sm font-medium hover:bg-primary-700 disabled:opacity-50 transition">
              {saving ? "Saving…" : isEdit ? "Save Changes" : "Add Patient"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
