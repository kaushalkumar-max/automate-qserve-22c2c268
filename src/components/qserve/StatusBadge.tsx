type Props = {
  status?: string | null;
  passed?: boolean | null;
  size?: "sm" | "lg";
  testId?: string;
};

export default function StatusBadge({ status, passed, size = "sm", testId }: Props) {
  let label: string, classes: string;
  if (status === "running" || status === "queued") {
    label = status === "queued" ? "QUEUED" : "RUNNING";
    classes = "bg-[rgba(88,166,255,0.1)] text-[#58a6ff] border border-[rgba(88,166,255,0.25)]";
  } else if (passed === true) {
    label = "PASSED";
    classes = "bg-[rgba(63,185,80,0.1)] text-[#3fb950] border border-[rgba(63,185,80,0.25)]";
  } else if (passed === false) {
    label = "FAILED";
    classes = "bg-[rgba(248,81,73,0.1)] text-[#f85149] border border-[rgba(248,81,73,0.25)]";
  } else {
    label = "—";
    classes = "bg-[#161b22] text-[#8b949e] border border-[#30363d]";
  }
  const sizeClasses = size === "lg" ? "px-4 py-1.5 text-sm" : "px-2.5 py-0.5 text-[11px]";
  return (
    <span
      data-testid={testId || "status-badge"}
      className={`inline-flex items-center gap-2 ${sizeClasses} ${classes} rounded-full font-bold uppercase tracking-[0.12em] font-mono-heading`}
    >
      {(status === "running" || status === "queued") && (
        <span className="w-1.5 h-1.5 rounded-full bg-[#58a6ff] pulse-dot" />
      )}
      {label}
    </span>
  );
}
