"use client";

/**
 * Support toggle.
 *
 * The count rendered by the server is the starting point; every API response
 * replaces it with the authoritative value, so a double click, a second tab or
 * cleared browser storage all converge instead of drifting.
 *
 * `supported` starts as `null` (not yet known) and is filled in after mount,
 * because the remembered state lives in localStorage and the server cannot see
 * it. Rendering `null` exactly like `false` keeps the first client render
 * identical to the server's.
 */

import { useEffect, useState } from "react";

import { ApiRequestError, supportRequest, withdrawSupport } from "@/lib/api";
import { getVoterKey, hasSupportedLocally, rememberSupport } from "@/lib/voter";

export function VoteButton({
  requestId,
  initialCount,
}: {
  requestId: string;
  initialCount: number;
}) {
  const [count, setCount] = useState(initialCount);
  const [supported, setSupported] = useState<boolean | null>(null);
  const [isPending, setIsPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setSupported(hasSupportedLocally(requestId));
  }, [requestId]);

  async function toggle() {
    setIsPending(true);
    setError(null);
    const wasSupported = supported === true;

    try {
      const result = wasSupported
        ? await withdrawSupport(requestId, getVoterKey())
        : await supportRequest(requestId, getVoterKey());

      setCount(result.vote_count);
      setSupported(result.has_voted);
      rememberSupport(requestId, result.has_voted);
    } catch (caught) {
      setError(
        caught instanceof ApiRequestError && caught.code === "network_error"
          ? "Offline"
          : "Failed",
      );
    } finally {
      setIsPending(false);
    }
  }

  const isSupported = supported === true;

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={toggle}
        disabled={isPending}
        aria-pressed={isSupported}
        aria-label={
          isSupported
            ? `Withdraw support. ${count} supporters`
            : `Support this request. ${count} supporters`
        }
        className={[
          "inline-flex min-w-18 items-center justify-center gap-1.5 rounded-lg border",
          "px-3 py-1.5 text-sm font-medium transition-colors",
          "disabled:cursor-not-allowed disabled:opacity-60",
          isSupported
            ? "border-accent bg-accent-soft text-accent"
            : "border-border-strong bg-surface text-muted hover:border-subtle hover:text-foreground",
        ].join(" ")}
      >
        <span aria-hidden="true">{isSupported ? "▲" : "△"}</span>
        <span className="tabular-nums">{count}</span>
      </button>
      {error && (
        <span role="alert" className="text-xs text-danger">
          {error}
        </span>
      )}
    </div>
  );
}
