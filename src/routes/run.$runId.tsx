import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { Loader2, X } from "lucide-react";
import { toast, Toaster } from "sonner";
import AppHeader from "@/components/qserve/AppHeader";
import { getStatus, getResults } from "@/lib/qserve.functions";

export const Route = createFileRoute("/run/$runId")({
  component: TestRunning,
});

function TestRunning() {
  const { runId } = Route.useParams();
  const navigate = useNavigate();
  const [status, setStatus] = useState<any>(null);
  const [meta, setMeta] = useState<any>(null);
  const [logs, setLogs] = useState<{ ts: string; msg: string }[]>([]);
  const logsRef = useRef<HTMLDivElement>(null);
  const lastMsgRef = useRef("");

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      try {
        const s = await getStatus({ data: { run_id: runId } });
        if (cancelled) return;
        setStatus(s);
        if (s.message && s.message !== lastMsgRef.current) {
          lastMsgRef.current = s.message;
          setLogs((prev) => [...prev, { ts: new Date().toLocaleTimeString(), msg: s.message! }]);
        }
        if (s.status === "completed" || s.status === "passed" || s.status === "failed") {
          navigate({ to: "/results/$runId", params: { runId }, replace: true });
          return;
        }
      } catch {}
      timer = setTimeout(tick, 1500);
    };

    getResults({ data: { run_id: runId } }).then(setMeta).catch(() => toast.error("Run not found"));
    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [runId, navigate]);

  useEffect(() => {
    if (logsRef.current) logsRef.current.scrollTop = logsRef.current.scrollHeight;
  }, [logs]);

  const stepsDone = status?.steps_done ?? 0;
  const stepsTotal = status?.steps_total ?? 0;
  const pct = stepsTotal ? Math.min(100, Math.round((stepsDone / stepsTotal) * 100)) : 0;

  return (
    <div className="min-h-screen bg-[#0d1117] text-white">
      <Toaster theme="dark" position="top-right" />
      <AppHeader />
      <main className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-10 space-y-8">
        <div className="flex items-start justify-between gap-6 flex-wrap">
          <div>
            <div className="text-[11px] font-mono-heading uppercase tracking-[0.18em] text-[#8b949e]">
              Live Session · {runId.slice(0, 8)}
            </div>
            <h1
              data-testid="running-title"
              className="text-3xl sm:text-4xl font-bold tracking-tight font-mono-heading flex items-center gap-3"
            >
              <span className="w-3 h-3 rounded-full bg-[#58a6ff] pulse-dot inline-block" />
              Test in progress…
            </h1>
            {meta && (
              <p className="text-sm text-[#8b949e] mt-2">
                <span className="text-white font-medium">{meta.test_case_name}</span>{" "}
                · {meta.build_name} · {meta.device}
              </p>
            )}
          </div>
          <Link
            to="/"
            data-testid="cancel-button"
            className="inline-flex items-center gap-2 px-4 py-2 rounded-md border border-[#30363d] text-sm text-white hover:bg-[#1f242c] transition-colors"
          >
            <X className="w-4 h-4" /> Cancel
          </Link>
        </div>

        <div
          data-testid="progress-card"
          className="bg-[#161b22] border border-[#30363d] rounded-md p-6 space-y-4"
        >
          <div className="flex items-center justify-between text-xs font-mono-heading uppercase tracking-[0.14em] text-[#8b949e]">
            <span>Progress</span>
            <span>{stepsDone}/{stepsTotal} steps · {pct}%</span>
          </div>
          <div className="h-2 bg-[#0d1117] rounded-full border border-[#30363d] overflow-hidden">
            <div
              data-testid="progress-bar"
              className="h-full bg-[#2563eb] transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="flex items-center gap-2 text-sm text-white">
            <Loader2 className="w-4 h-4 animate-spin text-[#58a6ff]" />
            <span data-testid="current-step">
              {status?.current_step_name || status?.message || "Preparing…"}
            </span>
          </div>
        </div>

        <div data-testid="console" className="bg-[#0d1117] border border-[#30363d] rounded-md overflow-hidden">
          <div className="px-4 py-2 border-b border-[#30363d] flex items-center gap-2 text-[11px] font-mono-heading uppercase tracking-[0.14em] text-[#8b949e] bg-[#161b22]">
            <div className="flex gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full bg-[#f85149]" />
              <span className="w-2.5 h-2.5 rounded-full bg-[#d2a8ff]" />
              <span className="w-2.5 h-2.5 rounded-full bg-[#3fb950]" />
            </div>
            <span className="ml-2">browserstack · session log</span>
          </div>
          <div ref={logsRef} className="p-4 h-72 overflow-y-auto space-y-1">
            {logs.length === 0 ? (
              <div className="console-line text-[#484f58]">$ waiting for first event…</div>
            ) : (
              logs.map((l, i) => (
                <div key={i} data-testid={`console-line-${i}`} className="console-line text-[#8b949e]">
                  <span className="text-[#484f58]">[{l.ts}]</span>{" "}
                  <span className="text-[#58a6ff]">›</span>{" "}
                  <span className="text-white">{l.msg}</span>
                </div>
              ))
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
