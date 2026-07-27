"use client";
import { useEffect, useState } from "react";
import AppLayout from "@/components/layout/AppLayout";
import { appointmentsApi, patientsApi, agentApi } from "@/lib/api";
import { format } from "date-fns";

function StatCard({ label, value, icon, color }: any) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-6 flex items-center gap-4">
      <div className={`w-12 h-12 rounded-xl flex items-center justify-center text-2xl ${color}`}>{icon}</div>
      <div>
        <p className="text-sm text-gray-500">{label}</p>
        <p className="text-2xl font-bold text-gray-900">{value}</p>
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const [todayAppts, setTodayAppts] = useState<any[]>([]);
  const [runs, setRuns] = useState<any[]>([]);
  const [stats, setStats] = useState({ total: 0, confirmed: 0, cancelled: 0 });
  const [pendingRequests, setPendingRequests] = useState(0);

  useEffect(() => {
    const today = format(new Date(), "yyyy-MM-dd");
    appointmentsApi.list({ date_from: today, date_to: today }).then(data => {
      setTodayAppts(data);
      setStats({
        total: data.length,
        confirmed: data.filter(a => a.status === "confirmed").length,
        cancelled: data.filter(a => a.status === "cancelled").length,
      });
    }).catch(() => {});
    agentApi.listRuns().then(setRuns).catch(() => {});
    // Visitor-submitted bookings awaiting review — see the "Booking Requests"
    // tab on /appointments, which is where these are actioned.
    appointmentsApi.list({ source: "patient_portal", status: "proposed" })
      .then(data => setPendingRequests(data.length))
      .catch(() => {});
  }, []);

  return (
    <AppLayout>
      <div className="space-y-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
          <p className="text-gray-500 mt-1">{format(new Date(), "EEEE, MMMM d, yyyy")}</p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
          <StatCard label="Today's Appointments" value={stats.total} icon="📅" color="bg-blue-50" />
          <StatCard label="Confirmed" value={stats.confirmed} icon="✅" color="bg-green-50" />
          <StatCard label="Cancelled" value={stats.cancelled} icon="❌" color="bg-red-50" />
          <StatCard label="Booking Requests" value={pendingRequests} icon="📥" color="bg-amber-50" />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="bg-white rounded-xl border border-gray-200 p-6">
            <h2 className="font-semibold text-gray-900 mb-4">Today's Schedule</h2>
            {todayAppts.length === 0 ? (
              <p className="text-gray-400 text-sm">No appointments today</p>
            ) : (
              <div className="space-y-2">
                {todayAppts.slice(0, 8).map(a => (
                  <div key={a.id} className="flex items-center gap-3 py-2 border-b border-gray-50 last:border-0">
                    <span className="text-xs text-gray-400 w-12">{format(new Date(a.scheduled_at), "HH:mm")}</span>
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                      a.status === "confirmed" ? "bg-green-100 text-green-700" :
                      a.status === "cancelled" ? "bg-red-100 text-red-700" :
                      "bg-gray-100 text-gray-600"
                    }`}>{a.status}</span>
                    <span className="text-sm text-gray-700 truncate">{a.patient_id}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="bg-white rounded-xl border border-gray-200 p-6">
            <h2 className="font-semibold text-gray-900 mb-4">Recent Agent Runs</h2>
            {runs.length === 0 ? (
              <p className="text-gray-400 text-sm">No agent runs yet. Try the Agent Chat!</p>
            ) : (
              <div className="space-y-2">
                {runs.slice(0, 6).map(r => (
                  <div key={r.id} className="flex items-center gap-3 py-2 border-b border-gray-50 last:border-0">
                    <span className={`w-2 h-2 rounded-full flex-shrink-0 ${
                      r.status === "completed" ? "bg-green-400" :
                      r.status === "awaiting_approval" ? "bg-yellow-400" :
                      r.status === "failed" ? "bg-red-400" : "bg-blue-400"
                    }`} />
                    <span className="text-sm text-gray-700 truncate flex-1">{r.input_text}</span>
                    <span className="text-xs text-gray-400">{r.status}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </AppLayout>
  );
}
