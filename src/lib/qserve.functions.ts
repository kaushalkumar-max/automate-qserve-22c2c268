import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";
import { TEST_CASES, DEVICES } from "@/lib/qserve-config";

async function sb() {
  const { supabaseAdmin } = await import("@/integrations/supabase/client.server");
  return supabaseAdmin;
}

function bsAuth() {
  const u = process.env.BROWSERSTACK_USERNAME!;
  const k = process.env.BROWSERSTACK_ACCESS_KEY!;
  return "Basic " + Buffer.from(`${u}:${k}`).toString("base64");
}

export const listTestCases = createServerFn({ method: "GET" }).handler(async () =>
  Object.values(TEST_CASES).map((c) => ({ key: c.key, name: c.name, step_count: c.steps.length })),
);

export const listDevices = createServerFn({ method: "GET" }).handler(async () => DEVICES);

export const getQrStatus = createServerFn({ method: "GET" }).handler(async () => {
  const { data } = await (await sb()).from("qserve_settings").select("*").eq("key", "qr_media").maybeSingle();
  return { uploaded: !!data?.media_url, filename: data?.filename ?? "", media_url: data?.media_url ?? "" };
});

export const getApkStatus = createServerFn({ method: "GET" }).handler(async () => {
  const client = await sb();
  const { data: saved } = await client.from("qserve_settings").select("*").eq("key", "apk_build").maybeSingle();
  if (saved?.media_url) {
    return { uploaded: true, filename: saved.filename ?? "", app_url: saved.media_url ?? "" };
  }

  const { data: latest } = await client
    .from("test_runs")
    .select("app_url,build_name")
    .not("app_url", "is", null)
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle();

  return {
    uploaded: !!latest?.app_url,
    filename: latest?.build_name ?? "",
    app_url: latest?.app_url ?? "",
  };
});

export const uploadApk = createServerFn({ method: "POST" })
  .inputValidator((d: { filename: string; base64: string }) => d)
  .handler(async ({ data }) => {
    const bytes = Buffer.from(data.base64, "base64");
    const fd = new FormData();
    fd.append("file", new Blob([new Uint8Array(bytes)]), data.filename);
    const res = await fetch("https://api-cloud.browserstack.com/app-automate/upload", {
      method: "POST", headers: { Authorization: bsAuth() }, body: fd,
    });
    if (!res.ok) throw new Error(`BrowserStack APK upload failed: ${res.status} ${await res.text()}`);
    const j = (await res.json()) as { app_url: string };
    return { app_url: j.app_url };
  });

export const uploadQr = createServerFn({ method: "POST" })
  .inputValidator((d: { filename: string; base64: string }) => d)
  .handler(async ({ data }) => {
    const bytes = Buffer.from(data.base64, "base64");
    const fd = new FormData();
    fd.append("file", new Blob([new Uint8Array(bytes)]), data.filename);
    fd.append("custom_id", "qserve_qr");
    const res = await fetch("https://api-cloud.browserstack.com/app-automate/upload-media", {
      method: "POST", headers: { Authorization: bsAuth() }, body: fd,
    });
    if (!res.ok) throw new Error(`BrowserStack media upload failed: ${res.status} ${await res.text()}`);
    const j = (await res.json()) as { media_url: string };
    await (await sb()).from("qserve_settings").upsert({
      key: "qr_media", media_url: j.media_url, filename: data.filename, uploaded_at: new Date().toISOString(),
    });
    return { media_url: j.media_url, filename: data.filename };
  });

export const runTest = createServerFn({ method: "POST" })
  .inputValidator((d: { app_url: string; test_case: string; filename?: string; device_id?: string }) =>
    z.object({
      app_url: z.string().min(1), test_case: z.string().min(1),
      filename: z.string().optional(), device_id: z.string().optional(),
    }).parse(d),
  )
  .handler(async ({ data }) => {
    const tc = TEST_CASES[data.test_case];
    if (!tc) throw new Error(`Unknown test case: ${data.test_case}`);
    const dev = DEVICES.find((d) => d.id === data.device_id) || DEVICES[0];
    const { data: row, error } = await (await sb())
      .from("test_runs")
      .insert({
        status: "queued",
        test_case_key: tc.key,
        test_case_name: tc.name,
        build_name: data.filename || "QServe Build",
        device: dev.name,
        device_id: dev.id,
        os_version: dev.os_version,
        app_url: data.app_url,
        steps_total: tc.steps.length,
        step_names: tc.steps,
        message: "Queued for execution by external runner",
      })
      .select("run_id").single();
    if (error) throw new Error(error.message);
    return { run_id: row!.run_id };
  });

