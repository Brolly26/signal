/**
 * Browser-scoped demo identity.
 *
 * This is NOT authentication. It stops the same browser from supporting a
 * request twice by accident, and the real guarantee is the
 * `UNIQUE (request_id, voter_key)` constraint in the database — not this file.
 *
 * The locally remembered set of supported requests is a cache of what the API
 * already told us. Every vote response carries the authoritative `has_voted`,
 * so clearing browser storage loses the button state but never the vote, and
 * the next click re-syncs from the server.
 */

const KEY_STORAGE = "signal.voter_key";
const VOTED_STORAGE = "signal.supported_requests";
const KEY_PATTERN = /^[A-Za-z0-9_-]{8,64}$/;

/** Used when storage is unavailable (private window, blocked site data). */
let fallbackKey: string | null = null;

function createKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // Older browsers: still opaque, still within the pattern the API accepts.
  return Array.from({ length: 4 }, () =>
    Math.random().toString(36).slice(2, 10),
  ).join("-");
}

export function getVoterKey(): string {
  try {
    const existing = window.localStorage.getItem(KEY_STORAGE);
    if (existing && KEY_PATTERN.test(existing)) return existing;

    const created = createKey();
    window.localStorage.setItem(KEY_STORAGE, created);
    return created;
  } catch {
    // Voting still works for this page view; it just will not be remembered.
    fallbackKey ??= createKey();
    return fallbackKey;
  }
}

function readSupported(): Set<string> {
  try {
    const raw = window.localStorage.getItem(VOTED_STORAGE);
    if (!raw) return new Set();
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed)
      ? new Set(parsed.filter((value): value is string => typeof value === "string"))
      : new Set();
  } catch {
    // Unreadable or corrupt: treat as "nothing remembered".
    return new Set();
  }
}

export function hasSupportedLocally(requestId: string): boolean {
  return readSupported().has(requestId);
}

/** Record what the API reported, so a reload shows the same state. */
export function rememberSupport(requestId: string, hasVoted: boolean): void {
  try {
    const supported = readSupported();
    if (hasVoted) supported.add(requestId);
    else supported.delete(requestId);
    window.localStorage.setItem(VOTED_STORAGE, JSON.stringify([...supported]));
  } catch {
    // Nothing to do: the vote is already recorded server-side.
  }
}
