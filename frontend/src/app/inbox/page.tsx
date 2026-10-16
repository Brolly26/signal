import { Suspense } from "react";

import { Card } from "@/components/ui";
import { DecisionLog, InboxQueue, InboxSkeleton } from "@/features/review/Inbox";

export const metadata = {
  title: "Decision inbox — Signal",
};

export default function InboxPage() {
  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-1.5">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">
          Decision inbox
        </h1>
        <p className="max-w-2xl text-sm text-muted">
          Relationships the AI proposed, waiting for a product manager to approve
          or reject. Approving records a link between two requests — it never
          merges or deletes either of them.
        </p>
      </div>

      <Suspense fallback={<InboxSkeleton />}>
        <InboxQueue />
      </Suspense>

      <section className="flex flex-col gap-3" aria-label="Decision history">
        <div className="flex flex-col gap-1">
          <h2 className="text-lg font-semibold tracking-tight text-foreground">
            Decision history
          </h2>
          <p className="text-sm text-muted">
            Append-only. Changing a decision adds an entry rather than replacing
            one, so the record of what was decided and why stays intact.
          </p>
        </div>
        <Card>
          <Suspense
            fallback={
              <div className="h-16 animate-pulse rounded bg-surface-muted" aria-busy="true" />
            }
          >
            <DecisionLog />
          </Suspense>
        </Card>
      </section>
    </div>
  );
}
