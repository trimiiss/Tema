"use client";
import { useEffect, useState } from "react";
import { appointmentsApi, patientsApi, servicesApi, staffApi } from "@/lib/api";
import { format } from "date-fns";
import toast from "react-hot-toast";

/**
 * Manual booking and editing — the direct counterpart to the agent-chat flow.
 * Admins and receptionists write here without an approval gate; the gate only
 * guards writes an agent proposes on a user's behalf.
 *
 * Slot strings carry the clinic's UTC offset, so they are compared as instants
 * (`Date.getTime()`), never as strings: the same moment has several spellings.
 */
export default function AppointmentModal({
  appointment,
  canCreatePatients,
  onClose,
  onSaved,
}: {
  appointment?: any;
  /** Adding a patient is receptionist-only, so admins don't see the option. */
  canCreatePatients: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isEdit = Boolean(appointment);

  const [patients, setPatients] = useState<any[]>([]);
  const [staff, setStaff] = useState<any[]>([]);
  const [services, setServices] = useState<any[]>([]);

  const [patientId, setPatientId] = useState(appointment?.patient_id ?? "");
  const [staffId, setStaffId] = useState(appointment?.staff_id ?? "");
  const [serviceId, setServiceId] = useState(appointment?.service_id ?? "");
  const [date, setDate] = useState(
    format(appointment ? new Date(appointment.scheduled_at) : new Date(), "yyyy-MM-dd")
  );
  const [slot, setSlot] = useState(appointment?.scheduled_at ?? "");
  const [notes, setNotes] = useState(appointment?.notes ?? "");

  const [slots, setSlots] = useState<string[]>([]);
  const [slotsLoading, setSlotsLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  // Inline patient creation, for booking someone not yet on file.
  const [showNewPatient, setShowNewPatient] = useState(false);
  const [newPatient, setNewPatient] = useState({ first_name: "", last_name: "", phone: "" });
  const [creatingPatient, setCreatingPatient] = useState(false);

  const duration =
    services.find(s => s.id === serviceId)?.duration_minutes ?? appointment?.duration_min ?? 30;

  useEffect(() => {
    Promise.all([patientsApi.list(), staffApi.list(), servicesApi.list()])
      .then(([p, st, sv]) => { setPatients(p); setStaff(st); setServices(sv); })
      .catch(e => toast.error(e.message));
  }, []);

  useEffect(() => {
    if (!staffId || !date) { setSlots([]); return; }
    let cancelled = false;
    setSlotsLoading(true);
    appointmentsApi
      .slots(staffId, date, duration, appointment?.id)
      .then(r => { if (!cancelled) setSlots(r.slots ?? []); })
      .catch(() => { if (!cancelled) setSlots([]); })
      .finally(() => { if (!cancelled) setSlotsLoading(false); });
    return () => { cancelled = true; };
  }, [staffId, date, duration, appointment?.id]);

  // Drop a stale selection when it is no longer on offer (doctor/date/duration
  // changed), but keep it while the list is still loading.
  useEffect(() => {
    if (slotsLoading || !slot) return;
    if (!slots.some(s => sameInstant(s, slot))) setSlot("");
  }, [slots, slotsLoading]);

  function sameInstant(a: string, b: string) {
    return new Date(a).getTime() === new Date(b).getTime();
  }

  function nextPatientCode() {
    const nums = patients
      .map(p => parseInt(String(p.code ?? "").replace(/\D/g, ""), 10))
      .filter(n => !isNaN(n));
    const next = (nums.length ? Math.max(...nums) : 0) + 1;
    return `P${String(next).padStart(3, "0")}`;
  }

  async function createPatient() {
    if (!newPatient.first_name.trim() || !newPatient.last_name.trim()) {
      return toast.error("First and last name are required");
    }
    setCreatingPatient(true);
    try {
      const created = await patientsApi.create({
        code: nextPatientCode(),
        first_name: newPatient.first_name.trim(),
        last_name: newPatient.last_name.trim(),
        ...(newPatient.phone.trim() ? { phone: newPatient.phone.trim() } : {}),
      });
      setPatients(await patientsApi.list());
      setPatientId(created.id);
      setNewPatient({ first_name: "", last_name: "", phone: "" });
      setShowNewPatient(false);
      toast.success(`Patient ${created.code} added`);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setCreatingPatient(false);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!patientId || !staffId || !slot) {
      return toast.error("Patient, doctor and time slot are required");
    }
    setSaving(true);
    try {
      const body = {
        patient_id: patientId,
        staff_id: staffId,
        service_id: serviceId || null,
        scheduled_at: slot,
        duration_min: duration,
        notes: notes || null,
      };
      if (isEdit) {
        await appointmentsApi.update(appointment.id, body);
        toast.success("Appointment updated");
      } else {
        await appointmentsApi.create(body);
        toast.success("Appointment booked");
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
      <div
        className="bg-white rounded-xl w-full max-w-lg max-h-[90vh] overflow-y-auto"
        onClick={e => e.stopPropagation()}
      >
        <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
          <h2 className="font-bold text-gray-900">
            {isEdit ? "Edit Appointment" : "New Appointment"}
          </h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
        </div>

        <form onSubmit={submit} className="p-5 space-y-4">
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className={label + " mb-0"}>Patient *</label>
              {canCreatePatients && (
                <button type="button" onClick={() => setShowNewPatient(v => !v)}
                  className="text-xs font-medium text-primary-600 hover:underline">
                  {showNewPatient ? "Cancel" : "+ New patient"}
                </button>
              )}
            </div>
            <select className={field} value={patientId} onChange={e => setPatientId(e.target.value)}>
              <option value="">Select a patient…</option>
              {patients.map(p => (
                <option key={p.id} value={p.id}>{p.code} — {p.first_name} {p.last_name}</option>
              ))}
            </select>

            {showNewPatient && (
              <div className="mt-3 p-3 bg-gray-50 border border-gray-200 rounded-lg space-y-3">
                <p className="text-xs text-gray-500">
                  Added as <span className="font-mono font-medium">{nextPatientCode()}</span> and
                  selected for this appointment.
                </p>
                <div className="grid grid-cols-2 gap-3">
                  <input className={field} placeholder="First name"
                    value={newPatient.first_name}
                    onChange={e => setNewPatient(p => ({ ...p, first_name: e.target.value }))} />
                  <input className={field} placeholder="Last name"
                    value={newPatient.last_name}
                    onChange={e => setNewPatient(p => ({ ...p, last_name: e.target.value }))} />
                </div>
                <input className={field} placeholder="Phone (optional)"
                  value={newPatient.phone}
                  onChange={e => setNewPatient(p => ({ ...p, phone: e.target.value }))} />
                <button type="button" onClick={createPatient} disabled={creatingPatient}
                  className="px-4 py-2 bg-gray-900 text-white rounded-lg text-sm font-medium hover:bg-gray-800 disabled:opacity-50 transition">
                  {creatingPatient ? "Adding…" : "Add patient"}
                </button>
              </div>
            )}
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
              onChange={e => setDate(e.target.value)} />
          </div>

          <div>
            <label className={label}>Available slots * <span className="text-gray-400">({duration} min)</span></label>
            {!staffId ? (
              <p className="text-sm text-gray-400 py-2">Select a doctor to see open times.</p>
            ) : slotsLoading ? (
              <p className="text-sm text-gray-400 py-2">Loading slots…</p>
            ) : slots.length === 0 ? (
              <p className="text-sm text-yellow-600 py-2">
                No open slots — the day is fully booked, this service is too long to fit, or
                the doctor doesn&apos;t work this weekday. Doctors default to Mon–Fri 09:00–17:00;
                change a doctor&apos;s hours on the Staff page.
              </p>
            ) : (
              <div className="grid grid-cols-5 gap-2 max-h-40 overflow-y-auto">
                {slots.map(s => (
                  <button key={s} type="button" onClick={() => setSlot(s)}
                    className={`px-2 py-1.5 rounded-lg text-xs font-medium border transition ${
                      slot && sameInstant(slot, s)
                        ? "bg-primary-600 border-primary-600 text-white"
                        : "border-gray-200 text-gray-600 hover:bg-gray-50"
                    }`}>
                    {format(new Date(s), "HH:mm")}
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
              {saving ? "Saving…" : isEdit ? "Save Changes" : "Book Appointment"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
