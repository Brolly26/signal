"use client";

/**
 * The analysis view.
 *
 * Client-side because it polls: an analysis is queued when the request is
 * submitted and finishes a few seconds later, so the panel follows it to a
 * terminal state and then stops. Polling is bounded — after the budget it
 * stops and offers a retry rather than hammering the API forever.
 *
 * Every claim shown here carries its evidence. The relationship a reviewer
 * acts on is per candidate, with the explanation that justified it; the
 * headline classification is the aggregate the backend derived.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { Badge, Button, Card, ErrorPanel, Spinner } from "@/components/ui";
import { ApiRequestError, getRequest, retryAnalysis } from "@/lib/api";
import type {
  Analysis,
  AnalysisErrorCode,
  Classification,
  FeatureRequestDetail,
  Recommendation,
  RelatedRequest,
} from "@/lib/api";

const POLL_INTERVAL_MS = 1500;
const POLL_BUDGET = 40; // ~60 seconds

const CLASSIFICATION_COPY: Record<Classification, { label: string; detail: string }> = {
  DUPLICATE: {
    label: "Duplicate",
    detail: "Another request appears to describe the same need.",
  },
  RELATED: {
    label: "Related",
    detail: "Other requests share the underlying problem without being the same.",
  },
  DISTINCT: {
    label: "Distinct",
    detail: "No sufficiently related request was found.",
  },
  NEEDS_CLARIFICATION: {
    label: "Needs clarification",
    detail: "There is not enough detail to compare this against other requests.",
  },
};

const RECOMMENDATION_LABELS: Record<Recommendation, string> = {
  MERGE_CANDIDATE: "Consider consolidating",
  LINK_AS_RELATED: "Link as related",
  KEEP_SEPARATE: "Keep separate",
  REQUEST_CLARIFICATION: "Ask the requester a question",
};

const ERROR_COPY: Record<AnalysisErrorCode, string> = {
  TIMEOUT: "The model did not respond within the time limit.",
  PROVIDER_ERROR: "The AI provider could not be reached.",
  INVALID_OUTPUT: "The model returned a response that did not match the required format.",
  REFUSAL: "The provider declined to analyse this request.",
  INTERRUPTED: "The analysis was interrupted, most likely by a server restart.",
  FABRICATED_REFERENCES:
    "The model referenced requests that could not be verified against the database, so the result was rejected rather than shown.",
  UNEXPECTED: "Something went wrong while analysing this request.",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-subtle">{title}</h3>
      <div className="text-sm leading-relaxed text-foreground">{children}</div>
    </div>
  );
}

function CandidateCard({ candidate }: { candidate: RelatedRequest }) {
  const isDuplicate = candidate.relationship_type === "DUPLICATE";
  return (
    <li className="rounded-lg border border-border-subtle bg-surface-muted p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <Link
          href={`/requests/${candidate.request_id}`}
          className="text-sm font-medium text-foreground underline-offset-4 hover:underline"
        >
          {candidate.title}
        </Link>
        <div className="flex shrink-0 items-center gap-2">
          <Badge tone={isDuplicate ? "danger" : "accent"}>
            {isDuplicate ? "Duplicate" : "Related"}
          </Badge>
          <span className="text-xs text-subtle">
            {candidate.vote_count} {candidate.vote_count === 1 ? "supporter" : "supporters"}
          </span>
        </div>
      </div>
      {/* The evidence. Without this a reviewer would be approving a guess. */}
      <p className="mt-2 text-sm leading-relaxed text-muted">{candidate.explanation}</p>
    </li>
  );
}

