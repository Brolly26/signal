/**
 * Typed client for the Signal API.
 *
 * These types mirror the Pydantic schemas in `backend/app/schemas/request.py`.
 * Nothing enforces that they stay in sync — that is the cost of the two-service
 * split, and it is recorded in docs/decisions.md (ADR-001).
 */

export type RequestStatus = "open" | "triaged" | "planned" | "declined";

/**
 * `not_requested` means no analysis row exists for the request. Until the
 * orchestrator ships, that is the only state a new request can be in: nothing
 * is queued, so nothing sits pending forever.
 */
export type AnalysisState = "not_requested" | "pending" | "completed" | "failed";

export interface FeatureRequest {
  id: string;
  title: string;
  description: string;
  requester_label: string;
  status: RequestStatus;
  analysis_state: AnalysisState;
  created_at: string;
}

export interface FeatureRequestPage {
  items: FeatureRequest[];
  total: number;
  limit: number;
  offset: number;
}

export interface NewFeatureRequest {
  title: string;
  description: string;
  requester_label?: string;
}

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/** An error the API reported, carrying per-field messages when it has them. */
export class ApiRequestError extends Error {
  readonly code: string;
  readonly status: number;
  readonly fields: Record<string, string>;

  constructor(
    message: string,
    options: { code?: string; status?: number; fields?: Record<string, string> } = {},
  ) {
    super(message);
    this.name = "ApiRequestError";
    this.code = options.code ?? "unknown_error";
    this.status = options.status ?? 0;
    this.fields = options.fields ?? {};
  }
}

interface ApiErrorBody {
  error?: { code?: string; message?: string; fields?: Record<string, string> };
}

async function toApiError(response: Response): Promise<ApiRequestError> {
  let body: ApiErrorBody = {};
  try {
    body = (await response.json()) as ApiErrorBody;
  } catch {
    // A non-JSON body (a proxy error page, for instance) leaves the defaults.
  }

  return new ApiRequestError(body.error?.message ?? "The request failed.", {
    code: body.error?.code,
    status: response.status,
    fields: body.error?.fields,
  });
}

/**
 * Fetch a page of requests.
 *
 * Deliberately uncached: in Next.js 16 `fetch` is uncached by default, which is
 * what we want here — the board must show what is in the database, including
 * immediately after a submission. The caller is responsible for wrapping this
 * in a `<Suspense>` boundary.
 */
export async function listRequests(
  { limit = 50, offset = 0 }: { limit?: number; offset?: number } = {},
): Promise<FeatureRequestPage> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/requests?${query}`, {
      headers: { Accept: "application/json" },
    });
  } catch {
    throw new ApiRequestError("Could not reach the Signal API.", {
      code: "network_error",
    });
  }

  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as FeatureRequestPage;
}

/** Submit a new request. Throws ApiRequestError with `fields` set on a 422. */
export async function createRequest(input: NewFeatureRequest): Promise<FeatureRequest> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/requests`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(input),
    });
  } catch {
    throw new ApiRequestError("Could not reach the Signal API.", {
      code: "network_error",
    });
  }

  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as FeatureRequest;
}

const DATE_FORMAT = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

/** Format a timestamp deterministically, so server and client agree. */
export function formatDate(isoTimestamp: string): string {
  return DATE_FORMAT.format(new Date(isoTimestamp));
}
