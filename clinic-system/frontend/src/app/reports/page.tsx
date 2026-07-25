"use client";
import { useEffect, useState } from "react";
import AppLayout from "@/components/layout/AppLayout";
import { reportsApi } from "@/lib/api";
import { format, startOfWeek, endOfWeek } from "date-fns";
import toast from "react-hot-toast";

const STATUS_COLORS: Record<string, string> = {
  proposed: "bg-gray-400",
  confirmed: "bg-blue-500",
  completed: "bg-green-500",
  cancelled: "bg-red-400",
  no_show: "bg-amber-500",
};

function StatCard({ label, value, icon, color }: { label: string; value: number; icon: string; color: string }) {
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

export default function ReportsPage() {
  const today = new Date();
  const [dateFrom, setDateFrom] = useState(format(startOfWeek(today, { weekStartsOn: 1 }), "yyyy-MM-dd"));
  const [dateTo, setDateTo] = useState(format(endOfWeek(today, { weekStartsOn: 1 }), "yyyy-MM-dd"));
  const [fmt, setFmt] = useState<"pdf" | "csv">("pdf");
  const [loading, setLoading] = useState(false);
  const [summary, setSummary] = useState<{
    total_appointments: number;
    by_status: Record<string, number>;
    no_show_count: number;
    missing_documents_count: number;
  } | null>(null);

  useEffect(() => {
    reportsApi.summary(dateFrom, dateTo).then(setSummary).catch(() => setSummary(null));
  }, [dateFrom, dateTo]);

  async function generate() {
    setLoading(true);
    try {
      const blob = await reportsApi.generate(dateFrom, dateTo, fmt);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `report_${dateFrom}.${fmt}`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success(`${fmt.toUpperCase()} report downloaded`);
    } catch (e: any) {
      toast.error(e.message);
    } finally {
      setLoading(false);
    }
  }

  const statusEntries = Object.entries(summary?.by_status ?? {});
  const maxStatusCount = Math.max(1, ...statusEntries.map(([, c]) => c));

  return (
    <AppLayout>
      <div className="space-y-6 max-w-4xl">
        <h1 className="text-2xl font-bold text-gray-900">Reports</h1>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <StatCard label="Appointments in range" value={summary?.total_appointments ?? 0} icon="📅" color="bg-blue-50" />
          <StatCard label="No-shows / cancellations" value={summary?.no_show_count ?? 0} icon="⚠️" color="bg-amber-50" />
          <StatCard label="Patients missing documents" value={summary?.missing_documents_count ?? 0} icon="📄" color="bg-red-50" />
        </div>

        <div className="bg-white rounded-xl border border-gray-200 p-6">
          <h2 className="font-semibold text-gray-900 mb-4">Appointments by Status</h2>
          {statusEntries.length === 0 ? (
            <p className="text-gray-400 text-sm">No appointments in this range</p>
          ) : (
            <div className="space-y-2">
              {statusEntries.map(([status, count]) => (
                <div key={status} className="flex items-center gap-3">
                  <span className="text-xs font-medium text-gray-600 w-24 truncate">{status}</span>
                  <div className="flex-1 h-3 bg-gray-100 rounded-full overflow-hidden">
                    <div className={`h-full rounded-full ${STATUS_COLORS[status] || "bg-gray-400"}`}
                      style={{ width: `${(count / maxStatusCount) * 100}%` }} />
                  </div>
                  <span className="text-xs text-gray-500 w-6 text-right">{count}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="bg-white rounded-xl border border-gray-200 p-6 space-y-5 max-w-xl">
          <h2 className="font-semibold text-gray-900">Generate Operational Report</h2>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">From</label>
              <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 outline-none" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">To</label>
              <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 outline-none" />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Format</label>
            <div className="flex gap-3">
              {(["pdf", "csv"] as const).map(f => (
                <label key={f} className="flex items-center gap-2 cursor-pointer">
                  <input type="radio" name="fmt" value={f} checked={fmt === f} onChange={() => setFmt(f)}
                    className="text-primary-600" />
                  <span className="text-sm font-medium uppercase text-gray-700">{f}</span>
                </label>
              ))}
            </div>
          </div>

          <div className="bg-gray-50 rounded-lg p-4 text-sm text-gray-600 space-y-1">
            <p className="font-medium text-gray-700">Report includes:</p>
            <ul className="list-disc list-inside space-y-0.5 text-gray-500">
              <li>Appointment summary by status</li>
              <li>No-shows and cancellations</li>
              <li>Patients with missing documents</li>
            </ul>
          </div>

          <button onClick={generate} disabled={loading}
            className="w-full py-2.5 bg-primary-600 hover:bg-primary-700 text-white font-semibold rounded-lg transition disabled:opacity-50">
            {loading ? "Generating…" : `Generate & Download ${fmt.toUpperCase()}`}
          </button>
        </div>
      </div>
    </AppLayout>
  );
}
