import { Suspense } from "react";

import { ButtonLink } from "@/components/ui";
import { RequestList, RequestListSkeleton } from "@/features/requests/RequestList";

export default function RequestBoardPage() {
  return (
    <div className="flex flex-col gap-7">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            Request board
          </h1>
          <p className="max-w-xl text-sm text-muted">
            Every feature request submitted by customers and stakeholders, newest
            first.
          </p>
        </div>
        <ButtonLink href="/requests/new">New request</ButtonLink>
      </div>

      {/* The list reads uncached data, so it streams in behind this boundary. */}
      <Suspense fallback={<RequestListSkeleton />}>
        <RequestList />
      </Suspense>
    </div>
  );
}
