"use client";

/**
 * Submission form.
 *
 * Client-side on purpose: the browser calls the FastAPI service directly, which
 * is the boundary documented in the README. Field errors come from the API's
 * 422 response rather than being re-implemented here, so the backend stays the
 * single source of truth for what is valid.
 */

import { useState } from "react";
import { useRouter } from "next/navigation";

import {
  Button,
  ButtonLink,
  Card,
  ErrorPanel,
  Spinner,
  TextAreaField,
  TextField,
} from "@/components/ui";
import { ApiRequestError, createRequest } from "@/lib/api";
import type { FeatureRequest } from "@/lib/api";

const TITLE_MAX = 140;
const DESCRIPTION_MAX = 4000;

type FormState =
  | { phase: "editing" }
  | { phase: "submitting" }
  | { phase: "failed"; message: string }
  | { phase: "succeeded"; request: FeatureRequest };

export function NewRequestForm() {
  const router = useRouter();

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [requesterLabel, setRequesterLabel] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [state, setState] = useState<FormState>({ phase: "editing" });

  const isSubmitting = state.phase === "submitting";

  function resetForm() {
    setTitle("");
    setDescription("");
    setFieldErrors({});
    setState({ phase: "editing" });
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFieldErrors({});
    setState({ phase: "submitting" });

    try {
      const created = await createRequest({
        title,
        description,
        ...(requesterLabel.trim() ? { requester_label: requesterLabel } : {}),
      });
      // Invalidate the board so the new request is there when the user navigates.
      router.refresh();
      setState({ phase: "succeeded", request: created });
    } catch (error) {
      if (error instanceof ApiRequestError) {
        setFieldErrors(error.fields);
        setState({
          phase: "failed",
          message:
            error.code === "network_error"
              ? "Could not reach the Signal API. Is the backend running on port 8000?"
              : error.message,
        });
        return;
      }
      setState({ phase: "failed", message: "Something went wrong. Please try again." });
    }
  }

  if (state.phase === "succeeded") {
    return (
      <Card className="flex flex-col gap-4 border-success/40 bg-success-soft">
        <div className="flex flex-col gap-1.5">
          <h2 className="text-base font-semibold text-success">Request submitted</h2>
          <p className="text-sm text-foreground/80">
            “{state.request.title}” is saved and visible on the board.
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <ButtonLink href="/">Back to the board</ButtonLink>
          <Button variant="secondary" type="button" onClick={resetForm}>
            Submit another
          </Button>
        </div>
      </Card>
    );
  }

  return (
    <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-6">
      {state.phase === "failed" && (
        <ErrorPanel
          title="Could not submit the request"
          description={state.message}
        />
      )}

      <Card className="flex flex-col gap-5">
        <TextField
          id="title"
          label="Title"
          name="title"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="Export sales data to Excel"
          maxLength={TITLE_MAX}
          disabled={isSubmitting}
          error={fieldErrors.title}
          hint={`A short summary. ${title.length}/${TITLE_MAX} characters.`}
          autoComplete="off"
        />

        <TextAreaField
          id="description"
          label="Description"
          name="description"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          placeholder="Describe the problem you are trying to solve, not only the feature you have in mind. What do you do today, and what goes wrong?"
          rows={7}
          maxLength={DESCRIPTION_MAX}
          disabled={isSubmitting}
          error={fieldErrors.description}
          hint={`At least 20 characters. ${description.length}/${DESCRIPTION_MAX}.`}
        />

        <TextField
          id="requester_label"
          label="Your name (optional)"
          name="requester_label"
          value={requesterLabel}
          onChange={(event) => setRequesterLabel(event.target.value)}
          placeholder="anonymous"
          maxLength={60}
          disabled={isSubmitting}
          error={fieldErrors.requester_label}
          hint="A display label for this demo. It is not an authenticated identity."
          autoComplete="off"
        />
      </Card>

      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={isSubmitting}>
          {isSubmitting ? (
            <>
              <Spinner label="Submitting request" />
              Submitting…
            </>
          ) : (
            "Submit request"
          )}
        </Button>
        <ButtonLink href="/" variant="secondary">
          Cancel
        </ButtonLink>
      </div>
    </form>
  );
}
