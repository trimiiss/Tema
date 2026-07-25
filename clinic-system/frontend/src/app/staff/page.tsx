"use client";
import { useEffect, useState } from "react";
import AppLayout from "@/components/layout/AppLayout";
import { staffApi, usersApi } from "@/lib/api";
import toast from "react-hot-toast";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const EMPTY_FORM = {
  role: "doctor",
  full_name: "",
  specialty: "",
  bio: "",
  email: "",
  password: "",
  work_days: [0, 1, 2, 3, 4] as number[],
  start_time: "08:00",
  end_time: "16:00",
};

export default function StaffPage() {
  const [staff, setStaff] = useState<any[]>([]);
  const [accounts, setAccounts] = useState<any[]>([]);
  const [schedules, setSchedules] = useState<Record<string, any[]>>({});
  const [loading, setLoading] = useState(true);
  const [roles, setRoles] = useState<string[] | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({ ...EMPTY_FORM });

  const isAdmin = roles?.includes("admin") ?? false;

  useEffect(() => {
    usersApi.me().then(me => setRoles(me.roles)).catch(() => setRoles([]));
  }, []);

  async function load() {
    setLoading(true);
    try {
      const data = await staffApi.list(true);
      setStaff(data);
      const pairs = await Promise.all(
        data.map(async s => [s.id, await staffApi.schedules(s.id).catch(() => [])] as const)
      );
      setSchedules(Object.fromEntries(pairs));
      // Non-fatal: listing accounts needs the Supabase admin API.
      setAccounts(await staffApi.accounts().catch(() => []));
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  function toggleDay(d: number) {
    setForm(f => ({
      ...f,
      work_days: f.work_days.includes(d)
        ? f.work_days.filter(x => x !== d)
        : [...f.work_days, d],
    }));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.full_name.trim()) return toast.error("Full name is required");
    if (!!form.email !== !!form.password)
      return toast.error("Email and password must be provided together");
    if (form.role === "receptionist" && !form.email)
      return toast.error("A receptionist needs a login account (email + password)");

    setSaving(true);
    try {
      await staffApi.create({
        role: form.role,
        full_name: form.full_name.trim(),
        specialty: form.specialty || null,
        bio: form.bio || null,
        email: form.email || null,
        password: form.password || null,
        ...(form.role === "doctor"
          ? { work_days: form.work_days, start_time: form.start_time, end_time: form.end_time }
          : {}),
      });
      toast.success(form.role === "doctor" ? "Doctor created" : "Receptionist account created");
      setForm({ ...EMPTY_FORM });
      setShowForm(false);
      load();
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function toggleActive(s: any) {
    try {
      if (s.active) await staffApi.deactivate(s.id);
      else await staffApi.update(s.id, { active: true });
      toast.success(s.active ? "Deactivated" : "Reactivated");
      load();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  if (roles === null) {
    return <AppLayout><div className="p-8 text-center text-gray-400">Loading…</div></AppLayout>;
  }

  if (!isAdmin) {
    return (
      <AppLayout>
        <div className="bg-white rounded-xl border border-gray-200 p-8 text-center">
          <p className="text-gray-900 font-semibold">Admins only</p>
          <p className="text-sm text-gray-400 mt-1">Staff management requires the admin role.</p>
        </div>
      </AppLayout>
    );
  }

  const field = "w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 outline-none";
  const label = "block text-xs font-medium text-gray-500 mb-1";

  return (
    <AppLayout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Staff</h1>
            <p className="text-sm text-gray-400">Create doctors and receptionist accounts</p>
          </div>
          <button
            onClick={() => setShowForm(v => !v)}
            className="px-4 py-2.5 bg-primary-600 text-white rounded-lg text-sm font-medium hover:bg-primary-700 transition"
          >
            {showForm ? "Cancel" : "+ Add Staff"}
          </button>
        </div>

        {showForm && (
          <form onSubmit={submit} className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
            <div className="flex gap-2">
              {["doctor", "receptionist"].map(r => (
                <button
                  key={r} type="button"
                  onClick={() => setForm(f => ({ ...f, role: r }))}
                  className={`px-4 py-2 rounded-lg text-sm font-medium border transition ${
                    form.role === r
                      ? "bg-primary-50 border-primary-300 text-primary-700"
                      : "border-gray-200 text-gray-500 hover:bg-gray-50"
                  }`}
                >
                  {r === "doctor" ? "🩺 Doctor" : "🗂️ Receptionist"}
                </button>
              ))}
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className={label}>Full name *</label>
                <input className={field} value={form.full_name}
                  onChange={e => setForm(f => ({ ...f, full_name: e.target.value }))}
                  placeholder={form.role === "doctor" ? "Dr. Arben Hoxha" : "Elira Bytyqi"} />
              </div>
              {form.role === "doctor" && (
                <div>
                  <label className={label}>Specialty</label>
                  <input className={field} value={form.specialty}
                    onChange={e => setForm(f => ({ ...f, specialty: e.target.value }))}
                    placeholder="General Practice" />
                </div>
              )}
            </div>

            <div className="border-t border-gray-100 pt-4">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
                Login account {form.role === "doctor" && <span className="font-normal normal-case text-gray-400">(optional)</span>}
              </p>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className={label}>Email</label>
                  <input type="email" className={field} value={form.email}
                    onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
                    placeholder="name@clinic.demo" />
                </div>
                <div>
                  <label className={label}>Password</label>
                  <input type="password" className={field} value={form.password}
                    onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
                    placeholder="min. 6 characters" />
                </div>
              </div>
            </div>

            {form.role === "doctor" && (
              <div className="border-t border-gray-100 pt-4">
                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
                  Working hours
                </p>
                <div className="flex gap-2 mb-3">
                  {WEEKDAYS.map((d, i) => (
                    <button key={d} type="button" onClick={() => toggleDay(i)}
                      className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition ${
                        form.work_days.includes(i)
                          ? "bg-primary-600 border-primary-600 text-white"
                          : "border-gray-200 text-gray-500 hover:bg-gray-50"
                      }`}>
                      {d}
                    </button>
                  ))}
                </div>
                <div className="flex gap-4">
                  <div>
                    <label className={label}>From</label>
                    <input type="time" className={field} value={form.start_time}
                      onChange={e => setForm(f => ({ ...f, start_time: e.target.value }))} />
                  </div>
                  <div>
                    <label className={label}>To</label>
                    <input type="time" className={field} value={form.end_time}
                      onChange={e => setForm(f => ({ ...f, end_time: e.target.value }))} />
                  </div>
                </div>
                <p className="text-xs text-gray-400 mt-2">
                  Working hours drive the available-slot picker when booking appointments.
                </p>
              </div>
            )}

            <div className="flex justify-end">
              <button type="submit" disabled={saving}
                className="px-5 py-2.5 bg-primary-600 text-white rounded-lg text-sm font-medium hover:bg-primary-700 disabled:opacity-50 transition">
                {saving ? "Creating…" : "Create"}
              </button>
            </div>
          </form>
        )}

        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          {loading ? (
            <div className="p-8 text-center text-gray-400">Loading…</div>
          ) : staff.length === 0 ? (
            <div className="p-8 text-center text-gray-400">No staff yet</div>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  {["Name", "Specialty", "Working hours", "Login", "Status", "Actions"].map(h => (
                    <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {staff.map(s => {
                  const sched = schedules[s.id] ?? [];
                  const days = sched.map(x => WEEKDAYS[x.weekday]).join(", ");
                  const hours = sched[0] ? `${sched[0].start_time.slice(0,5)}–${sched[0].end_time.slice(0,5)}` : "";
                  return (
                    <tr key={s.id} className={`hover:bg-gray-50 ${s.active ? "" : "opacity-50"}`}>
                      <td className="px-4 py-3 font-medium">{s.full_name}</td>
                      <td className="px-4 py-3 text-gray-500">{s.specialty ?? "—"}</td>
                      <td className="px-4 py-3 text-gray-500">
                        {sched.length ? <>{days} <span className="text-gray-400">{hours}</span></> : "— not scheduled"}
                      </td>
                      <td className="px-4 py-3 text-gray-500">{s.user_id ? "✓" : "—"}</td>
                      <td className="px-4 py-3">
                        <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${
                          s.active ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"
                        }`}>
                          {s.active ? "active" : "inactive"}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <button onClick={() => toggleActive(s)}
                          className="text-xs font-medium text-primary-600 hover:underline">
                          {s.active ? "Deactivate" : "Reactivate"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        <div>
          <h2 className="text-lg font-bold text-gray-900 mb-1">Login accounts</h2>
          <p className="text-sm text-gray-400 mb-3">
            Every account that can sign in, with its assigned roles. Receptionists appear
            here only — they are not bookable resources, so they have no staff record.
          </p>
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            {accounts.length === 0 ? (
              <div className="p-6 text-center text-gray-400 text-sm">
                No accounts listed. Creating one here requires the Supabase service-role key.
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr>
                    {["Name", "Email", "Roles"].map(h => (
                      <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {accounts.map(a => (
                    <tr key={a.id} className="hover:bg-gray-50">
                      <td className="px-4 py-3 font-medium">{a.full_name || "—"}</td>
                      <td className="px-4 py-3 text-gray-500">{a.email}</td>
                      <td className="px-4 py-3">
                        {a.roles.length ? (
                          <div className="flex gap-1.5 flex-wrap">
                            {a.roles.map((r: string) => (
                              <span key={r} className="px-2 py-0.5 rounded-full bg-primary-50 text-primary-700 text-xs font-medium">{r}</span>
                            ))}
                          </div>
                        ) : (
                          <span className="text-xs text-yellow-600">no role assigned</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>
    </AppLayout>
  );
}
