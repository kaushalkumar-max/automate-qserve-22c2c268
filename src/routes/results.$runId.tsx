import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useState, type ReactNode } from "react";
import {
  CheckCircle2, XCircle, RotateCcw, Video, ExternalLink, Clock, Smartphone,
} from "lucide-react";
import AppHeader from "@/components/qserve/AppHeader";
import StepsTable from "@/components/qserve/StepsTable";
import StatusBadge from "@/components/qserve/StatusBadge";
import { getResults } from "@/lib/qserve.functions";

export const Route = createFileRoute("/results/$runId")({
  component: Results,
});

function Results() {
  const { runId } = Route.useParams();
  const navigate = useNavigate();
  const [run, setRun] = useState<any>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    getResults({ data: { run_id: runId } }).then(setRun).catch((e) => setErr(e.message));
  }, [runId]);

  if (err)
    return (
      <div className="min-h-screen bg-[#0d1117] text-white">
        <AppHeader />
        <div className="max-w-3xl mx-auto px-6 py-20 text-center">
          <h1 className="text-2xl font-mono-heading text-[#f85149]">Run not found</h1>
          <Link to="/" className="text-[#58a6ff] underline mt-4 inline-block">
            ← Back to dashboard
          </Link>
        </div>
      </div>
    );

  if (!run)
    return (
      <div className="min-h-screen bg-[#0d1117] text-white">
        <AppHeader />
        <div className="max-w-3xl mx-auto px-6 py-20 text-center text-[#8b949e]">
          Loading results…
        </div>
      </div>
    );

  const passed = run.passed;
  const Icon = passed ? CheckCircle2 : XCircle;
  const heroColor = passed ? "#3fb950" : "#f85149";

  return (
    <div className="min-h-screen bg-[#0d1117] text-white">
      <AppHeader />
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-10 space-y-8">
        <section
          data-testid="result-hero"
          className="bg-[#161b22] border border-[#30363d] rounded-md p-8 grid grid-cols-1 md:grid-cols-3 gap-6 items-center"
        >
          <div className="flex items-center gap-5">
            <div
              className="w-20 h-20 rounded-md flex items-center justify-center border"
              style={{
                borderColor: heroColor,
                backgroundColor: passed ? "rgba(63,185,80,0.08)" : "rgba(248,81,73,0.08)",
              }}
            >
              <Icon className="w-12 h-12" style={{ color: heroColor }} strokeWidth={2} />
            </div>
            <div>
              <StatusBadge passed={passed} status="completed" size="lg" testId="hero-badge" />
              <div className="mt-2 text-sm text-[#8b949e]">Run · {runId.slice(0, 8)}</div>
            </div>
          </div>
          <div className="md:col-span-2 grid grid-cols-2 gap-4">
            <Meta label="Test Case" value={run.test_case_name} />
            <Meta label="Build" value={run.build_name} mono />
            <Meta
              label="Device"
              value={run.os_version ? `${run.device} · Android ${run.os_version}` : run.device}
              icon={<Smartphone className="w-3 h-3" strokeWidth={2} />}
            />
            <Meta
              label="Duration"
              value={`${run.duration_seconds || 0}s`}
              icon={<Clock className="w-3 h-3" strokeWidth={2} />}
            />
          </div>
        </section>

        <section data-testid="actions-row" className="flex flex-wrap items-center gap-3">
          <button
            data-testid="run-again-button"
            onClick={() => navigate({ to: "/" })}
            className="inline-flex items-center gap-2 bg-[#2563eb] hover:bg-[#1d4ed8] text-white px-5 py-2.5 rounded-md font-mono-heading text-sm font-bold tracking-wide transition-colors"
          >
            <RotateCcw className="w-4 h-4" strokeWidth={2.25} /> Run Again
          </button>
          {run.video_url && (
            <a
              data-testid="video-link"
              href={run.video_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-2 border border-[#30363d] text-white px-5 py-2.5 rounded-md text-sm hover:bg-[#1f242c] transition-colors"
            >
              <Video className="w-4 h-4" strokeWidth={2.25} /> Video Recording
            </a>
          )}
          {run.public_url && (
            <a
              data-testid="bs-session-link"
              href={run.public_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-2 border border-[#30363d] text-[#8b949e] px-5 py-2.5 rounded-md text-sm hover:bg-[#1f242c] hover:text-white transition-colors"
            >
              <ExternalLink className="w-4 h-4" /> BrowserStack Session
            </a>
          )}
        </section>

        <section data-testid="steps-section" className="space-y-3">
          <div className="text-[11px] font-mono-heading uppercase tracking-[0.18em] text-[#8b949e]">
            Step-by-step results
          </div>
          <StepsTable steps={run.steps || []} plannedSteps={run.step_names || []} />
        </section>

        {run.screenshots && run.screenshots.length > 0 && (
          <section data-testid="screenshots-section" className="space-y-3">
            <div className="text-[11px] font-mono-heading uppercase tracking-[0.18em] text-[#8b949e]">
              Failure screenshots ({run.screenshots.length})
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
              {run.screenshots.map((s: string, i: number) => {
                const src = s.startsWith("http") || s.startsWith("data:") ? s : `data:image/png;base64,${s}`;
                return (
                  <a
                    key={i}
                    data-testid={`screenshot-${i}`}
                    href={src}
                    target="_blank"
                    rel="noreferrer"
                    className="block border border-[#30363d] rounded-md overflow-hidden hover:border-[#2563eb] transition-colors bg-[#0d1117]"
                  >
                    <img src={src} alt={`screenshot-${i}`} className="w-full h-48 object-cover" />
                  </a>
                );
              })}
            </div>
          </section>
        )}
      </main>
    </div>
  );
}

function Meta({
  label, value, icon, mono,
}: { label: string; value?: string | null; icon?: ReactNode; mono?: boolean }) {
  return (
    <div>
      <div className="text-[10px] font-mono-heading uppercase tracking-[0.14em] text-[#8b949e] mb-1 flex items-center gap-1">
        {icon} {label}
      </div>
      <div className={`text-sm text-white ${mono ? "font-mono-heading" : ""} truncate`}>
        {value || "—"}
      </div>
    </div>
  );
}
