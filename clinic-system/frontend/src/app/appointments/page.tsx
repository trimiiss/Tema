"use client";
import { useEffect, useState } from "react";
import AppLayout from "@/components/layout/AppLayout";
import { appointmentsApi, usersApi } from "@/lib/api";
import { format, addDays, subDays } from "date-fns";
import toast from "react-hot-toast";
import AppointmentModal from "./AppointmentModal";

const STATUS_COLORS: Record<string, string> = {
  proposed:  "bg-yellow-100 text-yellow-700",
  confirmed: "bg-green-100 text-green-700",
  completed: "bg-blue-100 text-blue-700",
  cancelled: "bg-red-100 text-red-700",
};

const NEXT_STATUSES: Record<string, string[]> = {
  proposed:  ["confirmed", "cancelled"],
  confirmed: ["completed", "cancelled"],
};

export default function AppointmentsPage() {
  const [appointments, setAppointments] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [dateFrom, setDateFrom] = useState(format(subDays(new Date(), 30), "yyyy-MM-dd"));
  const [dateTo, setDateTo] = useState(format(addDays(new Date(), 30), "yyyy-MM-dd"));
  const [statusFilter, setStatusFilter] = useState("");
  const [sort, setSort] = useState("latest");
  const [roles, setRoles] = useState<string[]>([]);
  const [showNew, setShowNew] = useState(false);
  const [editing, setEditing] = useState<any>(null);
  const [view, setView] = useState<"all" | "requests">("all");
  const [requests, setRequests] = useState<any[]>([]);
  const [requestsLoading, setRequestsLoading] = useState(true);
  const canManage = roles.some(r => r === "admin" || r === "receptionist");
  // The patient register is receptionist-owned; admins book but don't add patients.
  const canAddPatients = roles.includes("receptionist");

  useEffect(() => {
    usersApi.me().then(me => setRoles(me.roles)).catch(() => setRoles([]));
  }, []);

  async function load() {
    setLoading(true);
    try {
      const data = await appointmentsApi.list({
        date_from: dateFrom, date_to: dateTo, sort,
        ...(statusFilter ? { status: statusFilter } : {}),
      });
      setAppointments(data);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, [dateFrom, dateTo, statusFilter, sort]);

  // Separate from the main table: `status=proposed` alone also matches an
  // appointment a receptionist just created and hasn't confirmed yet (that's
  // the DB's default status too), so the queue of *visitor* submissions is
  // its own fetch filtered on `source`, not a view of the same list above.
  async function loadRequests() {
    setRequestsLoading(true);
    try {
      const data = await appointmentsApi.list({ source: "patient_portal", status: "proposed", sort: "earliest" });
      setRequests(data);
    } finally {
      setRequestsLoading(false);
    }
  }

  // Loaded regardless of which tab is active, so the badge count is current
  // even if the visitor is looking at "All appointments".
  useEffect(() => { loadRequests(); }, []);

  async function handleStatusChange(id: string, newStatus: string) {
    try {
      await appointmentsApi.update(id, { status: newStatus });
      toast.success("Status updated");
      load();
      loadRequests();
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  return (
    <AppLayout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold text-gray-900">Appointments</h1>
          {canManage && (
            <button
              onClick={() => setShowNew(true)}
              className="px-4 py-2.5 bg-primary-600 text-white rounded-lg text-sm font-medium hover:bg-primary-700 transition"
            >
              + New Appointment
            </button>
          )}
        </div>

        {showNew && (
          <AppointmentModal
            canCreatePatients={canAddPatients}
            onClose={() => setShowNew(false)}
            onSaved={load}
          />
        )}
        {editing && (
          <AppointmentModal
            appointment={editing}
            canCreatePatients={canAddPatients}
            onClose={() => setEditing(null)}
            onSaved={load}
          />
        )}

        {/* Tabs — booking-chat submissions land as source='patient_portal' and
            need review separately from the general table above. */}
        <div className="flex gap-2 border-b border-gray-200">
          {([
            { key: "all", label: "All Appointments" },
            { key: "requests", label: `Booking Requests${requests.length ? ` (${requests.length})` : ""}` },
          ] as const).map(t => (
            <button
              key={t.key}
              onClick={() => setView(t.key)}
              className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition ${
                view === t.key
                  ? "border-primary-600 text-primary-700"
                  : "border-transparent text-gray-500 hover:text-gray-700"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {view === "requests" ? (
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            {requestsLoading ? (
              <div className="p-8 text-center text-gray-400">Loading...</div>
            ) : requests.length === 0 ? (
              <div className="p-8 text-center text-gray-400">No pending booking requests</div>
            ) : (
              <div className="divide-y divide-gray-100">
                {requests.map(a => (
                  <div key={a.id} className="p-4 flex flex-wrap items-start gap-4">
                    <div className="flex-1 min-w-[16rem]">
                      <p className="font-medium text-gray-900">{a.patient_name || "Unknown patient"}</p>
                      <p className="text-xs text-gray-500 mt-0.5">
                        {a.patient_phone && <span>📞 {a.patient_phone} </span>}
                        {a.patient_email && <span>✉️ {a.patient_email}</span>}
                        {!a.patient_phone && !a.patient_email && "No contact details on file"}
                      </p>
                      <p className="text-sm text-gray-600 mt-2">
                        {format(new Date(a.scheduled_at), "EEEE, MMM d 'at' HH:mm")} with{" "}
                        <span className="font-medium">{a.staff_name || "—"}</span>
                      </p>
                      {a.notes && <p className="text-xs text-gray-400 mt-1 whitespace-pre-wrap">{a.notes}</p>}
                    </div>
                    {canManage && (
                      <div className="flex gap-2 shrink-0">
                        <button
                          onClick={() => handleStatusChange(a.id, "confirmed")}
                          className="px-3 py-1.5 bg-green-600 hover:bg-green-700 text-white text-xs font-medium rounded-lg transition"
                        >
                          Confirm
                        </button>
                        <button
                          onClick={() => handleStatusChange(a.id, "cancelled")}
                          className="px-3 py-1.5 bg-red-50 hover:bg-red-100 text-red-700 text-xs font-medium rounded-lg transition"
                        >
                          Reject
                        </button>
                        <button
                          onClick={() => setEditing(a)}
                          className="px-3 py-1.5 text-gray-500 hover:bg-gray-50 text-xs font-medium rounded-lg transition"
                        >
                          Edit
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : (
        <>
        {/* Filters */}
        <div className="bg-white rounded-xl border border-gray-200 p-4 flex flex-wrap gap-4 items-end">
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">From</label>
            <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 outline-none" />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">To</label>
            <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 outline-none" />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">Status</label>
            <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 outline-none">
              <option value="">All</option>
              {["proposed","confirmed","completed","cancelled"].map(s =>
                <option key={s} value={s}>{s}</option>
              )}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">Sort</label>
            <select value={sort} onChange={e => setSort(e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 outline-none">
              <option value="latest">Latest first</option>
              <option value="earliest">Earliest first</option>
            </select>
          </div>
        </div>

        {/* Table */}
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          {loading ? (
            <div className="p-8 text-center text-gray-400">Loading...</div>
          ) : appointments.length === 0 ? (
            <div className="p-8 text-center text-gray-400">No appointments found</div>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">
                    <button
                      onClick={() => setSort(s => (s === "latest" ? "earliest" : "latest"))}
                      className="flex items-center gap-1 hover:text-gray-900 transition uppercase tracking-wide"
                      title={sort === "latest" ? "Sorted latest first — click for earliest" : "Sorted earliest first — click for latest"}
                    >
                      Date &amp; Time <span className="text-gray-400">{sort === "latest" ? "↓" : "↑"}</span>
                    </button>
                  </th>
                  {["Patient","Staff","Service","Status", ...(canManage ? ["Actions"] : [])].map(h =>
                    <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">{h}</th>
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {appointments.map(a => (
                  <tr key={a.id} className="hover:bg-gray-50">
                    <td className="px-4 py-3 font-medium">{format(new Date(a.scheduled_at), "MMM d, HH:mm")}</td>
                    <td className="px-4 py-3 text-gray-600">{a.patient_name || a.patient_id.slice(0,8) + "…"}</td>
                    <td className="px-4 py-3 text-gray-600">{a.staff_name || a.staff_id.slice(0,8) + "…"}</td>
                    <td className="px-4 py-3 text-gray-600">{a.service_name || (a.service_id ? a.service_id.slice(0,8) + "…" : "—")}</td>
                    <td className="px-4 py-3">
                      <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${STATUS_COLORS[a.status] || "bg-gray-100 text-gray-600"}`}>
                        {a.status}
                      </span>
                    </td>
                    {canManage && (
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-3">
                          {/* Finished appointments are history — don't let them be rewritten. */}
                          {a.status !== "completed" && a.status !== "cancelled" && (
                            <button
                              onClick={() => setEditing(a)}
                              className="text-xs font-medium text-primary-600 hover:underline"
                            >
                              Edit
                            </button>
                          )}
                          {NEXT_STATUSES[a.status]?.length ? (
                            <select
                              value=""
                              onChange={e => e.target.value && handleStatusChange(a.id, e.target.value)}
                              className="px-2 py-1 text-xs border border-gray-300 rounded focus:ring-2 focus:ring-primary-500 outline-none"
                            >
                              <option value="">Change status…</option>
                              {NEXT_STATUSES[a.status].map(s => (
                                <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>
                              ))}
                            </select>
                          ) : (
                            <span className="text-xs text-gray-400">—</span>
                          )}
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        </>
        )}
      </div>
    </AppLayout>
  );
}
