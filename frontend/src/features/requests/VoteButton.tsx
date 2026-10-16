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
 *
 * None of the voting logic changed when this control was redesigned: the same
 * endpoints, the same authoritative count, the same localStorage cache, and
 * duplicate prevention still enforced by the database.
 */

import { useEffect, useState } from "react";

import { ApiRequestError, supportRequest, withdrawSupport } from "@/lib/api";
import { getVoterKey, hasSupportedLocally, rememberSupport } from "@/lib/voter";

function ArrowIcon({ filled }: { filled: boolean }) {
  return (
    <svg
      viewBox="0 0 16 16"
      aria-hidden="true"
      className="size-4 shrink-0"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth={filled ? 0 : 1.6}
      strokeLinejoin="round"
    >
      <path d="M8 2.6 14.2 12.4a.9.9 0 0 1-.76 1.38H2.56a.9.9 0 0 1-.76-1.38Z" />
    </svg>
  );
}

function SpinnerIcon() {
  return (
    <span
      aria-hidden="true"
      className="size-4 shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent"
    />
  );
}

export function VoteButton({
  requestId,
  initialCount,
}: {
  requestId: string;
  initialCount: number;
}) {
  const [count, setCount] = useState(initialCount);
  const [serverCount, setServerCount] = useState(initialCount);
  const [supported, setSupported] = useState<boolean | null>(null);
  const [isPending, setIsPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // When the server re-renders this card with a different count — a sort
  // change, a search, a reload — that value is newer than ours, so adopt it.
  if (initialCount !== serverCount) {
    setServerCount(initialCount);
    setCount(initialCount);
  }

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
          ? "Offline — try again"
          : "Could not save",
      );
    } finally {
      setIsPending(false);
    }
  }

  const isSupported = supported === true;

  return (
    <div className="flex flex-col items-start gap-1">
      <button
        type="button"
        onClick={toggle}
        disabled={isPending}
        aria-pressed={isSupported}
        aria-label={
          isSupported
            ? `Withdraw your support. ${count} ${count === 1 ? "person supports" : "people support"} this request`
            : `Support this request. ${count} ${count === 1 ? "person supports" : "people support"} it`
        }
        className={[
          "group inline-flex items-center gap-2 rounded-full border px-3 py-1.5",
          "text-sm font-medium transition-colors",
          "disabled:cursor-not-allowed disabled:opacity-70",
          isSupported
            ? "border-accent bg-accent text-white hover:bg-accent-hover"
            : "border-border-strong bg-surface text-foreground hover:border-accent hover:bg-accent-soft hover:text-accent",
        ].join(" ")}
      >
        {isPending ? <SpinnerIcon /> : <ArrowIcon filled={isSupported} />}
        {/* The word is what makes the control legible; it is hidden on the
            narrowest screens, where the icon and count still carry it and the
            aria-label keeps it announced. */}
        <span className="hidden sm:inline">
          {isSupported ? "Supported" : "Support"}
        </span>
        <span
          className={[
            "tabular-nums rounded-full px-1.5 py-0.5 text-xs font-semibold",
            isSupported ? "bg-white/20 text-white" : "bg-surface-muted text-muted",
          ].join(" ")}
        >
          {count}
        </span>
      </button>

      {error && (
        <span role="alert" className="text-xs text-danger">
          {error}
        </span>
      )}
    </div>
  );
}
