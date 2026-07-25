"use client";
import { useEffect, useState } from "react";
import { appointmentsApi, patientsApi, servicesApi, staffApi } from "@/lib/api";
import { format } from "date-fns";
import toast from "react-hot-toast";

/**
 * Manual booking — the direct counterpart to the agent-chat flow.
 * Admins and receptionists book here without an approval gate; the gate only
 * guards writes an agent proposes on a user's behalf.
 */
export default function NewAppointmentModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [patients, setPatients] = useState<any[]>([]);
  const [staff, setStaff] = useState<any[]>([]);
  const [services, setServices] = useState<any[]>([]);

  const [patientId, setPatientId] = useState("");
  const [staffId, setStaffId] = useState("");
  const [serviceId, setServiceId] = useState("");
  const [date, setDate] = useState(format(new Date(), "yyyy-MM-dd"));
  const [slot, setSlot] = useState("");
  const [notes, setNotes] = useState("");

  const [slots, setSlots] = useState<string[]>([]);
  const [slotsLoading, setSlotsLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    Promise.all([patientsApi.list(), staffApi.list(), servicesApi.list()])
      .then(([p, st, sv]) => { setPatients(p); setStaff(st); setServices(sv); })
      .catch(e => toast.error(e.message));
  }, []);

  useEffect(() => {
    if (!staffId || !date) { setSlots([]); setSlot(""); return; }
    let cancelled = false;
    setSlotsLoading(true);
    setSlot("");
    appointmentsApi.slots(staffId, date)
      .then(r => { if (!cancelled) setSlots(r.slots ?? []); })
      .catch(() => { if (!cancelled) setSlots([]); })
      .finally(() => { if (!cancelled) setSlotsLoading(false); });
    return () => { cancelled = true; };
  }, [staffId, date]);

  const duration = services.find(s => s.id === serviceId)?.duration_minutes ?? 30;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!patientId || !staffId || !slot) {
      return toast.error("Patient, doctor and time slot are required");
    }
    setSaving(true);
    try {
      await appointmentsApi.create({
        patient_id: patientId,
        staff_id: staffId,
        service_id: serviceId || null,
        scheduled_at: slot,
        duration_min: duration,
        notes: notes || null,
      });
      toast.success("Appointment booked");
      onCreated();
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
      <div
        className="bg-white rounded-xl w-full max-w-lg max-h-[90vh] overflow-y-auto"
        onClick={e => e.stopPropagation()}
      >
        <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
          <h2 className="font-bold text-gray-900">New Appointment</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
        </div>

        <form onSubmit={submit} className="p-5 space-y-4">
          <div>
            <label className={label}>Patient *</label>
            <select className={field} value={patientId} onChange={e => setPatientId(e.target.value)}>
              <option value="">Select a patient…</option>
              {patients.map(p => (
                <option key={p.id} value={p.id}>{p.code} — {p.first_name} {p.last_name}</option>
              ))}
            </select>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className={label}>Doctor *</label>
              <select className={field} value={staffId} onChange={e => setStaffId(e.target.value)}>
                <option value="">Select a doctor…</option>
                {staff.map(s => (
                  <option key={s.id} value={s.id}>
                    {s.full_name}{s.specialty ? ` — ${s.specialty}` : ""}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className={label}>Service</label>
              <select className={field} value={serviceId} onChange={e => setServiceId(e.target.value)}>
                <option value="">None (30 min)</option>
                {services.map(s => (
                  <option key={s.id} value={s.id}>{s.name} ({s.duration_minutes} min)</option>
                ))}
              </select>
            </div>
          </div>

          <div>
            <label className={label}>Date *</label>
            <input type="date" className={field} value={date}
              min={format(new Date(), "yyyy-MM-dd")}
              onChange={e => setDate(e.target.value)} />
          </div>

          <div>
            <label className={label}>Available slots *</label>
            {!staffId ? (
              <p className="text-sm text-gray-400 py-2">Select a doctor to see open times.</p>
            ) : slotsLoading ? (
              <p className="text-sm text-gray-400 py-2">Loading slots…</p>
            ) : slots.length === 0 ? (
              <p className="text-sm text-yellow-600 py-2">
                No open slots — the doctor has no working hours set for this day, or the day is fully booked.
              </p>
            ) : (
              <div className="grid grid-cols-5 gap-2 max-h-40 overflow-y-auto">
                {slots.map(s => (
                  <button key={s} type="button" onClick={() => setSlot(s)}
                    className={`px-2 py-1.5 rounded-lg text-xs font-medium border transition ${
                      slot === s
                        ? "bg-primary-600 border-primary-600 text-white"
                        : "border-gray-200 text-gray-600 hover:bg-gray-50"
                    }`}>
                    {s.slice(11, 16)}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div>
            <label className={label}>Notes</label>
            <textarea className={field} rows={2} value={notes}
              onChange={e => setNotes(e.target.value)}
              placeholder="Administrative notes (no medical content)" />
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose}
              className="px-4 py-2.5 text-sm font-medium text-gray-600 hover:bg-gray-50 rounded-lg transition">
              Cancel
            </button>
            <button type="submit" disabled={saving}
              className="px-5 py-2.5 bg-primary-600 text-white rounded-lg text-sm font-medium hover:bg-primary-700 disabled:opacity-50 transition">
              {saving ? "Booking…" : "Book Appointment"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
