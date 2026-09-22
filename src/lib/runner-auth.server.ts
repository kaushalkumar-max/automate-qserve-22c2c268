import { timingSafeEqual } from "crypto";

const CURRENT_RUNNER_USERNAME = "rohitkumar_m6xDSl";
const VALIDATION_TTL_MS = 10 * 60 * 1000;

let validatedAuthorization = "";
let validatedUntil = 0;

function safeEqual(actual: string, expected: string) {
  if (actual.length !== expected.length) return false;
  return timingSafeEqual(Buffer.from(actual), Buffer.from(expected));
}

function parseBasicAuthorization(authorization: string) {
  if (!authorization.startsWith("Basic ")) return null;

  try {
    const decoded = Buffer.from(authorization.slice(6), "base64").toString("utf8");
    const separator = decoded.indexOf(":");
    if (separator < 1) return null;

    return {
      username: decoded.slice(0, separator).trim(),
      accessKey: decoded.slice(separator + 1).trim(),
    };
  } catch {
    return null;
  }
}

export async function isRunnerAuthorized(request: Request) {
  const actual = request.headers.get("authorization")?.trim() ?? "";
  if (!actual) return false;

  const configuredUser = process.env.BROWSERSTACK_USERNAME?.trim();
  const configuredKey = process.env.BROWSERSTACK_ACCESS_KEY?.trim();
  if (configuredUser && configuredKey) {
    const expected = `Basic ${Buffer.from(`${configuredUser}:${configuredKey}`).toString("base64")}`;
    if (safeEqual(actual, expected)) return true;
  }

  // A stale deployment secret must not strand queued runs. Verify the current
  // runner credentials directly with BrowserStack, without storing or logging
  // the received access key. Successful checks are cached for warm instances.
  const credentials = parseBasicAuthorization(actual);
  if (!credentials || credentials.username !== CURRENT_RUNNER_USERNAME || !credentials.accessKey) {
    return false;
  }

  if (safeEqual(actual, validatedAuthorization) && Date.now() < validatedUntil) {
    return true;
  }

  try {
    const response = await fetch("https://api-cloud.browserstack.com/app-automate/plan.json", {
      headers: { authorization: actual },
      signal: AbortSignal.timeout(8_000),
    });
    if (!response.ok) return false;

    validatedAuthorization = actual;
    validatedUntil = Date.now() + VALIDATION_TTL_MS;
    return true;
  } catch {
    return false;
  }
}