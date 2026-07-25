"use client";
import { useEffect, useState } from "react";
import AppLayout from "@/components/layout/AppLayout";
import { patientsApi } from "@/lib/api";
import toast from "react-hot-toast";

export default function PatientsPage() {
  const [patients, setPatients] = useState<any[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<any>(null);
  const [missing, setMissing] = useState<string[]>([]);

  async function load() {
    setLoading(true);
    try {
      setPatients(await patientsApi.list(search));
    } finally { setLoading(false); }
  }

  useEffect(() => { load(); }, [search]);

  async function selectPatient(p: any) {
    setSelected(p);
    const data = await patientsApi.missingFields(p.id).catch(() => ({ missing_fields: [] }));
    setMissing(data.missing_fields);
  }

  return (
    <AppLayout>
      <div className="space-y-6">
        <h1 className="text-2xl font-bold text-gray-900">Patients</h1>

        <input
          value={search} onChange={e => setSearch(e.target.value)}
          placeholder="Search by name or code..."
          className="w-full max-w-sm px-4 py-2.5 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 outline-none"
        />

        <div className="flex gap-6">
          {/* List */}
          <div className="flex-1 bg-white rounded-xl border border-gray-200 overflow-hidden">
            {loading ? (
              <div className="p-8 text-center text-gray-400">Loading...</div>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr>
                    {["Code","Name","Gender","Phone","Email"].map(h =>
                      <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">{h}</th>
                    )}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {patients.map(p => (
                    <tr key={p.id} onClick={() => selectPatient(p)}
                      className={`cursor-pointer hover:bg-gray-50 ${selected?.id === p.id ? "bg-primary-50" : ""}`}>
                      <td className="px-4 py-3 font-mono text-primary-600">{p.code}</td>
                      <td className="px-4 py-3 font-medium">{p.first_name} {p.last_name}</td>
                      <td className="px-4 py-3 text-gray-500">{p.gender ?? "—"}</td>
                      <td className="px-4 py-3 text-gray-500">{p.phone ?? "—"}</td>
                      <td className="px-4 py-3 text-gray-500">{p.email ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Detail panel */}
          {selected && (
            <div className="w-72 bg-white rounded-xl border border-gray-200 p-5 space-y-4 flex-shrink-0">
              <div>
                <h3 className="font-semibold text-gray-900">{selected.first_name} {selected.last_name}</h3>
                <p className="text-xs text-gray-400 font-mono">{selected.code}</p>
              </div>
              <div className="space-y-2 text-sm">
                {[["DOB", selected.dob], ["Gender", selected.gender], ["Phone", selected.phone],
                  ["Email", selected.email], ["Address", selected.address]].map(([label, val]) => (
                  <div key={label} className="flex gap-2">
                    <span className="text-gray-400 w-16 flex-shrink-0">{label}</span>
                    <span className="text-gray-700">{val ?? <span className="text-red-400">Missing</span>}</span>
                  </div>
                ))}
              </div>
              {missing.length > 0 && (
                <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3">
                  <p className="text-xs font-semibold text-yellow-700 mb-1">Missing fields:</p>
                  <ul className="list-disc list-inside text-xs text-yellow-600 space-y-0.5">
                    {missing.map(f => <li key={f}>{f}</li>)}
                  </ul>
                </div>
              )}
              <p className="text-xs text-gray-400">{selected.notes || "No notes"}</p>
            </div>
          )}
        </div>
      </div>
    </AppLayout>
  );
}
