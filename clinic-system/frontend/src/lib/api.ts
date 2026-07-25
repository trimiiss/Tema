import { supabase } from "./supabase";

const BASE = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

async function getToken(): Promise<string> {
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? "";
}

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const token = await getToken();
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(options.headers ?? {}),
    },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Request failed");
  }
  return res.json();
}

// ---- Users ----
export const usersApi = {
  me: () => request<{ id: string; email: string; full_name: string | null; roles: string[] }>("/users/me"),
};

// ---- Patients ----
export const patientsApi = {
  list: (search = "") => request<any[]>(`/patients/?search=${encodeURIComponent(search)}`),
  get: (id: string) => request<any>(`/patients/${id}`),
  create: (body: any) => request<any>("/patients/", { method: "POST", body: JSON.stringify(body) }),
  update: (id: string, body: any) => request<any>(`/patients/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  missingFields: (id: string) => request<any>(`/patients/${id}/missing-fields`),
};

// ---- Appointments ----
export const appointmentsApi = {
  list: (params: Record<string, string> = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request<any[]>(`/appointments/?${qs}`);
  },
  get: (id: string) => request<any>(`/appointments/${id}`),
  slots: (staffId: string, date: string) =>
    request<any>(`/appointments/slots?staff_id=${staffId}&date=${date}`),
  create: (body: any) => request<any>("/appointments/", { method: "POST", body: JSON.stringify(body) }),
  update: (id: string, body: any) =>
    request<any>(`/appointments/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  cancel: (id: string) => request<void>(`/appointments/${id}`, { method: "DELETE" }),
};

// ---- Documents ----
export const documentsApi = {
  list: (patientId = "") =>
    request<any[]>(`/documents/?patient_id=${patientId}`),
  get: (id: string) => request<any>(`/documents/${id}`),
  fields: (id: string) => request<any[]>(`/documents/${id}/fields`),
  verify: (id: string) => request<any>(`/documents/${id}/verify`, { method: "POST" }),
  reject: (id: string) => request<any>(`/documents/${id}/reject`, { method: "POST" }),
  upload: async (file: File, patientId?: string) => {
    const token = await getToken();
    const form = new FormData();
    form.append("file", file);
    if (patientId) form.append("patient_id", patientId);
    const res = await fetch(`${BASE}/documents/upload`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: form,
    });
    if (!res.ok) throw new Error("Upload failed");
    return res.json();
  },
};

// ---- Reports ----
export const reportsApi = {
  summary: (dateFrom: string, dateTo: string) =>
    request<{
      total_appointments: number;
      by_status: Record<string, number>;
      no_show_count: number;
      missing_documents_count: number;
    }>(`/reports/summary?date_from=${dateFrom}&date_to=${dateTo}`),
  generate: async (dateFrom: string, dateTo: string, format: "pdf" | "csv") => {
    const token = await getToken();
    const res = await fetch(`${BASE}/reports/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ date_from: dateFrom, date_to: dateTo, format }),
    });
    if (!res.ok) throw new Error("Report generation failed");
    return res.blob();
  },
  auditLog: (entityType = "") =>
    request<any[]>(`/reports/audit-log?entity_type=${entityType}`),
};

// ---- Agent ----
export const agentApi = {
  run: (inputText: string) =>
    request<any>("/agent/run", { method: "POST", body: JSON.stringify({ input_text: inputText }) }),
  getRun: (id: string) => request<any>(`/agent/runs/${id}`),
  listRuns: () => request<any[]>("/agent/runs"),
  decide: (gateId: string, decision: "approved" | "rejected") =>
    request<any>(`/agent/approve/${gateId}`, { method: "POST", body: JSON.stringify({ decision }) }),
  // Server-Sent Events over fetch (native EventSource can't send an
  // Authorization header, and auth here is a Supabase bearer token).
  streamRun: async (runId: string, onEvent: (event: any) => void, signal?: AbortSignal) => {
    const token = await getToken();
    const res = await fetch(`${BASE}/agent/runs/${runId}/stream`, {
      headers: { Authorization: `Bearer ${token}` },
      signal,
    });
    if (!res.ok || !res.body) throw new Error("Failed to open agent run stream");

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let sep;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const rawEvent = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        for (const line of rawEvent.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          try {
            onEvent(JSON.parse(line.slice(6)));
          } catch {}
        }
      }
    }
  },
};
