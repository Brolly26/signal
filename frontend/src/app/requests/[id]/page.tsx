import { Suspense } from "react";
import Link from "next/link";
import { notFound } from "next/navigation";

import { Badge, Card, ErrorPanel } from "@/components/ui";
import { AnalysisPanel } from "@/features/analysis/AnalysisPanel";
import { VoteButton } from "@/features/requests/VoteButton";
import { ApiRequestError, formatDate, getRequest } from "@/lib/api";

const STATUS_LABELS: Record<string, string> = {
  open: "Open",
  triaged: "Triaged",
  planned: "Planned",
  declined: "Declined",
};

async function RequestDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let detail;
  try {
    detail = await getRequest(id);
  } catch (error) {
    if (!(error instanceof ApiRequestError)) throw error;
    if (error.status === 404) notFound();

    return (
      <ErrorPanel
        title={error.code === "network_error" ? "Cannot reach the API" : "Could not load this request"}
        description={
          error.code === "network_error"
            ? "The Signal API is not responding. Start the backend and reload this page."
            : error.message
        }
      />
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <Card className="flex gap-4">
        <div className="shrink-0 pt-0.5">
          <VoteButton requestId={detail.id} initialCount={detail.vote_count} />
        </div>
        <div className="flex min-w-0 flex-col gap-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <h1 className="text-xl font-semibold leading-snug tracking-tight text-foreground">
              {detail.title}
            </h1>
            <Badge tone={detail.status === "open" ? "accent" : "neutral"}>
              {STATUS_LABELS[detail.status] ?? detail.status}
            </Badge>
          </div>
          <p className="whitespace-pre-line text-sm leading-relaxed text-muted">
            {detail.description}
          </p>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-subtle">
            <span>{detail.requester_label}</span>
            <span aria-hidden="true">·</span>
            <time dateTime={detail.created_at}>{formatDate(detail.created_at)}</time>
          </div>
        </div>
      </Card>

      {detail.links.length > 0 && (
        <Card className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <h2 className="text-base font-semibold text-foreground">
              Approved relationships
            </h2>
            <p className="text-sm text-muted">
              Confirmed by a product manager. Both requests remain separate and
              intact.
            </p>
          </div>
          <ul className="flex flex-col gap-2">
            {detail.links.map((link) => (
              <li
                key={link.linked_request_id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border-subtle bg-surface-muted px-4 py-3"
              >
                <Link
                  href={`/requests/${link.linked_request_id}`}
                  className="text-sm font-medium text-foreground underline-offset-4 hover:underline"
                >
                  {link.linked_request_title}
                </Link>
                <div className="flex items-center gap-2">
                  <Badge tone={link.link_type === "DUPLICATE" ? "danger" : "accent"}>
                    {link.link_type === "DUPLICATE" ? "Duplicate" : "Related"}
                  </Badge>
                  <span className="text-xs text-subtle">{formatDate(link.created_at)}</span>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <AnalysisPanel requestId={detail.id} initial={detail} />
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div className="flex flex-col gap-5" aria-busy="true">
      <span className="sr-only">Loading request…</span>
      {[0, 1].map((index) => (
        <Card key={index} className="flex flex-col gap-3">
          <div className="h-6 w-1/2 animate-pulse rounded bg-surface-muted" />
          <div className="h-4 w-full animate-pulse rounded bg-surface-muted" />
          <div className="h-4 w-2/3 animate-pulse rounded bg-surface-muted" />
        </Card>
      ))}
    </div>
  );
}

export default function RequestDetailPage({ params }: PageProps<"/requests/[id]">) {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <nav aria-label="Breadcrumb">
        <Link href="/" className="text-sm text-muted transition-colors hover:text-foreground">
          ← Back to the board
        </Link>
      </nav>

      {/* Uncached read plus route params: streamed behind the boundary. */}
      <Suspense fallback={<DetailSkeleton />}>
        <RequestDetail params={params} />
      </Suspense>
    </div>
  );
}
