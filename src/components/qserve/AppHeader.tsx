import { Link } from "@tanstack/react-router";
import { Terminal } from "lucide-react";

export default function AppHeader() {
  return (
    <header data-testid="app-header" className="border-b border-[#30363d] bg-[#0d1117]">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4 flex items-center justify-between">
        <Link to="/" data-testid="header-home-link" className="flex items-center gap-3 group">
          <div className="w-9 h-9 rounded-md bg-[#161b22] border border-[#30363d] flex items-center justify-center group-hover:border-[#2563eb] transition-colors">
            <Terminal className="w-4 h-4 text-[#58a6ff]" strokeWidth={2.25} />
          </div>
          <div className="leading-tight">
            <div className="font-mono-heading text-base font-bold tracking-tight text-white">
              QServe<span className="text-[#58a6ff]">/</span>Test Manager
            </div>
            <div className="text-[10px] uppercase tracking-[0.18em] text-[#8b949e]">
              Android Build Testing
            </div>
          </div>
        </Link>
        <div className="hidden sm:flex items-center gap-3 text-xs text-[#8b949e] font-mono-heading">
          <span className="w-2 h-2 rounded-full bg-[#3fb950] pulse-dot" />
          BrowserStack Connected
        </div>
      </div>
    </header>
  );
}
