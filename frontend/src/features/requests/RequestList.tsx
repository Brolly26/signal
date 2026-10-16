/**
 * The board's data region.
 *
 * An async Server Component: the fetch is uncached, so it runs on every
 * request and a page reload always reflects the database. It must be rendered
 * inside a `<Suspense>` boundary — with Cache Components enabled, that is how
 * Next.js streams uncached reads after the shell.
 */

import { Badge, Card, ButtonLink, EmptyState, ErrorPanel } from "@/components/ui";
import { ApiRequestError, formatDate, listRequests } from "@/lib/api";
import type { AnalysisState, FeatureRequest, RequestStatus } from "@/lib/api";

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
    <Card className="flex flex-col gap-3 transition-colors hover:border-border-strong">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h3 className="text-base font-semibold leading-snug text-foreground">
          {request.title}
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

      <p className="whitespace-pre-line text-sm leading-relaxed text-muted">
        {request.description}
      </p>

      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-subtle">
        <span>{request.requester_label}</span>
        <span aria-hidden="true">·</span>
        <time dateTime={request.created_at}>{formatDate(request.created_at)}</time>
      </div>
    </Card>
  );
}

export async function RequestList() {
  let page;
  try {
    page = await listRequests({ limit: 50 });
  } catch (error) {
    const isNetwork = error instanceof ApiRequestError && error.code === "network_error";
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

  if (page.items.length === 0) {
    return (
      <EmptyState
        title="No requests yet"
        description="Feature requests submitted by customers and stakeholders will appear here. Submit the first one to get started."
        action={<ButtonLink href="/requests/new">New request</ButtonLink>}
      />
    );
  }

  return (
    <section className="flex flex-col gap-4" aria-label="Feature requests">
      <p className="text-sm text-muted">
        {page.total} {page.total === 1 ? "request" : "requests"}
        {page.total > page.items.length ? ` · showing the ${page.items.length} most recent` : ""}
      </p>
      <ul className="flex flex-col gap-3">
        {page.items.map((request) => (
          <li key={request.id}>
            <RequestCard request={request} />
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Shown by the Suspense boundary while the board data is loading. */
export function RequestListSkeleton() {
  return (
    <div className="flex flex-col gap-3" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading requests…</span>
      {[0, 1, 2].map((index) => (
        <Card key={index} className="flex flex-col gap-3">
          <div className="h-5 w-2/5 animate-pulse rounded bg-surface-muted" />
          <div className="h-4 w-full animate-pulse rounded bg-surface-muted" />
          <div className="h-4 w-3/4 animate-pulse rounded bg-surface-muted" />
        </Card>
      ))}
    </div>
  );
}
