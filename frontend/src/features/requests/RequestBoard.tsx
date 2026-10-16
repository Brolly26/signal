/**
 * The board's data region.
 *
 * An async Server Component: the fetch is uncached, so it runs on every
 * request and a page reload always reflects the database. It must be rendered
 * inside a `<Suspense>` boundary — with Cache Components enabled, that is how
 * Next.js streams uncached reads after the shell. The search parameters are
 * awaited here, inside the boundary, so the page shell stays prerenderable.
 */

import Link from "next/link";

import { Badge, Card, ButtonLink, EmptyState, ErrorPanel } from "@/components/ui";
import { ApiRequestError, formatDate, listRequests } from "@/lib/api";
import type {
  AnalysisState,
  FeatureRequest,
  FeatureRequestPage,
  RequestSort,
  RequestStatus,
} from "@/lib/api";

import { BoardControls, BoardControlsSkeleton } from "./BoardControls";
import { VoteButton } from "./VoteButton";

const STATUS_LABELS: Record<RequestStatus, string> = {
  open: "Open",
  triaged: "Triaged",
  planned: "Planned",
  declined: "Declined",
};

const ANALYSIS_LABELS: Record<AnalysisState, string> = {
  not_requested: "Not analysed",
  pending: "Analysing",
  completed: "Analysed",
  failed: "Analysis failed",
};

function RequestCard({ request }: { request: FeatureRequest }) {
  return (
    <Card className="flex gap-4 transition-colors hover:border-border-strong">
      <div className="shrink-0 pt-0.5">
        <VoteButton requestId={request.id} initialCount={request.vote_count} />
      </div>

      <div className="flex min-w-0 flex-col gap-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <h3 className="text-base font-semibold leading-snug text-foreground">
            <Link
              href={`/requests/${request.id}`}
              className="underline-offset-4 hover:underline"
            >
              {request.title}
            </Link>
          </h3>
          <div className="flex shrink-0 items-center gap-2">
            <Badge tone={request.status === "open" ? "accent" : "neutral"}>
              {STATUS_LABELS[request.status]}
            </Badge>
            <Badge tone={request.analysis_state === "failed" ? "danger" : "neutral"}>
              {ANALYSIS_LABELS[request.analysis_state]}
            </Badge>
          </div>
        </div>

        <p className="line-clamp-3 whitespace-pre-line text-sm leading-relaxed text-muted">
          {request.description}
        </p>

        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-subtle">
          <span>{request.requester_label}</span>
          <span aria-hidden="true">·</span>
          <time dateTime={request.created_at}>{formatDate(request.created_at)}</time>
        </div>
      </div>
    </Card>
  );
}

function ResultSummary({ page }: { page: FeatureRequestPage }) {
  const noun = page.total === 1 ? "request" : "requests";
  return (
    <p className="text-sm text-muted">
      {page.query ? (
        <>
          {page.total} {noun} matching “{page.query}”
        </>
      ) : (
        <>
          {page.total} {noun}
        </>
      )}
      {page.total > page.items.length
        ? ` · showing the first ${page.items.length}`
        : ""}
      {page.sort === "top" ? " · most supported first" : ""}
    </p>
  );
}

type SearchParams = Promise<{ [key: string]: string | string[] | undefined }>;

/** An unrecognised value falls back to the default rather than erroring: the
 *  URL is user-editable, and a hand-typed `?sort=xyz` should not break a page. */
function readParams(resolved: Awaited<SearchParams>): {
  query: string | null;
  sort: RequestSort;
} {
  const raw = typeof resolved.q === "string" ? resolved.q.trim() : "";
  return {
    query: raw || null,
    sort: resolved.sort === "top" ? "top" : "recent",
  };
}

export async function RequestBoard({ searchParams }: { searchParams: SearchParams }) {
  const { query, sort } = readParams(await searchParams);

  let page: FeatureRequestPage;
  try {
    page = await listRequests({ limit: 50, q: query ?? undefined, sort });
  } catch (error) {
    // Only API failures are a UI state. A TypeError in this component is a bug,
    // and dressing it up as "the API responded with an error" would hide it, so
    // it is re-thrown to the error boundary instead.
    if (!(error instanceof ApiRequestError)) throw error;

    const isNetwork = error.code === "network_error";
    return (
      <ErrorPanel
        title={isNetwork ? "Cannot reach the API" : "Could not load requests"}
        description={
          isNetwork
            ? "The Signal API is not responding. Start the backend with `uvicorn app.main:app --reload --port 8000` and reload this page."
            : "The API responded with an error. Check the backend logs for details."
        }
      />
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <BoardControls query={page.query} sort={page.sort} />

      {page.items.length === 0 ? (
        query ? (
          <EmptyState
            title="No matching requests"
            description={`Nothing matches “${query}”. Try a different term, or clear the search to see everything.`}
            action={
              // Keeps the chosen ordering, like the Clear link in the controls.
              <ButtonLink
                href={
                  page.sort === "recent"
                    ? "/"
                    : { pathname: "/", query: { sort: page.sort } }
                }
                variant="secondary"
              >
                Clear search
              </ButtonLink>
            }
          />
        ) : (
          <EmptyState
            title="No requests yet"
            description="Feature requests submitted by customers and stakeholders will appear here. Submit the first one to get started."
            action={<ButtonLink href="/requests/new">New request</ButtonLink>}
          />
        )
      ) : (
        <section className="flex flex-col gap-4" aria-label="Feature requests">
          <ResultSummary page={page} />
          <ul className="flex flex-col gap-3">
            {page.items.map((request) => (
              <li key={request.id}>
                <RequestCard request={request} />
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

/** Shown by the Suspense boundary while the board data is loading. */
export function RequestBoardSkeleton() {
  return (
    <div className="flex flex-col gap-5" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading requests…</span>
      <BoardControlsSkeleton />
      <div className="flex flex-col gap-3">
        {[0, 1, 2].map((index) => (
          <Card key={index} className="flex flex-col gap-3">
            <div className="h-5 w-2/5 animate-pulse rounded bg-surface-muted" />
            <div className="h-4 w-full animate-pulse rounded bg-surface-muted" />
            <div className="h-4 w-3/4 animate-pulse rounded bg-surface-muted" />
          </Card>
        ))}
      </div>
    </div>
  );
}
