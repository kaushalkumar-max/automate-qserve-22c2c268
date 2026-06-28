import { createFileRoute } from "@tanstack/react-router";

async function isRunnerAuthorized(request: Request) {
  const user = process.env.BROWSERSTACK_USERNAME;
  const key = process.env.BROWSERSTACK_ACCESS_KEY;
  if (!user || !key) return false;

  const expected = "Basic " + btoa(`${user}:${key}`);
  const actual = request.headers.get("authorization") ?? "";
  if (actual.length !== expected.length) return false;

  const { timingSafeEqual } = await import("crypto");
  return timingSafeEqual(Buffer.from(actual), Buffer.from(expected));
}

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export const Route = createFileRoute("/api/public/runner-next")({
  server: {
    handlers: {
      GET: async ({ request }) => {
        if (!(await isRunnerAuthorized(request))) return json({ error: "Unauthorized" }, 401);

        const { supabaseAdmin } = await import("@/integrations/supabase/client.server");

        // Reap stale runs: any 'running'/'starting' row whose runner has gone
        // silent for >90s (Render free tier restarts, crashes, network drops).
        // Without this they'd stay 'running' forever because nothing else
        // updates them.
        const cutoff = new Date(Date.now() - 90_000).toISOString();
        await supabaseAdmin
          .from("test_runs")
          .update({
            status: "failed",
            passed: false,
            message: "Runner process stopped responding (no heartbeat for >90s). Run abandoned.",
            updated_at: new Date().toISOString(),
          })
          .in("status", ["running", "starting"])
          .lt("updated_at", cutoff);

        const { data: queued, error: readError } = await supabaseAdmin
          .from("test_runs")
          .select("*")
          .eq("status", "queued")
          .order("created_at", { ascending: true })
          .limit(1)
          .maybeSingle();

        if (readError) return json({ error: readError.message }, 500);
        if (!queued) return json({ job: null });


        const { data: claimed, error: claimError } = await supabaseAdmin
          .from("test_runs")
          .update({
            status: "starting",
            message: "Picked up by external runner…",
            updated_at: new Date().toISOString(),
          })
          .eq("run_id", queued.run_id)
          .eq("status", "queued")
          .select("*")
          .maybeSingle();

        if (claimError) return json({ error: claimError.message }, 500);
        if (!claimed) return json({ job: null });

        // Attach QR media URL (already uploaded to BrowserStack as media://...)
        const { data: qr } = await supabaseAdmin
          .from("qserve_settings")
          .select("media_url")
          .eq("key", "qr_media")
          .maybeSingle();

        return json({ job: { ...claimed, qr_media_url: qr?.media_url ?? null } });
      },
    },
  },
});
