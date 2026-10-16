/**
 * Search and ordering controls.
 *
 * A plain GET form and two links, with no client JavaScript: the URL is the
 * state, so a search can be bookmarked, shared and used with the back button,
 * and it still works before hydration.
 */

import Link from "next/link";

import type { RequestSort } from "@/lib/api";

const SORT_OPTIONS: Array<{ value: RequestSort; label: string }> = [
  { value: "recent", label: "Newest" },
  { value: "top", label: "Most supported" },
];

export function BoardControls({
  query,
  sort,
}: {
  query: string | null;
  sort: RequestSort;
}) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <form action="/" method="get" role="search" className="flex items-center gap-2">
        <label htmlFor="board-search" className="sr-only">
          Search requests
        </label>
        <input
          id="board-search"
          type="search"
          name="q"
          defaultValue={query ?? ""}
          placeholder="Search requests…"
          maxLength={200}
          className="w-full rounded-lg border border-border-strong bg-surface px-3.5 py-2 text-sm text-foreground placeholder:text-subtle hover:border-subtle sm:w-72"
        />
        {/* Keeps the chosen ordering when a search is submitted. */}
        {sort !== "recent" && <input type="hidden" name="sort" value={sort} />}
        <button
          type="submit"
          className="rounded-lg border border-border-strong bg-surface px-3.5 py-2 text-sm font-medium text-foreground transition-colors hover:bg-surface-muted"
        >
          Search
        </button>
        {query && (
          <Link
            href={sort === "recent" ? "/" : { pathname: "/", query: { sort } }}
            className="px-1 text-sm text-muted transition-colors hover:text-foreground"
          >
            Clear
          </Link>
        )}
      </form>

      <div
        className="inline-flex items-center gap-1 rounded-lg border border-border-subtle bg-surface-muted p-1"
        role="group"
        aria-label="Sort requests"
      >
        {SORT_OPTIONS.map((option) => {
          const isActive = option.value === sort;
          return (
            <Link
              key={option.value}
              href={{
                pathname: "/",
                query: {
                  ...(query ? { q: query } : {}),
                  ...(option.value === "recent" ? {} : { sort: option.value }),
                },
              }}
              aria-current={isActive ? "true" : undefined}
              className={[
                "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                isActive
                  ? "bg-surface text-foreground shadow-sm"
                  : "text-muted hover:text-foreground",
              ].join(" ")}
            >
              {option.label}
            </Link>
          );
        })}
      </div>
    </div>
  );
}

export function BoardControlsSkeleton() {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="h-10 w-full animate-pulse rounded-lg bg-surface-muted sm:w-72" />
      <div className="h-10 w-48 animate-pulse rounded-lg bg-surface-muted" />
    </div>
  );
}
