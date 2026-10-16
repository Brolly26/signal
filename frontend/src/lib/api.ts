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
  vote_count: number;
  created_at: string;
}


export type AnalysisStatus = "pending" | "completed" | "failed";

export type Classification =
  | "DUPLICATE"
  | "RELATED"
  | "DISTINCT"
  | "NEEDS_CLARIFICATION";

export type Recommendation =
  | "MERGE_CANDIDATE"
  | "LINK_AS_RELATED"
  | "KEEP_SEPARATE"
  | "REQUEST_CLARIFICATION";

export type AnalysisErrorCode =
  | "TIMEOUT"
  | "PROVIDER_ERROR"
  | "INVALID_OUTPUT"
  | "REFUSAL"
  | "INTERRUPTED"
  | "FABRICATED_REFERENCES"
  | "UNEXPECTED";

export type RelationshipType = "DUPLICATE" | "RELATED";

/** A candidate relationship that survived reference checking, with its evidence. */
export interface RelatedRequest {
  request_id: string;
  title: string;
  relationship_type: RelationshipType;
  explanation: string;
  vote_count: number;
}

export interface Analysis {
  id: string;
  request_id: string;
  status: AnalysisStatus;
  classification: Classification | null;
  recommendation: Recommendation | null;
  underlying_need: string | null;
  problem_summary: string | null;
  suggested_theme: string | null;
  reasoning: string | null;
  related_requests: RelatedRequest[];
  model_name: string | null;
  retrieval_mode: "agentic" | "backend_fallback" | null;
  tool_calls_count: number | null;
  fabricated_refs_count: number;
  latency_ms: number | null;
  error_code: AnalysisErrorCode | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}


export type Decision = "APPROVED" | "REJECTED" | "DISMISSED";

/** A human-approved relationship. A decision, not a suggestion. */
export interface RequestLink {
  linked_request_id: string;
  linked_request_title: string;
  link_type: RelationshipType;
  created_at: string;
}

export interface InboxCandidate {
  request_id: string;
  title: string;
  relationship_type: RelationshipType;
  explanation: string;
  vote_count: number;
  decision: Decision | null;
}

export interface InboxItem {
  analysis_id: string;
  request_id: string;
  request_title: string;
  request_description: string;
  vote_count: number;
  classification: Classification;
  recommendation: Recommendation | null;
  underlying_need: string | null;
  problem_summary: string | null;
  suggested_theme: string | null;
  reasoning: string | null;
  candidates: InboxCandidate[];
  created_at: string;
  completed_at: string | null;
}

export interface InboxPage {
  items: InboxItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface DecisionEntry {
  id: string;
  analysis_id: string;
  request_id: string;
  request_title: string;
  related_request_id: string | null;
  related_request_title: string | null;
  decision: Decision;
  reviewer_label: string;
  reason: string | null;
  created_at: string;
}

export interface DecisionHistoryPage {
  items: DecisionEntry[];
  total: number;
  limit: number;
  offset: number;
}

export interface FeatureRequestDetail extends FeatureRequest {
  analysis: Analysis | null;
  links: RequestLink[];
}

/** How the board is ordered. `top` ranks by support, which is a demand signal
 *  and not a priority — the product never conflates the two. */
export type RequestSort = "recent" | "top";

export interface FeatureRequestPage {
  items: FeatureRequest[];
  total: number;
  limit: number;
  offset: number;
  query: string | null;
  sort: RequestSort;
}

/** Returned by every vote operation, including the idempotent no-op, so the
 *  client can correct drifted local state from the authoritative count. */
export interface VoteResult {
  request_id: string;
  vote_count: number;
  has_voted: boolean;
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
  {
    limit = 50,
    offset = 0,
    q,
    sort = "recent",
  }: { limit?: number; offset?: number; q?: string; sort?: RequestSort } = {},
): Promise<FeatureRequestPage> {
  const query = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
    sort,
  });
  if (q) query.set("q", q);

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

/**
 * Support a request. Idempotent: the API answers 201 when the vote is newly
 * recorded and 200 when this identity had already supported it, and both
 * carry the authoritative count.
 */
export async function supportRequest(
  requestId: string,
  voterKey: string,
): Promise<VoteResult> {
  return voteRequest("POST", requestId, voterKey);
}

/** Withdraw support. Also idempotent. */
export async function withdrawSupport(
  requestId: string,
  voterKey: string,
): Promise<VoteResult> {
  return voteRequest("DELETE", requestId, voterKey);
}

async function voteRequest(
  method: "POST" | "DELETE",
  requestId: string,
  voterKey: string,
): Promise<VoteResult> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/requests/${requestId}/votes`, {
      method,
      headers: { "X-Voter-Key": voterKey, Accept: "application/json" },
    });
  } catch {
    throw new ApiRequestError("Could not reach the Signal API.", {
      code: "network_error",
    });
  }

  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as VoteResult;
}

/** One request with its most recent analysis. */
export async function getRequest(requestId: string): Promise<FeatureRequestDetail> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/requests/${requestId}`, {
      headers: { Accept: "application/json" },
    });
  } catch {
    throw new ApiRequestError("Could not reach the Signal API.", {
      code: "network_error",
    });
  }

  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as FeatureRequestDetail;
}

/**
 * Queue the analysis again.
 *
 * 503 when no provider is configured, 404 when the request is gone, 409 while
 * an analysis is still running — all surfaced as ApiRequestError with the
 * message the API chose.
 */
export async function retryAnalysis(requestId: string): Promise<FeatureRequestDetail> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/requests/${requestId}/analyses`, {
      method: "POST",
      headers: { Accept: "application/json" },
    });
  } catch {
    throw new ApiRequestError("Could not reach the Signal API.", {
      code: "network_error",
    });
  }

  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as FeatureRequestDetail;
}

/** Analyses waiting for a human decision. */
export async function getInbox(
  { limit = 25, offset = 0 }: { limit?: number; offset?: number } = {},
): Promise<InboxPage> {
  return getJson<InboxPage>(`/api/inbox?limit=${limit}&offset=${offset}`);
}

/** The decision audit log, newest first. */
export async function getDecisionHistory(
  { limit = 25 }: { limit?: number } = {},
): Promise<DecisionHistoryPage> {
  return getJson<DecisionHistoryPage>(`/api/decisions?limit=${limit}`);
}

/** Approve, reject or dismiss. Returns the analysis as it now stands. */
export async function submitDecision(
  analysisId: string,
  body: {
    decision: Decision;
    related_request_id?: string;
    reason?: string;
    reviewer_label?: string;
  },
): Promise<InboxItem> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/analyses/${analysisId}/decisions`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiRequestError("Could not reach the Signal API.", {
      code: "network_error",
    });
  }

  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as InboxItem;
}

async function getJson<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      headers: { Accept: "application/json" },
    });
  } catch {
    throw new ApiRequestError("Could not reach the Signal API.", {
      code: "network_error",
    });
  }

  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as T;
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
