import Link from "next/link";

import { NewRequestForm } from "@/features/requests/NewRequestForm";

export const metadata = {
  title: "New request — Signal",
};

export default function NewRequestPage() {
  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-7">
      <nav aria-label="Breadcrumb">
        <Link
          href="/"
          className="text-sm text-muted transition-colors hover:text-foreground"
        >
          ← Back to the board
        </Link>
      </nav>

      <div className="flex flex-col gap-1.5">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">
          Submit a feature request
        </h1>
        <p className="text-sm text-muted">
          Describe the problem in your own words. The clearer the problem, the
          better a product manager can judge what to build.
        </p>
      </div>

      <NewRequestForm />
    </div>
  );
}
