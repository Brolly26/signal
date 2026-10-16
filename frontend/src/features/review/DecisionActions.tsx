"use client";

/**
 * Approve / reject one proposed relationship, or dismiss the analysis.
 *
 * The decision is the point of the product: the model proposed this
 * relationship and a person decides whether it holds. Approving records a link
 * between the two requests; it never merges or removes either of them, and the
 * reason is kept with the decision so the next reviewer can see why.
 */

import { useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui";
import { ApiRequestError, submitDecision } from "@/lib/api";
import type { Decision, InboxCandidate } from "@/lib/api";

type Pending = Decision | null;

export function CandidateDecision({
  analysisId,
  candidate,
}: {
  analysisId: string;
  candidate: InboxCandidate;
}) {
  const router = useRouter();
  const [pending, setPending] = useState<Pending>(null);
  const [error, setError] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [showReason, setShowReason] = useState(false);
  const [decision, setDecision] = useState<Decision | null>(candidate.decision);
  const [serverDecision, setServerDecision] = useState(candidate.decision);

  // When the server re-renders this candidate with a different decision — a
  // refresh, or another reviewer acting — that value is newer than ours.
  // Without this the component keeps its first prop for as long as React
  // reuses it, exactly as VoteButton did before the Phase 2 review.
  if (candidate.decision !== serverDecision) {
    setServerDecision(candidate.decision);
    setDecision(candidate.decision);
  }

  async function decide(next: Exclude<Decision, "DISMISSED">) {
    setPending(next);
    setError(null);
    try {
      await submitDecision(analysisId, {
        decision: next,
        related_request_id: candidate.request_id,
        ...(reason.trim() ? { reason } : {}),
      });
      setDecision(next);
      setShowReason(false);
      setReason("");
      // The board, the inbox count and the links all derive from this.
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ApiRequestError
          ? caught.message
          : "Could not record the decision.",
      );
    } finally {
      setPending(null);
    }
  }

  const isBusy = pending !== null;

  return (
    <div className="flex flex-col gap-2">
      {decision && (
        <p
          className={
            decision === "APPROVED"
              ? "text-xs font-medium text-success"
              : "text-xs font-medium text-muted"
          }
        >
          {decision === "APPROVED" ? "Approved" : "Rejected"}
          {" — you can change this; the history keeps both."}
        </p>
      )}

      {showReason && (
        <label className="flex flex-col gap-1 text-xs text-muted">
          Reason (optional, kept with the decision)
          <input
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            maxLength={2000}
            placeholder="Different enough to ship separately"
            className="rounded-lg border border-border-strong bg-surface px-3 py-1.5 text-sm text-foreground placeholder:text-subtle"
          />
        </label>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          onClick={() => decide("APPROVED")}
          disabled={isBusy}
          className="px-3 py-1.5 text-xs"
        >
          {pending === "APPROVED" ? "Approving…" : "Approve"}
        </Button>
        <Button
          variant="secondary"
          onClick={() => decide("REJECTED")}
          disabled={isBusy}
          className="px-3 py-1.5 text-xs"
        >
          {pending === "REJECTED" ? "Rejecting…" : "Reject"}
        </Button>
        {!showReason && (
          <button
            type="button"
            onClick={() => setShowReason(true)}
            className="text-xs text-muted underline-offset-4 hover:text-foreground hover:underline"
          >
            Add a reason
          </button>
        )}
      </div>

      {error && (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  );
}

export function DismissAnalysis({ analysisId }: { analysisId: string }) {
  const router = useRouter();
  const [isPending, setIsPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function dismiss() {
    setIsPending(true);
    setError(null);
    try {
      await submitDecision(analysisId, { decision: "DISMISSED" });
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ApiRequestError ? caught.message : "Could not dismiss.",
      );
    } finally {
      setIsPending(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <Button
        variant="secondary"
        onClick={dismiss}
        disabled={isPending}
        className="px-3 py-1.5 text-xs"
      >
        {isPending ? "Dismissing…" : "Dismiss"}
      </Button>
      {error && (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
