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

        // Stream the request body straight through — no buffering in the Worker.
        const bsRes = await fetch(target, {
          method: "POST",
          headers: { Authorization: auth, "content-type": contentType },
          body: request.body,
          // @ts-expect-error — required by undici/workerd for streaming bodies
          duplex: "half",
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
