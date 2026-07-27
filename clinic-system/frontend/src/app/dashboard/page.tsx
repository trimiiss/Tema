"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import AppLayout from "@/components/layout/AppLayout";
import { appointmentsApi, patientsApi, agentApi, usersApi } from "@/lib/api";
import { sessionStart } from "@/lib/session";
import { format, formatDistanceToNow, isAfter } from "date-fns";

const STATUS_STYLES: Record<string, string> = {
  proposed: "bg-amber-100 text-amber-700",
  confirmed: "bg-green-100 text-green-700",
  completed: "bg-blue-100 text-blue-700",
  cancelled: "bg-red-100 text-red-700",
};

const RUN_STATUS_STYLES: Record<string, { dot: string; label: string }> = {
  completed: { dot: "bg-green-500", label: "text-green-700" },
  awaiting_approval: { dot: "bg-amber-500", label: "text-amber-700" },
  failed: { dot: "bg-red-500", label: "text-red-700" },
  running: { dot: "bg-primary-500 animate-pulse", label: "text-primary-700" },
};

/** A stat that links somewhere — every number on this page is a question
 *  ("which ones?") the destination page already answers. `accent` colours the
 *  icon chip only; the card itself stays neutral so four of them in a row
 *  don't compete with the alerts below. */
function StatCard({ label, value, icon, accent, href, hint, loading }: {
  label: string; value: number; icon: string; accent: string;
  href: string; hint?: string; loading: boolean;
}) {
  return (
    <Link
      href={href}
      className="group bg-white rounded-xl border border-gray-200 p-5 flex items-start gap-4 hover:border-primary-300 hover:shadow-sm transition"
    >
      <div className={`w-11 h-11 rounded-xl flex items-center justify-center text-xl shrink-0 ${accent}`}>
        {icon}
      </div>
      <div className="min-w-0">
        <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">{label}</p>
        {loading ? (
          <div className="h-8 w-12 bg-gray-100 rounded animate-pulse mt-1" />
        ) : (
          <p className="text-3xl font-bold text-gray-900 leading-tight">{value}</p>
        )}
        {hint && <p className="text-xs text-gray-400 mt-0.5 truncate">{hint}</p>}
      </div>
    </Link>
  );
}

function Card({ title, action, children }: {
  title: string; action?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <div className="bg-white rounded-xl border border-gray-200">
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
        <h2 className="font-semibold text-gray-900">{title}</h2>
        {action}
      </div>
      <div className="p-6">{children}</div>
    </div>
  );
}

function Empty({ icon, text, cta }: { icon: string; text: string; cta?: React.ReactNode }) {
  return (
    <div className="py-8 text-center">
      <p className="text-3xl mb-2 opacity-40">{icon}</p>
      <p className="text-sm text-gray-400">{text}</p>
      {cta && <div className="mt-3">{cta}</div>}
    </div>
  );
}

function Rows({ n }: { n: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: n }).map((_, i) => (
        <div key={i} className="h-10 bg-gray-50 rounded-lg animate-pulse" />
      ))}
    </div>
  );
}

