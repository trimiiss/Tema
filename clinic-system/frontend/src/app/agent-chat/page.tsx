"use client";
import { useEffect, useRef, useState } from "react";
import AppLayout from "@/components/layout/AppLayout";
import { agentApi, usersApi } from "@/lib/api";
import { sessionStart } from "@/lib/session";
import toast from "react-hot-toast";

// `run` is set only on messages rebuilt from history — it carries the steps and
// gates `GET /agent/runs` already returned, so a finished run renders straight
// away instead of re-fetching what we are holding.
type Message = { role: "user" | "agent"; text: string; runId?: string; run?: any };

const EXAMPLES = [
  "Schedule Alban Krasniqi with Dr. Hoxha for a general checkup tomorrow at 10am",
  "Look up patient P002",
  "Generate a weekly report for last week",
  "Show appointments for patient P001",
];

const isFinished = (status?: string) => status === "completed" || status === "failed";

function runLabel(run: any): string {
  const short = run.id.slice(0, 8);
  if (run.status === "awaiting_approval") return `Waiting for approval (run ${short})`;
  if (run.status === "failed") return `Run ${short} failed`;
  if (run.status === "running") return `Processing… (run ${short})`;
  return `Run ${short}`;
}

/** Rebuild the transcript from the runs the backend already stores. */
function messagesFromRuns(runs: any[]): Message[] {
  // The endpoint returns newest first; a transcript reads oldest first.
  return [...runs].reverse().flatMap((run): Message[] => [
    { role: "user", text: run.input_text },
    { role: "agent", text: runLabel(run), runId: run.id, run },
  ]);
}

