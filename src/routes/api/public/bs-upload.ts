import { createFileRoute } from "@tanstack/react-router";

// Streams the incoming multipart upload straight to BrowserStack so the
// Worker never has to base64-encode or hold the full APK in memory.
// Client posts FormData with field "file" to /api/public/bs-upload?kind=apk|media.

export const Route = createFileRoute("/api/public/bs-upload")({
  server: {
    handlers: {
      POST: async ({ request }) => {
        const url = new URL(request.url);
        const kind = url.searchParams.get("kind") === "media" ? "media" : "apk";

        const user = process.env.BROWSERSTACK_USERNAME;
        const key = process.env.BROWSERSTACK_ACCESS_KEY;
        if (!user || !key) {
          return new Response(JSON.stringify({ error: "BrowserStack credentials not configured" }), {
            status: 500, headers: { "content-type": "application/json" },
          });
        }
        const auth = "Basic " + btoa(`${user}:${key}`);
        const contentType = request.headers.get("content-type");
        if (!contentType?.includes("multipart/form-data")) {
          return new Response(JSON.stringify({ error: "Expected multipart/form-data" }), {
            status: 400, headers: { "content-type": "application/json" },
          });
        }

        const target = kind === "media"
          ? "https://api-cloud.browserstack.com/app-automate/upload-media"
          : "https://api-cloud.browserstack.com/app-automate/upload";

        // Re-parse incoming multipart and forward as a fresh FormData.
        // Streaming request.body through workerd's fetch is unreliable for
        // large multipart uploads and was returning 500s.
        const inForm = await request.formData();
        const file = inForm.get("file") as unknown;
        if (!(file instanceof Blob)) {
          return new Response(JSON.stringify({ error: "Missing 'file' field" }), {
            status: 400, headers: { "content-type": "application/json" },
          });
        }
        const outForm = new FormData();
        const filename = (file as File).name || url.searchParams.get("filename") || (kind === "media" ? "qr.png" : "app.apk");
        outForm.append("file", file, filename);

        const bsRes = await fetch(target, {
          method: "POST",
          headers: { Authorization: auth },
          body: outForm,
        });

        const text = await bsRes.text();
        if (!bsRes.ok) {
          return new Response(JSON.stringify({ error: `BrowserStack ${bsRes.status}: ${text}` }), {
            status: 502, headers: { "content-type": "application/json" },
          });
        }

        // For QR media uploads, persist the media_url in our settings table.
        if (kind === "media") {
          try {
            const parsed = JSON.parse(text) as { media_url?: string };
            if (parsed.media_url) {
              const filename = url.searchParams.get("filename") || "qr.png";
              const { supabaseAdmin } = await import("@/integrations/supabase/client.server");
              await supabaseAdmin.from("qserve_settings").upsert({
                key: "qr_media",
                media_url: parsed.media_url,
                filename,
                uploaded_at: new Date().toISOString(),
              });
            }
          } catch {
            // fall through — return BrowserStack's body as-is
          }
        }

        return new Response(text, {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      },
    },
  },
});
