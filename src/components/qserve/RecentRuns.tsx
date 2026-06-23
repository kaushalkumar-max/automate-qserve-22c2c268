import { Link } from "@tanstack/react-router";
import StatusBadge from "./StatusBadge";
import { ChevronRight, Smartphone } from "lucide-react";

type Run = {
  run_id: string;
  status: string;
  test_case_name: string;
  build_name?: string | null;
  device?: string | null;
  passed?: boolean | null;
  created_at: string;
};

function timeAgo(iso?: string) {
  if (!iso) return "—";
  const d = new Date(iso);
  const secs = Math.max(1, (Date.now() - d.getTime()) / 1000);
  if (secs < 60) return `${Math.floor(secs)}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

export default function RecentRuns({ runs = [], loading }: { runs?: Run[]; loading?: boolean }) {
  if (loading) {
    return (
      <div data-testid="recent-runs-loading" className="text-xs text-[#8b949e] font-mono-heading">
        Loading runs…
      </div>
    );
  }
  if (!runs.length) {
    return (
      <div
        data-testid="recent-runs-empty"
        className="border border-dashed border-[#30363d] rounded-md p-8 text-center"
      >
        <div className="text-sm text-[#8b949e]">
          No test runs yet. Upload an APK and run your first test.
        </div>
      </div>
    );
  }
  return (
    <div data-testid="recent-runs-list" className="space-y-2">
      {runs.map((r) => {
        const target = r.status === "completed" ? `/results/${r.run_id}` : `/run/${r.run_id}`;
        return (
          <Link
            key={r.run_id}
            to={target}
            data-testid={`recent-run-${r.run_id}`}
            className="flex items-center justify-between gap-4 p-4 bg-[#161b22] border border-[#30363d] rounded-md hover:border-[#2563eb] hover:bg-[#1f242c] transition-colors"
          >
            <div className="flex items-center gap-4 min-w-0">
              <div className="hidden sm:flex w-10 h-10 rounded-md bg-[#0d1117] border border-[#30363d] items-center justify-center">
                <Smartphone className="w-4 h-4 text-[#58a6ff]" strokeWidth={2} />
              </div>
              <div className="min-w-0">
                <div className="text-sm font-medium text-white truncate">{r.test_case_name}</div>
                <div className="text-[11px] font-mono-heading text-[#8b949e] truncate">
                  {r.build_name} · {r.device} · {timeAgo(r.created_at)}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-3 shrink-0">
              <StatusBadge status={r.status} passed={r.passed} testId={`run-badge-${r.run_id}`} />
              <ChevronRight className="w-4 h-4 text-[#484f58]" />
            </div>
          </Link>
        );
      })}
    </div>
  );
}
