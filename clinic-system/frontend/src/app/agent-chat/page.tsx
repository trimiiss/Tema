"use client";
import { useEffect, useRef, useState } from "react";
import AppLayout from "@/components/layout/AppLayout";
import { agentApi, usersApi } from "@/lib/api";
import { sessionStart, chatId, newChatId } from "@/lib/session";
import toast from "react-hot-toast";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

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

// An agent message carried a "Processing… (run 0cd943e3)" label, written once
// when the run started and never updated — so a finished run still announced
// itself as processing, above its own answer, and quoted a run id that means
// nothing to the person reading it. `RunDetail` already reports live status
// from the run itself, so the bubble carries no text of its own.
const RUN_HAS_NO_LABEL = "";

// How persistently to read a run's row back when its stream stops early — see
// `reconcile` in `RunDetail`. Ten tries, three seconds apart, covers the runs
// this system produces with room to spare.
const RECONCILE_ATTEMPTS = 10;
const RECONCILE_DELAY_MS = 3000;

/** Rebuild the transcript from the runs the backend already stores. */
function messagesFromRuns(runs: any[]): Message[] {
  // The endpoint returns newest first; a transcript reads oldest first.
  return [...runs].reverse().flatMap((run): Message[] => [
    { role: "user", text: run.input_text },
    { role: "agent", text: RUN_HAS_NO_LABEL, runId: run.id, run },
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
    let cancelled = false;
    // Tracked alongside the state so `reconcile` can tell a stream that ended
    // because the run finished from one that ended for any other reason.
    let status: string | undefined = initialRun?.status;

    const apply = (event: any) => {
      if (event.type === "snapshot") status = event.run?.status;
      if (event.type === "status" && event.status) status = event.status;
      setRun((prev: any) => {
        if (event.type === "snapshot") {
          return { ...event.run, steps: event.steps, gates: event.gates };
        }
        if (!prev) return prev;
        if (event.type === "step") {
          // The snapshot is read after the subscription opens (see
          // `api/agents.stream_run`), so a step can legitimately arrive in
          // both. Same row, same id — keep the one already listed.
          const steps = prev.steps ?? [];
          if (steps.some((s: any) => s.id === event.step.id)) return prev;
          return { ...prev, steps: [...steps, event.step] };
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
    };

    /** Read the run row back until it settles.
     *
     * The stream is the fast path, not the only one. When it ends before the
     * run does — a dropped connection, an idle response some proxy closed, a
     * machine that slept, a stream that never opened at all — nothing was ever
     * going to correct the trace again: the run went on to finish and store
     * its answer, while the page sat on the last few steps it happened to
     * catch and reported "running" indefinitely. The row is authoritative and
     * one request cheap, so read it rather than leave the answer stranded. */
    async function reconcile() {
      for (let attempt = 0; attempt < RECONCILE_ATTEMPTS && !cancelled; attempt++) {
        await new Promise(r => setTimeout(r, RECONCILE_DELAY_MS));
        if (cancelled) return;
        try {
          const latest = await agentApi.getRun(runId);
          // `getRun` carries the run's whole trace, so this also replaces any
          // steps the interrupted stream had left half-listed.
          setRun((prev: any) => ({ ...(prev ?? {}), ...latest }));
          if (latest.status !== "running") return;
        } catch { /* a later attempt can still succeed */ }
      }
    }

    agentApi.streamRun(runId, apply, controller.signal)
      .catch((e: any) => { if (e?.name !== "AbortError") console.error(e); })
      .finally(() => { if (!cancelled && !isFinished(status)) reconcile(); });

    return () => { cancelled = true; controller.abort(); };
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
        <div className="bg-gray-50 rounded-lg p-3 text-sm text-gray-700">
          <p className="font-semibold text-gray-500 mb-1 text-xs">Result</p>
          {run.result.message ? (
            <div className="prose prose-sm max-w-none prose-p:my-1 prose-ul:my-1 prose-ol:my-1">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{run.result.message}</ReactMarkdown>
            </div>
          ) : (
            <pre className="whitespace-pre-wrap break-all text-xs">{JSON.stringify(run.result, null, 2)}</pre>
          )}
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
  // Scoped to the *current login* and the *current conversation*: replaying a
  // run from three days ago, or from a chat the user deliberately moved on
  // from with "New Chat", presents it as part of this thread, so the user
  // reads answers to questions they don't remember asking. `sessionStart` is
  // when this login began; `chatId` is this conversation's id — signing out,
  // or starting a new chat, leaves a clean transcript behind while navigation
  // and reloads within one sitting still restore everything.
  useEffect(() => {
    let cancelled = false;
    sessionStart().then(since =>
      agentApi.listRuns(since, chatId())
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
      const run = await agentApi.run(msg, chatId());
      setMessages(m => [...m, { role: "agent", text: RUN_HAS_NO_LABEL, runId: run.id }]);
    } catch (e: any) {
      toast.error(e.message);
      setMessages(m => [...m, { role: "agent", text: `Error: ${e.message}` }]);
    } finally {
      setSending(false);
    }
  }

  /** Starts a fresh conversation: a new chat id, so the next message carries
   * no memory of this one and `GET /agent/runs` won't restore it either. The
   * old conversation isn't deleted — it just stops being "the" thread. */
  function startNewChat() {
    if (sending) return;
    newChatId();
    setMessages([]);
    setInput("");
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
        <div className="flex items-center justify-between mb-4">
          <h1 className="text-2xl font-bold text-gray-900">Agent Chat</h1>
          <button onClick={startNewChat} disabled={sending || messages.length === 0}
            className="px-3 py-1.5 text-sm font-medium text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50 hover:text-gray-900 transition disabled:opacity-40">
            + New chat
          </button>
        </div>

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
