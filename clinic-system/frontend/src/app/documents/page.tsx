"use client";
import { useEffect, useRef, useState } from "react";
import AppLayout from "@/components/layout/AppLayout";
import { documentsApi } from "@/lib/api";
import toast from "react-hot-toast";

const STATUS_COLOR: Record<string, string> = {
  pending:    "bg-yellow-100 text-yellow-700",
  processing: "bg-blue-100 text-blue-700",
  verified:   "bg-green-100 text-green-700",
  rejected:   "bg-red-100 text-red-700",
};

export default function DocumentsPage() {
  const [docs, setDocs] = useState<any[]>([]);
  const [selected, setSelected] = useState<any>(null);
  const [fields, setFields] = useState<any[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  async function load() {
    setDocs(await documentsApi.list().catch(() => []));
  }
  useEffect(() => { load(); }, []);

  async function selectDoc(doc: any) {
    setSelected(doc);
    setFields(await documentsApi.fields(doc.id).catch(() => []));
  }

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      await documentsApi.upload(file);
      toast.success("Uploaded — processing in background");
      load();
    } catch (err: any) {
      toast.error(err.message);
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function verify(id: string) {
    await documentsApi.verify(id);
    toast.success("Verified");
    load();
    if (selected?.id === id) setSelected({ ...selected, status: "verified" });
  }
  async function reject(id: string) {
    await documentsApi.reject(id);
    toast.success("Rejected");
    load();
    if (selected?.id === id) setSelected({ ...selected, status: "rejected" });
  }

  return (
    <AppLayout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold text-gray-900">Documents</h1>
          <label className={`cursor-pointer px-4 py-2 bg-primary-600 text-white text-sm font-medium rounded-lg hover:bg-primary-700 transition ${uploading ? "opacity-50 pointer-events-none" : ""}`}>
            {uploading ? "Uploading…" : "+ Upload Document"}
            <input ref={fileRef} type="file" accept=".pdf,.png,.jpg,.jpeg,.tiff" className="hidden" onChange={handleUpload} />
          </label>
        </div>

        <div className="flex gap-6">
          <div className="flex-1 bg-white rounded-xl border border-gray-200 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  {["Filename","Type","Status","Uploaded"].map(h =>
                    <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wide">{h}</th>
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {docs.map(d => (
                  <tr key={d.id} onClick={() => selectDoc(d)}
                    className={`cursor-pointer hover:bg-gray-50 ${selected?.id === d.id ? "bg-primary-50" : ""}`}>
                    <td className="px-4 py-3 font-medium truncate max-w-xs">{d.filename}</td>
                    <td className="px-4 py-3 text-gray-500">{d.doc_type ?? "—"}</td>
                    <td className="px-4 py-3">
                      <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${STATUS_COLOR[d.status] ?? ""}`}>{d.status}</span>
                    </td>
                    <td className="px-4 py-3 text-gray-400 text-xs">
                      {d.created_at ? new Date(d.created_at).toLocaleDateString() : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {selected && (
            <div className="w-80 bg-white rounded-xl border border-gray-200 p-5 space-y-4 flex-shrink-0">
              <div>
                <h3 className="font-semibold text-gray-900 truncate">{selected.filename}</h3>
                <span className={`mt-1 inline-block px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_COLOR[selected.status] ?? ""}`}>{selected.status}</span>
              </div>

              {/* Shown above the fields and before the Verify button on
                  purpose: the summary is model-written prose, so it is the part
                  of this panel a human most needs to read against the document
                  itself rather than accept. */}
              {selected.summary && (
                <div>
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Summary</p>
                  <p className="text-sm text-gray-700 leading-relaxed bg-gray-50 border border-gray-100 rounded-lg p-3">
                    {selected.summary}
                  </p>
                  <p className="text-xs text-gray-400 mt-1.5">
                    Written from the document text — check it before verifying.
                  </p>
                </div>
              )}

              {fields.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Extracted Fields</p>
                  <div className="space-y-1.5">
                    {fields.map(f => (
                      <div key={f.id} className="flex justify-between text-sm">
                        <span className="text-gray-500">{f.field_name}</span>
                        <span className="text-gray-900 font-medium">{f.field_value ?? "—"}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {selected.status === "pending" && (
                <div className="flex gap-2 pt-2">
                  <button onClick={() => verify(selected.id)}
                    className="flex-1 py-2 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700">
                    Verify
                  </button>
                  <button onClick={() => reject(selected.id)}
                    className="flex-1 py-2 bg-red-100 text-red-700 text-sm font-medium rounded-lg hover:bg-red-200">
                    Reject
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </AppLayout>
  );
}