function CompletedAnalysis({ analysis }: { analysis: Analysis }) {
  const classification = analysis.classification
    ? CLASSIFICATION_COPY[analysis.classification]
    : null;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <h2 className="text-base font-semibold text-foreground">AI analysis</h2>
            {classification && <Badge tone="accent">{classification.label}</Badge>}
          </div>
          {classification && <p className="text-sm text-muted">{classification.detail}</p>}
        </div>
        {analysis.recommendation && (
          <div className="flex flex-col gap-0.5 sm:text-right">
            <span className="text-xs uppercase tracking-wide text-subtle">
              Suggested next step
            </span>
            <span className="text-sm font-medium text-muted">
              {RECOMMENDATION_LABELS[analysis.recommendation]}
            </span>
          </div>
        )}
      </div>

      {/* The most important sentence in the product, so it sits with the
          recommendation rather than at the bottom of the page. */}
      <p className="rounded-lg bg-accent-soft px-3 py-2 text-xs text-accent">
        This is a recommendation, not a decision. A product manager approves or
        rejects each relationship, and nothing here merges or removes a request.
      </p>

      {analysis.underlying_need && (
        <Section title="Underlying need">{analysis.underlying_need}</Section>
      )}
      {analysis.problem_summary && (
        <Section title="Problem">{analysis.problem_summary}</Section>
      )}
      {analysis.suggested_theme && (
        <Section title="Theme">
          <Badge>{analysis.suggested_theme}</Badge>
        </Section>
      )}

      <Section title={`Related requests (${analysis.related_requests.length})`}>
        {analysis.related_requests.length === 0 ? (
          <p className="text-muted">
            No existing request was found that shares this need.
          </p>
        ) : (
          <ul className="mt-1 flex flex-col gap-2">
            {analysis.related_requests.map((candidate) => (
              <CandidateCard key={candidate.request_id} candidate={candidate} />
            ))}
          </ul>
        )}
      </Section>

      {analysis.reasoning && <Section title="Reasoning">{analysis.reasoning}</Section>}

      <dl className="flex flex-wrap gap-x-6 gap-y-1 border-t border-border-subtle pt-3 text-xs text-subtle">
        {analysis.model_name && (
          <div className="flex gap-1.5">
            <dt>Model</dt>
            <dd className="font-medium text-muted">{analysis.model_name}</dd>
          </div>
        )}
        {analysis.retrieval_mode && (
          <div className="flex gap-1.5">
            <dt>Retrieval</dt>
            <dd className="font-medium text-muted">
              {analysis.retrieval_mode === "agentic"
                ? `tool calling (${analysis.tool_calls_count ?? 0} searches)`
                : "backend retrieval"}
            </dd>
          </div>
        )}
        {analysis.latency_ms !== null && (
          <div className="flex gap-1.5">
            <dt>Took</dt>
            <dd className="font-medium text-muted">
              {(analysis.latency_ms / 1000).toFixed(1)}s
            </dd>
          </div>
        )}
        {analysis.fabricated_refs_count > 0 && (
          <div className="flex gap-1.5">
            <dt>Unverifiable references discarded</dt>
            <dd className="font-medium text-danger">{analysis.fabricated_refs_count}</dd>
          </div>
        )}
      </dl>
    </div>
  );
}

export function AnalysisPanel({
  requestId,
  initial,
}: {
  requestId: string;
  initial: FeatureRequestDetail;
}) {
  const [detail, setDetail] = useState(initial);
  const [attempts, setAttempts] = useState(0);
  const [isRetrying, setIsRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);

  const isPending = detail.analysis_state === "pending";
  const gaveUp = isPending && attempts >= POLL_BUDGET;

  useEffect(() => {
    if (!isPending || attempts >= POLL_BUDGET) return;

    const timer = setTimeout(async () => {
      try {
        setDetail(await getRequest(requestId));
      } catch {
        // A failed poll is not worth surfacing; the next one may succeed and
        // the budget stops this eventually either way.
      }
      setAttempts((previous) => previous + 1);
    }, POLL_INTERVAL_MS);

    return () => clearTimeout(timer);
  }, [isPending, attempts, requestId]);

  const retry = useCallback(async () => {
    setIsRetrying(true);
    setRetryError(null);
    try {
      setDetail(await retryAnalysis(requestId));
      setAttempts(0);
    } catch (error) {
      setRetryError(
        error instanceof ApiRequestError
          ? error.message
          : "Could not start the analysis again.",
      );
    } finally {
      setIsRetrying(false);
    }
  }, [requestId]);

  const retryButton = (
    <Button variant="secondary" onClick={retry} disabled={isRetrying}>
      {isRetrying ? (
        <>
          <Spinner label="Starting analysis" />
          Starting…
        </>
      ) : (
        "Run analysis"
      )}
    </Button>
  );

  const analysis = detail.analysis;

  return (
    <Card className="flex flex-col gap-4">
      {retryError && (
        <p role="alert" className="text-sm text-danger">
          {retryError}
        </p>
      )}

      {detail.analysis_state === "completed" && analysis ? (
        <CompletedAnalysis analysis={analysis} />
      ) : isPending && !gaveUp ? (
        <div className="flex items-center gap-3 py-2">
          <Spinner label="Analysing the request" />
          <div className="flex flex-col">
            <p className="text-sm font-medium text-foreground">Analysing…</p>
            <p className="text-sm text-muted">
              Identifying the underlying need and searching existing requests.
            </p>
          </div>
        </div>
      ) : gaveUp ? (
        <ErrorPanel
          title="Still running"
          description="The analysis has not finished yet. It may still complete — reload the page, or start it again."
          action={retryButton}
        />
      ) : detail.analysis_state === "failed" && analysis?.error_code ? (
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <h2 className="text-base font-semibold text-foreground">
              Analysis did not complete
            </h2>
            <p className="text-sm text-muted">{ERROR_COPY[analysis.error_code]}</p>
            <p className="text-xs text-subtle">
              The request itself is saved and unaffected.
            </p>
          </div>
          {retryButton}
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <h2 className="text-base font-semibold text-foreground">Not analysed</h2>
            <p className="text-sm text-muted">
              This request has not been analysed yet. Analysis identifies the
              underlying need and looks for related requests.
            </p>
          </div>
          {retryButton}
        </div>
      )}
    </Card>
  );
}
