import { CheckCircle2, XCircle, MinusCircle } from "lucide-react";

type Step = { name: string; status: string; error?: string };

export default function StepsTable({
  steps = [],
  plannedSteps = [],
}: {
  steps?: Step[];
  plannedSteps?: string[];
}) {
  const map = Object.fromEntries(steps.map((s) => [s.name, s]));
  const rows = (plannedSteps.length ? plannedSteps : steps.map((s) => s.name)).map((name, idx) => {
    const s = map[name];
    return {
      idx: idx + 1,
      name,
      status: s ? s.status : "pending",
      error: s ? s.error || "" : "",
    };
  });

  return (
    <div
      data-testid="steps-table"
      className="border border-[#30363d] rounded-md overflow-hidden bg-[#161b22]"
    >
      <table className="w-full text-left border-collapse">
        <thead>
          <tr className="text-[11px] font-mono-heading uppercase tracking-[0.12em] text-[#8b949e] bg-[#0d1117]">
            <th className="py-3 px-4 w-12">#</th>
            <th className="py-3 px-4">Step</th>
            <th className="py-3 px-4 w-32">Status</th>
            <th className="py-3 px-4">Notes</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.idx}
              data-testid={`step-row-${r.idx}`}
              className="border-t border-[#30363d] hover:bg-[#1f242c] transition-colors"
            >
              <td className="py-3 px-4 text-xs text-[#8b949e] font-mono-heading">
                {String(r.idx).padStart(2, "0")}
              </td>
              <td className="py-3 px-4 text-sm text-white">{r.name}</td>
              <td className="py-3 px-4">
                {r.status === "pass" && (
                  <span className="inline-flex items-center gap-1.5 text-[#3fb950] text-xs font-mono-heading font-bold uppercase">
                    <CheckCircle2 className="w-4 h-4" strokeWidth={2.25} /> Pass
                  </span>
                )}
                {r.status === "fail" && (
                  <span className="inline-flex items-center gap-1.5 text-[#f85149] text-xs font-mono-heading font-bold uppercase">
                    <XCircle className="w-4 h-4" strokeWidth={2.25} /> Fail
                  </span>
                )}
                {r.status === "skipped" && (
                  <span className="inline-flex items-center gap-1.5 text-[#8b949e] text-xs font-mono-heading uppercase">
                    <MinusCircle className="w-4 h-4" strokeWidth={2} /> Skipped
                  </span>
                )}
                {r.status === "pending" && (
                  <span className="text-[#484f58] text-xs font-mono-heading uppercase">
                    — Pending
                  </span>
                )}
              </td>
              <td className="py-3 px-4 text-xs text-[#8b949e] font-mono-heading break-all">
                {r.error}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
