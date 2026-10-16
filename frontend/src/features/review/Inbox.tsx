/**
 * The review queue and the decision log.
 *
 * Server Components: both read uncached data inside the page's Suspense
 * boundary. The only client code here is the decision buttons.
 */

import Link from "next/link";

import { Badge, Card, EmptyState, ErrorPanel } from "@/components/ui";
import { ApiRequestError, formatDate, getDecisionHistory, getInbox } from "@/lib/api";
import type { Classification, InboxItem, Recommendation } from "@/lib/api";

import { CandidateDecision, DismissAnalysis } from "./DecisionActions";

const CLASSIFICATION_LABELS: Record<Classification, string> = {
  DUPLICATE: "Duplicate",
  RELATED: "Related",
  DISTINCT: "Distinct",
  NEEDS_CLARIFICATION: "Needs clarification",
};

const RECOMMENDATION_LABELS: Record<Recommendation, string> = {
  MERGE_CANDIDATE: "Consider consolidating",
  LINK_AS_RELATED: "Link as related",
  KEEP_SEPARATE: "Keep separate",
  REQUEST_CLARIFICATION: "Ask the requester a question",
};

function apiError(error: unknown, subject: string) {
  if (!(error instanceof ApiRequestError)) throw error;
  return (
    <ErrorPanel
      title={error.code === "network_error" ? "Cannot reach the API" : `Could not load ${subject}`}
      description={
        error.code === "network_error"
          ? "The Signal API is not responding. Start the backend and reload this page."
          : error.message
      }
    />
  );
}

function InboxCard({ item }: { item: InboxItem }) {
  return (
    <Card className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="accent">{CLASSIFICATION_LABELS[item.classification]}</Badge>
            {item.suggested_theme && <Badge>{item.suggested_theme}</Badge>}
            <span className="text-xs text-subtle">
              {item.vote_count} {item.vote_count === 1 ? "supporter" : "supporters"}
            </span>
          </div>
          <h2 className="text-base font-semibold leading-snug text-foreground">
            <Link
              href={`/requests/${item.request_id}`}
              className="underline-offset-4 hover:underline"
            >
              {item.request_title}
            </Link>
          </h2>
        </div>
        <DismissAnalysis analysisId={item.analysis_id} />
      </div>

      {item.underlying_need && (
        <div className="flex flex-col gap-1">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-subtle">
            Underlying need
          </h3>
          <p className="text-sm leading-relaxed text-foreground">{item.underlying_need}</p>
        </div>
      )}

      {item.recommendation && (
        <p className="text-sm text-muted">
          <span className="font-medium text-foreground">Suggested next step:</span>{" "}
          {RECOMMENDATION_LABELS[item.recommendation]}
        </p>
      )}

      {item.candidates.length === 0 ? (
        <p className="text-sm text-muted">
          No related request was proposed. {item.classification === "NEEDS_CLARIFICATION"
            ? "The request is too vague to compare — dismiss it once you have followed up."
            : ""}
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-subtle">
            Proposed relationships ({item.candidates.length})
          </h3>
          <ul className="flex flex-col gap-3">
            {item.candidates.map((candidate) => (
              <li
                key={candidate.request_id}
                className="flex flex-col gap-3 rounded-lg border border-border-subtle bg-surface-muted p-4"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <Link
                    href={`/requests/${candidate.request_id}`}
                    className="text-sm font-medium text-foreground underline-offset-4 hover:underline"
                  >
                    {candidate.title}
                  </Link>
                  <Badge
                    tone={candidate.relationship_type === "DUPLICATE" ? "danger" : "accent"}
                  >
                    {candidate.relationship_type === "DUPLICATE" ? "Duplicate" : "Related"}
                  </Badge>
                </div>
                {/* The evidence. Approving without it would be approving a guess. */}
                <p className="text-sm leading-relaxed text-muted">{candidate.explanation}</p>
                <CandidateDecision analysisId={item.analysis_id} candidate={candidate} />
              </li>
            ))}
          </ul>
        </div>
      )}

      {item.reasoning && (
        <details className="text-sm">
          <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-subtle">
            Model reasoning
          </summary>
          <p className="mt-2 leading-relaxed text-muted">{item.reasoning}</p>
        </details>
      )}
    </Card>
  );
}

export async function InboxQueue() {
  let page;
  try {
    page = await getInbox({ limit: 25 });
  } catch (error) {
    return apiError(error, "the review queue");
  }

  if (page.items.length === 0) {
    return (
      <EmptyState
        title="Nothing to review"
        description="Analyses appear here once the AI has proposed relationships for a request. Submit a request to see one."
      />
    );
  }

  return (
    <section className="flex flex-col gap-4" aria-label="Review queue">
      <p className="text-sm text-muted">
        {page.total} {page.total === 1 ? "analysis" : "analyses"} awaiting a decision
      </p>
      <ul className="flex flex-col gap-4">
        {page.items.map((item) => (
          <li key={item.analysis_id}>
            <InboxCard item={item} />
          </li>
        ))}
      </ul>
    </section>
  );
}

export async function DecisionLog() {
  let page;
  try {
    page = await getDecisionHistory({ limit: 25 });
  } catch (error) {
    return apiError(error, "the decision history");
  }

  if (page.items.length === 0) {
    return (
      <p className="text-sm text-muted">
        No decisions yet. Every approval and rejection is recorded here with its
        reason, and changing a decision adds an entry rather than replacing one.
      </p>
    );
  }

  return (
    <ul className="flex flex-col gap-2">
      {page.items.map((entry) => (
        <li
          key={entry.id}
          className="flex flex-wrap items-baseline gap-x-2 gap-y-1 border-b border-border-subtle pb-2 text-sm last:border-0"
        >
          <Badge tone={entry.decision === "APPROVED" ? "success" : "neutral"}>
            {entry.decision === "APPROVED"
              ? "Approved"
              : entry.decision === "REJECTED"
                ? "Rejected"
                : "Dismissed"}
          </Badge>
          <Link
            href={`/requests/${entry.request_id}`}
            className="font-medium text-foreground underline-offset-4 hover:underline"
          >
            {entry.request_title}
          </Link>
          {entry.related_request_title && (
            <span className="text-muted">↔ {entry.related_request_title}</span>
          )}
          <span className="text-xs text-subtle">
            {entry.reviewer_label} · {formatDate(entry.created_at)}
          </span>
          {entry.reason && (
            <span className="w-full text-sm text-muted">“{entry.reason}”</span>
          )}
        </li>
      ))}
    </ul>
  );
}

export function InboxSkeleton() {
  return (
    <div className="flex flex-col gap-4" aria-busy="true">
      <span className="sr-only">Loading the review queue…</span>
      {[0, 1].map((index) => (
        <Card key={index} className="flex flex-col gap-3">
          <div className="h-5 w-1/3 animate-pulse rounded bg-surface-muted" />
          <div className="h-4 w-full animate-pulse rounded bg-surface-muted" />
          <div className="h-20 w-full animate-pulse rounded bg-surface-muted" />
        </Card>
      ))}
    </div>
  );
}
