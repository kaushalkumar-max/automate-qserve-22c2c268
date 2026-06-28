import { useEffect, useState } from "react";
import { getRunnerHealth } from "@/lib/qserve.functions";

type Health = {
  ok: boolean;
  error?: string;
  runner_started?: boolean;
  runner?: {
    last_poll_at?: string | null;
    last_job_id?: string | null;
    last_step?: string | null;
    last_heartbeat_at?: string | null;
  };
};

function secsAgo(iso?: string | null): string {
  if (!iso) return "—";
  const d = Date.parse(iso);
  if (Number.isNaN(d)) return "—";
  const s = Math.max(0, Math.round((Date.now() - d) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  return `${Math.round(s / 3600)}h ago`;
}

export default function RunnerStatus() {
  const [h, setH] = useState<Health | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const r = (await getRunnerHealth()) as Health;
        if (!cancelled) setH(r);
      } catch {
        if (!cancelled) setH({ ok: false, error: "fetch failed" });
      }
    };
    tick();
    const id = setInterval(tick, 10_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const lastPoll = h?.runner?.last_poll_at;
  const lastPollMs = lastPoll ? Date.now() - Date.parse(lastPoll) : Infinity;
  const alive = !!h?.ok && !!h?.runner_started && lastPollMs < 30_000;
  const stale = !!h?.ok && !!h?.runner_started && lastPollMs >= 30_000;

  const dot = !h
    ? "bg-[#484f58]"
    : alive
      ? "bg-[#3fb950]"
      : stale
        ? "bg-[#d29922]"
        : "bg-[#f85149]";

  const label = !h
    ? "Checking runner…"
    : !h.ok
      ? `Runner unreachable${h.error ? ` (${h.error})` : ""}`
      : !h.runner_started
        ? "Runner thread not started"
        : alive
          ? `Runner online · last poll ${secsAgo(lastPoll)}`
          : `Runner idle · last poll ${secsAgo(lastPoll)}`;

  return (
    <div
      data-testid="runner-status"
      title={h?.runner?.last_step ? `Last step: ${h.runner.last_step}` : undefined}
      className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md border border-[#30363d] bg-[#161b22] text-[11px] font-mono-heading uppercase tracking-[0.14em] text-[#8b949e]"
    >
      <span className={`w-2 h-2 rounded-full ${dot} ${alive ? "pulse-dot" : ""}`} />
      <span className="text-white normal-case tracking-normal">{label}</span>
    </div>
  );
}
