import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";
import { isRunnerAuthorized } from "@/lib/runner-auth.server";

const UpdateSchema = z.object({
  run_id: z.string().min(1),
  patch: z.record(z.unknown()),
});

const ALLOWED_PATCH_FIELDS = new Set([
  "status",
  "passed",
  "message",
  "duration_seconds",
  "public_url",
  "video_url",
  "current_step_index",
  "current_step_name",
  "session_id",
  "steps",
  "screenshots",
]);

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export const Route = createFileRoute("/api/public/runner-update")({
  server: {
    handlers: {
      PATCH: async ({ request }) => {
        if (!(await isRunnerAuthorized(request))) return json({ error: "Unauthorized" }, 401);

        const parsed = UpdateSchema.safeParse(await request.json().catch(() => null));
        if (!parsed.success) return json({ error: "Invalid runner update" }, 400);

        const patch: Record<string, unknown> = {};
        for (const [key, value] of Object.entries(parsed.data.patch)) {
          if (ALLOWED_PATCH_FIELDS.has(key)) patch[key] = value;
        }
        patch.updated_at = new Date().toISOString();

        const { supabaseAdmin } = await import("@/integrations/supabase/client.server");
        const { error } = await supabaseAdmin
          .from("test_runs")
          .update(patch as never)
          .eq("run_id", parsed.data.run_id);

        if (error) return json({ error: error.message }, 500);
        return json({ ok: true });
      },
    },
  },
});