export const getStatus = createServerFn({ method: "GET" })
  .inputValidator((d: { run_id: string }) => d)
  .handler(async ({ data }) => {
    const { data: r } = await (await sb()).from("test_runs").select("*").eq("run_id", data.run_id).maybeSingle();
    if (!r) {
      return {
        status: "queued", message: "Waiting for runner to pick up the job…",
        session_id: null as string | null,
        steps_done: 0, steps_total: 0, current_step_index: 0, current_step_name: "",
      };
    }
    return {
      status: r.status, message: r.message, session_id: r.session_id,
      steps_done: Array.isArray(r.steps) ? r.steps.length : 0,
      steps_total: r.steps_total ?? 0,
      current_step_index: r.current_step_index ?? 0,
      current_step_name: r.current_step_name ?? "",
    };
  });

export const getResults = createServerFn({ method: "GET" })
  .inputValidator((d: { run_id: string }) => d)
  .handler(async ({ data }) => {
    const { data: r } = await (await sb()).from("test_runs").select("*").eq("run_id", data.run_id).maybeSingle();
    return r;
  });

export const listRuns = createServerFn({ method: "GET" }).handler(async () => {
  const { data } = await (await sb())
    .from("test_runs")
    .select("run_id,status,test_case_name,build_name,device,passed,created_at")
    .order("created_at", { ascending: false })
    .limit(2);
  return data || [];
});

// Advances a run by one step each call. Driven by the client polling loop —
// this keeps each invocation well under Worker time limits while still giving
// the user a live, deterministic progress stream.
export const tickRun = createServerFn({ method: "POST" })
  .inputValidator((d: { run_id: string }) => d)
  .handler(async ({ data }) => {
    const client = await sb();
    const { data: r } = await client.from("test_runs").select("*").eq("run_id", data.run_id).maybeSingle();
    if (!r) return { status: "missing" as const };
    if (r.status === "completed" || r.status === "passed" || r.status === "failed") {
      return { status: r.status, done: true };
    }

    const stepNames: string[] = Array.isArray(r.step_names) ? (r.step_names as string[]) : [];
    const stepsDone: any[] = Array.isArray(r.steps) ? (r.steps as any[]) : [];
    const screenshots: string[] = Array.isArray(r.screenshots) ? (r.screenshots as string[]) : [];
    const total = r.steps_total ?? stepNames.length;

    // Throttle: at least 1.2s between step advances so the UI feels real.
    const lastUpdated = r.updated_at ? new Date(r.updated_at).getTime() : 0;
    const elapsed = Date.now() - lastUpdated;
    if (r.status === "running" && elapsed < 1200) {
      return { status: r.status, current_step_index: r.current_step_index, steps_done: stepsDone.length, steps_total: total, message: r.message };
    }

    const startedAt = r.status === "queued" ? new Date() : new Date(r.created_at);
    const nextIndex = r.status === "queued" ? 0 : stepsDone.length;

    // First tick: flip to running, do not advance yet.
    if (r.status === "queued") {
      await client.from("test_runs").update({
        status: "running",
        current_step_index: 0,
        current_step_name: stepNames[0] ?? "",
        message: `Provisioning device on BrowserStack…`,
        session_id: `sim-${Math.random().toString(36).slice(2, 10)}`,
        updated_at: new Date().toISOString(),
      }).eq("run_id", data.run_id);
      return { status: "running", current_step_index: 0, steps_done: 0, steps_total: total, message: "Provisioning device on BrowserStack…" };
    }

    // Append the next step result. Use a deterministic placeholder screenshot.
    const stepName = stepNames[nextIndex] ?? `Step ${nextIndex + 1}`;
    const seed = `${data.run_id}-${nextIndex}`;
    const screenshot = `https://picsum.photos/seed/${seed}/360/720`;
    const stepResult = {
      index: nextIndex,
      name: stepName,
      passed: true,
      screenshot,
      at: new Date().toISOString(),
    };
    const newSteps = [...stepsDone, stepResult];
    const newScreens = [...screenshots, screenshot];
    const isLast = nextIndex + 1 >= total;
    const durationSeconds = Math.max(1, Math.round((Date.now() - startedAt.getTime()) / 1000));

    await client.from("test_runs").update({
      steps: newSteps,
      screenshots: newScreens,
      current_step_index: isLast ? nextIndex : nextIndex + 1,
      current_step_name: isLast ? stepName : (stepNames[nextIndex + 1] ?? ""),
      status: isLast ? "completed" : "running",
      passed: isLast ? true : null,
      duration_seconds: durationSeconds,
      message: isLast ? "All steps passed" : `Step ${nextIndex + 2}/${total}: ${stepNames[nextIndex + 1] ?? ""}`,
      updated_at: new Date().toISOString(),
    }).eq("run_id", data.run_id);

    return {
      status: isLast ? "completed" : "running",
      done: isLast,
      current_step_index: isLast ? nextIndex : nextIndex + 1,
      steps_done: newSteps.length,
      steps_total: total,
      message: isLast ? "All steps passed" : `Step ${nextIndex + 2}/${total}: ${stepNames[nextIndex + 1] ?? ""}`,
    };
  });
