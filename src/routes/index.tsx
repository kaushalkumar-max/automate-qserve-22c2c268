import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { toast, Toaster } from "sonner";
import {
  Upload, Play, FileBox, QrCode, ListChecks, CheckCircle2,
} from "lucide-react";
import AppHeader from "@/components/qserve/AppHeader";
import RecentRuns from "@/components/qserve/RecentRuns";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  listTestCases, listDevices, getQrStatus, getApkStatus, runTest, listRuns,
} from "@/lib/qserve.functions";

export const Route = createFileRoute("/")({
  component: Dashboard,
});


function Dashboard() {
  const navigate = useNavigate();
  const [testCases, setTestCases] = useState<{ key: string; name: string; step_count: number }[]>([]);
  const [selectedCase, setSelectedCase] = useState("");
  const [devices, setDevices] = useState<{ id: string; name: string; os_version: string }[]>([]);
  const [selectedDevice, setSelectedDevice] = useState("");
  const [apkFile, setApkFile] = useState<File | null>(null);
  const [apkFilename, setApkFilename] = useState("");
  const [apkUploading, setApkUploading] = useState(false);
  const [apkUrl, setApkUrl] = useState<string | null>(null);
  const [qrUploading, setQrUploading] = useState(false);
  const [qrUploaded, setQrUploaded] = useState(false);
  const [qrFilename, setQrFilename] = useState("");
  const [runs, setRuns] = useState<any[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [starting, setStarting] = useState(false);

  const apkInputRef = useRef<HTMLInputElement>(null);
  const qrInputRef = useRef<HTMLInputElement>(null);

  const refreshQr = () =>
    getQrStatus().then((d) => {
      setQrUploaded(!!d.uploaded);
      if (d.filename) setQrFilename(d.filename);
    }).catch(() => {});

  const refreshApk = () =>
    getApkStatus().then((d) => {
      if (d.uploaded && d.app_url) {
        setApkUrl(d.app_url);
        setApkFilename(d.filename || "Retained BrowserStack build");
      }
    }).catch(() => {});

  const refreshRuns = () => {
    setRunsLoading(true);
    listRuns().then((r) => setRuns(r as any[])).catch(() => {}).finally(() => setRunsLoading(false));
  };

  useEffect(() => {
    listTestCases().then((cases) => {
      setTestCases(cases);
      if (cases.length) setSelectedCase(cases[0].key);
    }).catch((e) => toast.error("Failed to load test cases", { description: e.message }));
    listDevices().then((d) => {
      setDevices(d);
      if (d.length) setSelectedDevice(d[0].id);
    }).catch(() => {});
    refreshApk();
    refreshQr();
    refreshRuns();
  }, []);

  const onApkPick = async (file?: File) => {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".apk")) {
      toast.error("Only .apk files are allowed");
      return;
    }
    setApkFile(file);
    setApkFilename(file.name);
    setApkUrl(null);
    setApkUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file, file.name);
      const r = await fetch("/api/public/bs-upload?kind=apk", { method: "POST", body: fd });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || `Upload failed (${r.status})`);
      setApkUrl(j.app_url);
      setApkFilename(file.name);
      toast.success("APK uploaded to BrowserStack", { description: j.app_url });
    } catch (e: any) {
      toast.error("APK upload failed", { description: e.message });
      setApkFile(null);
    } finally {
      setApkUploading(false);
    }
  };

  const onQrPick = async (file?: File) => {
    if (!file) return;
    setQrUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file, file.name);
      const r = await fetch(`/api/public/bs-upload?kind=media&filename=${encodeURIComponent(file.name)}`, { method: "POST", body: fd });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || `Upload failed (${r.status})`);
      setQrUploaded(true);
      setQrFilename(file.name);
      toast.success("QR image uploaded", { description: j.media_url });
    } catch (e: any) {
      toast.error("QR upload failed", { description: e.message });
    } finally {
      setQrUploading(false);
    }
  };

  const startRun = async () => {
    if (!apkUrl || !selectedCase) return;
    setStarting(true);
    try {
      const res = await runTest({
        data: {
          app_url: apkUrl,
          test_case: selectedCase,
          filename: apkFile?.name || apkFilename || undefined,
          device_id: selectedDevice || undefined,
        },
      });
      navigate({ to: "/run/$runId", params: { runId: res.run_id } });
    } catch (e: any) {
      toast.error("Failed to start test", { description: e.message });
    } finally {
      setStarting(false);
    }
  };

  const canRun = !!apkUrl && !!selectedCase && !!selectedDevice && qrUploaded && !starting;

  return (
    <div className="min-h-screen bg-[#0d1117] text-white">
      <Toaster theme="dark" position="top-right" />
      <AppHeader />
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-10 space-y-10">
        <section data-testid="hero-section" className="space-y-2">
          <div className="text-[11px] font-mono-heading uppercase tracking-[0.18em] text-[#8b949e]">
            // Mission Control
          </div>
          <h1 className="text-3xl sm:text-4xl font-bold tracking-tight font-mono-heading">
            Ship Android builds with confidence.
          </h1>
          <p className="text-sm text-[#8b949e] max-w-2xl">
            Upload an APK, pick a scripted scenario, and watch Appium drive it on real
            cloud devices via BrowserStack — no emulators, no ADB.
          </p>
        </section>

        <section className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div data-testid="apk-card" className="bg-[#161b22] border border-[#30363d] rounded-md p-6 flex flex-col">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <FileBox className="w-4 h-4 text-[#58a6ff]" strokeWidth={2.25} />
                <h3 className="text-xs font-mono-heading uppercase tracking-[0.14em] text-[#8b949e]">
                  01 · Upload Build
                </h3>
              </div>
              {apkUrl && <CheckCircle2 className="w-4 h-4 text-[#3fb950]" strokeWidth={2.25} />}
            </div>
            <button
              type="button"
              data-testid="apk-drop-zone"
              onClick={() => apkInputRef.current?.click()}
              className="border-2 border-dashed border-[#30363d] hover:border-[#2563eb] rounded-md p-8 flex flex-col items-center justify-center bg-[#0d1117] transition-colors cursor-pointer flex-1 min-h-[180px]"
            >
              <Upload className="w-6 h-6 text-[#58a6ff] mb-3" strokeWidth={2} />
              <div className="text-sm text-white font-medium">
                {apkFile ? apkFile.name : apkFilename || "Drop .apk or click to browse"}
              </div>
              <div className="text-[11px] text-[#8b949e] font-mono-heading mt-1">
                {apkUploading ? "Uploading to BrowserStack..." : apkUrl ? "Retained for testing until replaced" : "Single .apk file"}
              </div>
            </button>
            <input
              ref={apkInputRef}
              data-testid="apk-file-input"
              type="file"
              accept=".apk"
              className="hidden"
              onChange={(e) => onApkPick(e.target.files?.[0])}
            />
          </div>

          <div data-testid="testcase-card" className="bg-[#161b22] border border-[#30363d] rounded-md p-6 flex flex-col">
            <div className="flex items-center gap-2 mb-4">
              <ListChecks className="w-4 h-4 text-[#58a6ff]" strokeWidth={2.25} />
              <h3 className="text-xs font-mono-heading uppercase tracking-[0.14em] text-[#8b949e]">
                02 · Select Test Case
              </h3>
            </div>
            <div className="flex-1 flex flex-col justify-center">
              <Select value={selectedCase} onValueChange={setSelectedCase}>
                <SelectTrigger data-testid="testcase-select-trigger" className="bg-[#0d1117] border-[#30363d] text-white h-12 font-mono-heading text-sm">
                  <SelectValue placeholder="Login → Logout" />
                </SelectTrigger>
                <SelectContent
                  data-testid="testcase-select-content"
                  className="bg-[#161b22] border-[#30363d] text-white"
                >
                  {testCases.map((tc) => (
                    <SelectItem
                      data-testid={`testcase-option-${tc.key}`}
                      key={tc.key}
                      value={tc.key}
                      className="focus:bg-[#1f242c] focus:text-white"
                    >
                      {tc.name}
                      <span className="ml-2 text-xs text-[#8b949e]">({tc.step_count} steps)</span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <div className="text-[11px] text-[#8b949e] font-mono-heading mt-3">
                Only the current login-to-logout case is enabled.
              </div>
            </div>
          </div>

          <div data-testid="qr-card" className="bg-[#161b22] border border-[#30363d] rounded-md p-6 flex flex-col">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <QrCode className="w-4 h-4 text-[#58a6ff]" strokeWidth={2.25} />
                <h3 className="text-xs font-mono-heading uppercase tracking-[0.14em] text-[#8b949e]">
                  03 · Login QR Image
                </h3>
              </div>
              {qrUploaded && <CheckCircle2 className="w-4 h-4 text-[#3fb950]" strokeWidth={2.25} />}
            </div>
            <button
              type="button"
              data-testid="qr-drop-zone"
              onClick={() => qrInputRef.current?.click()}
              className="border-2 border-dashed border-[#30363d] hover:border-[#2563eb] rounded-md p-8 flex flex-col items-center justify-center bg-[#0d1117] transition-colors cursor-pointer flex-1 min-h-[180px]"
            >
              <QrCode className="w-6 h-6 text-[#58a6ff] mb-3" strokeWidth={2} />
              <div className="text-sm text-white font-medium">
                {qrUploaded ? qrFilename || "QR image ready" : "Upload QR image"}
              </div>
              <div className="text-[11px] text-[#8b949e] font-mono-heading mt-1">
                {qrUploading ? "Uploading..." : qrUploaded ? "Reused across all runs" : "PNG / JPG — required for login flow"}
              </div>
            </button>
            <input
              ref={qrInputRef}
              data-testid="qr-file-input"
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(e) => onQrPick(e.target.files?.[0])}
            />
          </div>
        </section>

        <section
          data-testid="run-cta-section"
          className="flex flex-col md:flex-row items-stretch md:items-center gap-4 bg-[#161b22] border border-[#30363d] rounded-md p-6"
        >
          <div className="flex-1 min-w-0">
            <div className="text-xs font-mono-heading uppercase tracking-[0.14em] text-[#8b949e] mb-1">
              Target Device
            </div>
            <Select value={selectedDevice} onValueChange={setSelectedDevice}>
              <SelectTrigger
                data-testid="device-select-trigger"
                className="bg-[#0d1117] border-[#30363d] text-white h-11 font-mono-heading text-sm"
              >
                <SelectValue placeholder="Choose a device..." />
              </SelectTrigger>
              <SelectContent
                data-testid="device-select-content"
                className="bg-[#161b22] border-[#30363d] text-white max-h-72"
              >
                {devices.map((d) => (
                  <SelectItem
                    key={d.id}
                    value={d.id}
                    data-testid={`device-option-${d.id}`}
                    className="focus:bg-[#1f242c] focus:text-white"
                  >
                    {d.name}
                    <span className="ml-2 text-xs text-[#8b949e]">Android {d.os_version}</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <div className="text-[11px] text-[#8b949e] mt-2">
              {canRun
                ? "All systems go. Hit Run Test to dispatch to BrowserStack."
                : "Upload APK and QR image to enable Run."}
            </div>
          </div>
          <button
            type="button"
            data-testid="run-test-button"
            onClick={startRun}
            disabled={!canRun}
            className={`flex items-center justify-center gap-2 px-8 py-4 rounded-md font-mono-heading font-bold text-base tracking-wide transition-all ${
              canRun
                ? "bg-[#2563eb] hover:bg-[#1d4ed8] text-white"
                : "bg-[#1f242c] text-[#484f58] cursor-not-allowed border border-[#30363d]"
            }`}
          >
            <Play className="w-4 h-4 fill-current" strokeWidth={2.5} />
            {starting ? "DISPATCHING…" : "RUN TEST"}
          </button>
        </section>

        <section data-testid="recent-runs-section">
          <div className="flex items-end justify-between mb-4">
            <div>
              <div className="text-[11px] font-mono-heading uppercase tracking-[0.18em] text-[#8b949e]">
                Recent Activity
              </div>
              <h2 className="text-xl font-semibold tracking-tight font-mono-heading">
                Recent Test Runs
              </h2>
            </div>
            <button
              type="button"
              data-testid="refresh-runs-button"
              onClick={refreshRuns}
              className="text-xs font-mono-heading text-[#58a6ff] hover:text-[#2563eb] uppercase tracking-wider"
            >
              Refresh ↻
            </button>
          </div>
          <RecentRuns runs={runs} loading={runsLoading} />
        </section>
      </main>
    </div>
  );
}