function TraceStep({ step }: { step: any }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden text-xs">
      <button onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 px-3 py-2 bg-gray-50 hover:bg-gray-100 text-left">
        <span className="text-gray-400">{open ? "▾" : "▸"}</span>
        <span className="font-mono text-blue-600">{step.agent_name}</span>
        <span className="text-gray-500">·</span>
        <span className="font-medium text-gray-700">{step.action}</span>
        <span className="ml-auto text-gray-400">{new Date(step.timestamp).toLocaleTimeString()}</span>
      </button>
      {open && (
        <div className="px-3 py-2 bg-white space-y-1 border-t border-gray-100">
          <p className="text-gray-400 font-semibold">Input:</p>
          <pre className="text-gray-600 whitespace-pre-wrap break-all">{JSON.stringify(step.input, null, 2)}</pre>
          <p className="text-gray-400 font-semibold mt-2">Output:</p>
          <pre className="text-gray-600 whitespace-pre-wrap break-all">{JSON.stringify(step.output, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}

function ApprovalGate({ gate, onDecide, canDecide }: { gate: any; onDecide: (id: string, d: "approved" | "rejected") => void; canDecide: boolean }) {
  if (gate.status !== "pending") {
    return (
      <div className={`rounded-lg px-4 py-3 text-sm font-medium ${
        gate.status === "approved" ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"
      }`}>
        Gate {gate.status}: {gate.action_description}
      </div>
    );
  }
  return (
    <div className="bg-yellow-50 border border-yellow-300 rounded-lg p-4 space-y-3">
      <p className="text-sm font-semibold text-yellow-900">Approval Required</p>
      <p className="text-sm text-yellow-800">{gate.action_description}</p>
      {canDecide ? (
        <div className="flex gap-3">
          <button onClick={() => onDecide(gate.id, "approved")}
            className="px-4 py-2 bg-green-600 hover:bg-green-700 text-white text-sm font-medium rounded-lg transition">
            Approve
          </button>
          <button onClick={() => onDecide(gate.id, "rejected")}
            className="px-4 py-2 bg-red-100 hover:bg-red-200 text-red-700 text-sm font-medium rounded-lg transition">
            Reject
          </button>
        </div>
      ) : (
        <p className="text-xs text-yellow-700">Waiting for an admin or receptionist to decide.</p>
      )}
    </div>
  );
}

function RunDetail({ runId, initialRun, onGateDecide, canDecide }: { runId: string; initialRun?: any; onGateDecide: (gateId: string, d: "approved"|"rejected") => void; canDecide: boolean }) {
  const [run, setRun] = useState<any>(initialRun ?? null);
  // A run restored from history that already finished has nothing left to
  // stream — its stream would send one snapshot and close. Skip the round-trip
  // so reopening the page doesn't fire a request per past run. One still
  // awaiting approval does need the stream: that gate can be decided from here.
  const settled = isFinished(initialRun?.status);

  useEffect(() => {
    if (!runId) return;
    if (settled) return;
    setRun(initialRun ?? null);
    const controller = new AbortController();
    agentApi.streamRun(runId, (event) => {
      setRun((prev: any) => {
        if (event.type === "snapshot") {
          return { ...event.run, steps: event.steps, gates: event.gates };
        }
        if (!prev) return prev;
        if (event.type === "step") {
          return { ...prev, steps: [...(prev.steps ?? []), event.step] };
        }
        if (event.type === "gate") {
          const gates = prev.gates ?? [];
          const idx = gates.findIndex((g: any) => g.id === event.gate.id);
          const nextGates = idx === -1
            ? [...gates, event.gate]
            : gates.map((g: any, i: number) => (i === idx ? { ...g, ...event.gate } : g));
          return { ...prev, gates: nextGates };
        }
        if (event.type === "status") {
          const { type, ...fields } = event;
          return { ...prev, ...fields };
        }
        return prev;
      });
    }, controller.signal).catch((e: any) => {
      if (e?.name !== "AbortError") console.error(e);
    });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, settled]);

  if (!run) return <div className="text-xs text-gray-400 mt-2">Loading trace…</div>;

  return (
    <div className="mt-3 space-y-2">
      {/* Approval gates first */}
      {run.gates?.filter((g: any) => g.status === "pending").map((g: any) => (
        <ApprovalGate key={g.id} gate={g} onDecide={onGateDecide} canDecide={canDecide} />
      ))}
      {/* Steps trace */}
      <details className="group" open>
        <summary className="cursor-pointer text-xs text-gray-400 hover:text-gray-600 flex items-center gap-1">
          <span className="group-open:rotate-90 transition-transform inline-block">▸</span>
          {run.steps?.length ?? 0} agent steps · status: <span className={`font-semibold ${
            run.status === "completed" ? "text-green-600" : run.status === "failed" ? "text-red-600" : "text-yellow-600"
          }`}>{run.status}</span>
        </summary>
        <div className="mt-2 space-y-1.5 pl-2 border-l-2 border-gray-100">
          {run.steps?.map((s: any) => <TraceStep key={s.id} step={s} />)}
        </div>
      </details>
      {/* Decided gates */}
      {run.gates?.filter((g: any) => g.status !== "pending").map((g: any) => (
        <ApprovalGate key={g.id} gate={g} onDecide={onGateDecide} canDecide={canDecide} />
      ))}
      {/* Result */}
      {run.result && (
        <div className="bg-gray-50 rounded-lg p-3 text-xs text-gray-700">
          <p className="font-semibold text-gray-500 mb-1">Result</p>
          <pre className="whitespace-pre-wrap break-all">{JSON.stringify(run.result, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}

export default function AgentChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [roles, setRoles] = useState<string[] | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);
  useEffect(() => {
    usersApi.me().then(me => setRoles(me.roles)).catch(() => setRoles([]));
  }, []);

  // Rebuild the transcript from `agent_runs` on every mount. Chat state lived
  // only in this component, so leaving the page for the dashboard and coming
  // back dropped it — but the backend has stored every run all along, keyed by
  // user. Reading it back is what makes the history survive navigation, a
  // reload and a different tab alike, rather than just an in-app route change.
  //
  // Scoped to the *current login*, though: replaying a run from three days ago
  // presents it as part of this conversation, so the user reads answers to
  // questions they don't remember asking. `sessionStart` is when this login
  // began, so signing out and back in starts a clean thread while navigation
  // and reloads within one sitting still restore everything.
  useEffect(() => {
    let cancelled = false;
    sessionStart().then(since =>
      agentApi.listRuns(since)
        // History arrives asynchronously, so it must never overwrite a message
        // the user managed to send while it was in flight.
        .then(runs => { if (!cancelled) setMessages(prev => (prev.length ? prev : messagesFromRuns(runs))); })
        .catch(() => { /* a fresh transcript is a fine fallback */ })
        .finally(() => { if (!cancelled) setLoadingHistory(false); })
    );
    return () => { cancelled = true; };
  }, []);

  const canUseAgent = roles !== null && roles.some(r => r === "admin" || r === "receptionist");

  async function send(text?: string) {
    const msg = text ?? input.trim();
    if (!msg || sending) return;
    setInput("");
    setSending(true);
    setMessages(m => [...m, { role: "user", text: msg }]);
    try {
      const run = await agentApi.run(msg);
      setMessages(m => [...m, { role: "agent", text: `Processing… (run ${run.id.slice(0, 8)})`, runId: run.id }]);
    } catch (e: any) {
      toast.error(e.message);
      setMessages(m => [...m, { role: "agent", text: `Error: ${e.message}` }]);
    } finally {
      setSending(false);
    }
  }

  async function handleGateDecide(gateId: string, decision: "approved" | "rejected") {
    try {
      await agentApi.decide(gateId, decision);
      toast.success(`Action ${decision}`);
      // No manual refresh needed — the run's SSE stream carries the gate
      // update and the resulting execution steps live.
    } catch (e: any) {
      toast.error(e.message);
    }
  }

  if (roles === null) {
    return (
      <AppLayout>
        <div className="flex items-center justify-center h-[calc(100vh-4rem)]">
          <div className="animate-spin w-8 h-8 border-4 border-primary-500 border-t-transparent rounded-full" />
        </div>
      </AppLayout>
    );
  }

  if (!canUseAgent) {
    return (
      <AppLayout>
        <div className="max-w-lg mx-auto mt-24 text-center space-y-3">
          <p className="text-4xl">🔒</p>
          <h1 className="text-xl font-bold text-gray-900">Agent Chat isn&apos;t available for your role</h1>
          <p className="text-sm text-gray-500">
            Only admin and receptionist accounts can run agent actions and approve or reject pending changes.
            Ask an admin or receptionist if you need something done here.
          </p>
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <div className="flex flex-col h-[calc(100vh-4rem)] max-w-3xl mx-auto">
        <h1 className="text-2xl font-bold text-gray-900 mb-4">Agent Chat</h1>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto space-y-4 pb-4">
          {loadingHistory && messages.length === 0 && (
            <p className="text-sm text-gray-400">Loading earlier conversations…</p>
          )}
          {/* Only offer the examples once history has loaded and is genuinely
              empty — otherwise they flash up and are replaced a moment later. */}
          {!loadingHistory && messages.length === 0 && (
            <div className="space-y-4">
              <p className="text-gray-500 text-sm">Ask the multi-agent system anything administrative. Examples:</p>
              <div className="grid grid-cols-1 gap-2">
                {EXAMPLES.map(ex => (
                  <button key={ex} onClick={() => send(ex)}
                    className="text-left px-4 py-3 bg-white border border-gray-200 rounded-xl text-sm text-gray-700 hover:border-primary-400 hover:bg-primary-50 transition">
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-xl ${m.role === "user"
                ? "bg-primary-600 text-white rounded-2xl rounded-tr-sm px-4 py-3 text-sm"
                : "bg-white border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-3 text-sm text-gray-800 w-full"
              }`}>
                {m.text}
                {m.runId && <RunDetail runId={m.runId} initialRun={m.run} onGateDecide={handleGateDecide} canDecide={canUseAgent} />}
              </div>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div className="flex gap-3 pt-3 border-t border-gray-200">
          <input
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === "Enter" && !e.shiftKey && (e.preventDefault(), send())}
            placeholder="Ask the agent… e.g. 'Schedule a checkup for P001 with Dr. Hoxha tomorrow at 2pm'"
            className="flex-1 px-4 py-3 border border-gray-300 rounded-xl text-sm focus:ring-2 focus:ring-primary-500 outline-none"
            disabled={sending}
          />
          <button onClick={() => send()} disabled={sending || !input.trim()}
            className="px-5 py-3 bg-primary-600 hover:bg-primary-700 text-white font-semibold rounded-xl transition disabled:opacity-40 text-sm">
            Send
          </button>
        </div>
      </div>
    </AppLayout>
  );
}
