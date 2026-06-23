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
  const { data } = await sb().from("qserve_settings").select("*").eq("key", "qr_media").maybeSingle();
  return { uploaded: !!data?.media_url, filename: data?.filename ?? "", media_url: data?.media_url ?? "" };
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
    await sb().from("qserve_settings").upsert({
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
    const { data: row, error } = await sb()
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
    const { data: r } = await sb().from("test_runs").select("*").eq("run_id", data.run_id).single();
    if (!r) throw new Error("Run not found");
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
    const { data: r } = await sb().from("test_runs").select("*").eq("run_id", data.run_id).single();
    if (!r) throw new Error("Run not found");
    return r;
  });

export const listRuns = createServerFn({ method: "GET" }).handler(async () => {
  const { data } = await sb()
    .from("test_runs")
    .select("run_id,status,test_case_name,build_name,device,passed,created_at")
    .order("created_at", { ascending: false })
    .limit(20);
  return data || [];
});
