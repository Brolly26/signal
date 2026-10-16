import { Suspense } from "react";

import { ButtonLink } from "@/components/ui";
import { RequestBoard, RequestBoardSkeleton } from "@/features/requests/RequestList";

export default function RequestBoardPage({ searchParams }: PageProps<"/">) {
  return (
    <div className="flex flex-col gap-7">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            Request board
          </h1>
          <p className="max-w-xl text-sm text-muted">
            Every feature request submitted by customers and stakeholders.
            Support count is a demand signal, not a priority.
          </p>
        </div>
        <ButtonLink href="/requests/new">New request</ButtonLink>
      </div>

      {/* The board reads uncached data and the search parameters, so it streams
          in behind this boundary while the shell above is prerendered. */}
      <Suspense fallback={<RequestBoardSkeleton />}>
        <RequestBoard searchParams={searchParams} />
      </Suspense>
    </div>
  );
}