export default function DashboardPage() {
  const [todayAppts, setTodayAppts] = useState<any[]>([]);
  const [runs, setRuns] = useState<any[]>([]);
  const [requests, setRequests] = useState<any[]>([]);
  const [patientCount, setPatientCount] = useState(0);
  const [roles, setRoles] = useState<string[]>([]);
  const [name, setName] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const canManage = roles.some(r => r === "admin" || r === "receptionist");
  // Mirrors the guard on /appointments and the backend: only a receptionist
  // decides a visitor's booking request, so only they get pushed toward it.
  const isReceptionist = roles.includes("receptionist");

  useEffect(() => {
    const today = format(new Date(), "yyyy-MM-dd");
    // Same login-scoping as the chat page: activity from a previous sitting is
    // history, not "this session".
    sessionStart().then(since => Promise.allSettled([
      appointmentsApi.list({ date_from: today, date_to: today, sort: "earliest" }),
      agentApi.listRuns(since),
      // Still keyed on 'proposed' — unlike the Online Bookings tab on
      // /appointments, this is an action queue, and online bookings now
      // confirm themselves. It surfaces only rows submitted before that
      // change, so it empties itself out and stays at "all caught up".
      appointmentsApi.list({ source: "patient_portal", status: "proposed", sort: "earliest" }),
      patientsApi.list(),
      usersApi.me(),
    ]).then(([appts, agentRuns, reqs, patients, me]) => {
      if (appts.status === "fulfilled") setTodayAppts(appts.value);
      if (agentRuns.status === "fulfilled") setRuns(agentRuns.value);
      if (reqs.status === "fulfilled") setRequests(reqs.value);
      if (patients.status === "fulfilled") setPatientCount(patients.value.length);
      if (me.status === "fulfilled") {
        setRoles(me.value.roles);
        setName(me.value.full_name || me.value.email);
      }
      setLoading(false);
    }));
  }, []);

  const confirmed = todayAppts.filter(a => a.status === "confirmed").length;
  const upcoming = todayAppts.filter(
    a => a.status !== "cancelled" && isAfter(new Date(a.scheduled_at), new Date())
  );
  const nextUp = upcoming[0];
  const today = format(new Date(), "yyyy-MM-dd");
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  const firstName = name?.split(/[\s@]/)[0];

  return (
    <AppLayout>
      <div className="space-y-6 max-w-6xl">
        {/* Header */}
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">
              {greeting}{firstName ? `, ${firstName}` : ""}
            </h1>
            <p className="text-gray-500 mt-1 text-sm">
              {format(new Date(), "EEEE, MMMM d, yyyy")}
              {nextUp && (
                <> · Next up <span className="font-medium text-gray-700">
                  {format(new Date(nextUp.scheduled_at), "HH:mm")}
                </span> with {nextUp.staff_name || "—"}</>
              )}
            </p>
          </div>
          {canManage && (
            <Link
              href="/appointments"
              className="px-4 py-2.5 bg-primary-600 text-white rounded-lg text-sm font-medium hover:bg-primary-700 transition"
            >
              + New Appointment
            </Link>
          )}
        </div>

        {/* The one thing on this page that is someone's job right now, so it
            sits above the stats rather than being a number among four. */}
        {isReceptionist && requests.length > 0 && (
          <Link
            href="/appointments"
            className="block bg-amber-50 border border-amber-200 rounded-xl p-5 hover:border-amber-300 transition"
          >
            <div className="flex items-start gap-4">
              <div className="w-11 h-11 rounded-xl bg-amber-100 flex items-center justify-center text-xl shrink-0">
                📥
              </div>
              <div className="min-w-0 flex-1">
                <p className="font-semibold text-amber-900">
                  {requests.length} booking request{requests.length === 1 ? "" : "s"} waiting for you
                </p>
                <p className="text-sm text-amber-700 mt-0.5">
                  Submitted from the public booking page. Only a receptionist can accept or reject them.
                </p>
                <div className="mt-3 space-y-1.5">
                  {requests.slice(0, 3).map(r => (
                    <p key={r.id} className="text-xs text-amber-800">
                      <span className="font-medium">{r.patient_name || "Unknown"}</span>
                      {" · "}{format(new Date(r.scheduled_at), "EEE MMM d, HH:mm")}
                      {" · "}{r.staff_name || "—"}
                    </p>
                  ))}
                  {requests.length > 3 && (
                    <p className="text-xs text-amber-600">+ {requests.length - 3} more…</p>
                  )}
                </div>
              </div>
              <span className="text-amber-400 text-sm shrink-0">→</span>
            </div>
          </Link>
        )}

        {/* Stats */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard
            label="Today" value={todayAppts.length} icon="📅" accent="bg-primary-50"
            href={`/appointments?date_from=${today}&date_to=${today}`}
            hint={`${upcoming.length} still to come`} loading={loading}
          />
          <StatCard
            label="Confirmed" value={confirmed} icon="✅" accent="bg-green-50"
            href="/appointments"
            hint={todayAppts.length ? `of ${todayAppts.length} today` : "nothing booked"}
            loading={loading}
          />
          <StatCard
            label="Requests" value={requests.length} icon="📥" accent="bg-amber-50"
            href="/appointments"
            hint={requests.length ? "awaiting review" : "all caught up"} loading={loading}
          />
          <StatCard
            label="Patients" value={patientCount} icon="👤" accent="bg-purple-50"
            href="/patients" hint="on file" loading={loading}
          />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Today's schedule */}
          <Card
            title="Today's Schedule"
            action={
              <Link href="/appointments" className="text-xs font-medium text-primary-600 hover:underline">
                View all
              </Link>
            }
          >
            {loading ? (
              <Rows n={4} />
            ) : todayAppts.length === 0 ? (
              <Empty
                icon="🗓️"
                text="Nothing scheduled today"
                cta={canManage && (
                  <Link href="/appointments" className="text-xs font-medium text-primary-600 hover:underline">
                    Book an appointment
                  </Link>
                )}
              />
            ) : (
              <div className="space-y-1">
                {todayAppts.slice(0, 7).map(a => {
                  const past = !isAfter(new Date(a.scheduled_at), new Date());
                  return (
                    <div
                      key={a.id}
                      className={`flex items-center gap-3 py-2.5 border-b border-gray-50 last:border-0 ${past ? "opacity-50" : ""}`}
                    >
                      <span className="text-sm font-semibold text-gray-900 w-12 shrink-0 tabular-nums">
                        {format(new Date(a.scheduled_at), "HH:mm")}
                      </span>
                      <div className="min-w-0 flex-1">
                        {/* patient_name comes from the backend's `_flatten`; the
                            raw patient_id is a UUID and means nothing to a human. */}
                        <p className="text-sm font-medium text-gray-800 truncate">
                          {a.patient_name || "Unknown patient"}
                        </p>
                        <p className="text-xs text-gray-400 truncate">
                          {a.staff_name || "—"}{a.service_name ? ` · ${a.service_name}` : ""}
                        </p>
                      </div>
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium shrink-0 ${
                        STATUS_STYLES[a.status] || "bg-gray-100 text-gray-600"
                      }`}>
                        {a.status}
                      </span>
                    </div>
                  );
                })}
                {todayAppts.length > 7 && (
                  <p className="text-xs text-gray-400 pt-2">+ {todayAppts.length - 7} more today</p>
                )}
              </div>
            )}
          </Card>

          {/* Agent activity */}
          <Card
            title="Agent Activity This Session"
            action={
              canManage && (
                <Link href="/agent-chat" className="text-xs font-medium text-primary-600 hover:underline">
                  Open chat
                </Link>
              )
            }
          >
            {loading ? (
              <Rows n={4} />
            ) : runs.length === 0 ? (
              <Empty
                icon="🤖"
                text="No agent runs yet"
                cta={canManage && (
                  <Link href="/agent-chat" className="text-xs font-medium text-primary-600 hover:underline">
                    Try the Agent Chat
                  </Link>
                )}
              />
            ) : (
              <div className="space-y-1">
                {runs.slice(0, 7).map(r => {
                  const style = RUN_STATUS_STYLES[r.status] ?? { dot: "bg-gray-300", label: "text-gray-500" };
                  return (
                    <div key={r.id} className="flex items-start gap-3 py-2.5 border-b border-gray-50 last:border-0">
                      <span className={`w-2 h-2 rounded-full shrink-0 mt-1.5 ${style.dot}`} />
                      <div className="min-w-0 flex-1">
                        <p className="text-sm text-gray-700 truncate">{r.input_text}</p>
                        <p className="text-xs text-gray-400">
                          {r.created_at ? formatDistanceToNow(new Date(r.created_at), { addSuffix: true }) : ""}
                        </p>
                      </div>
                      <span className={`text-xs font-medium shrink-0 ${style.label}`}>
                        {r.status.replace(/_/g, " ")}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </Card>
        </div>
      </div>
    </AppLayout>
  );
}
