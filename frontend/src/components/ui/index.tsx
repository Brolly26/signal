/**
 * Small shared UI primitives.
 *
 * Kept in one file on purpose: these are a handful of styled elements, and
 * splitting them across eight modules would be structure without benefit.
 */

import type { ComponentProps, ReactNode } from "react";
import Link from "next/link";

function cx(...values: Array<string | false | undefined>): string {
  return values.filter(Boolean).join(" ");
}

/* ------------------------------------------------------------------ button ---- */

type ButtonVariant = "primary" | "secondary";

const BUTTON_BASE =
  "inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-sm " +
  "font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60";

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-accent text-white hover:bg-accent-hover",
  secondary:
    "border border-border-strong bg-surface text-foreground hover:bg-surface-muted",
};

export function Button({
  variant = "primary",
  className,
  ...props
}: ComponentProps<"button"> & { variant?: ButtonVariant }) {
  return (
    <button className={cx(BUTTON_BASE, BUTTON_VARIANTS[variant], className)} {...props} />
  );
}

export function ButtonLink({
  variant = "primary",
  className,
  ...props
}: ComponentProps<typeof Link> & { variant?: ButtonVariant }) {
  return (
    <Link className={cx(BUTTON_BASE, BUTTON_VARIANTS[variant], className)} {...props} />
  );
}

/* -------------------------------------------------------------------- card ---- */

export function Card({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cx(
        "rounded-xl border border-border-subtle bg-surface p-5 sm:p-6",
        className,
      )}
      {...props}
    />
  );
}

/* ------------------------------------------------------------------- badge ---- */

type BadgeTone = "neutral" | "accent" | "success" | "danger";

const BADGE_TONES: Record<BadgeTone, string> = {
  neutral: "bg-surface-muted text-muted",
  accent: "bg-accent-soft text-accent",
  success: "bg-success-soft text-success",
  danger: "bg-danger-soft text-danger",
};

export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: BadgeTone;
  children: ReactNode;
}) {
  return (
    <span
      className={cx(
        "inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium",
        BADGE_TONES[tone],
      )}
    >
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ fields ---- */

const FIELD_BASE =
  "w-full rounded-lg border bg-surface px-3.5 py-2.5 text-sm text-foreground " +
  "placeholder:text-subtle transition-colors";

function fieldClasses(hasError: boolean, className?: string): string {
  return cx(
    FIELD_BASE,
    hasError ? "border-danger" : "border-border-strong hover:border-subtle",
    className,
  );
}

interface FieldProps {
  label: string;
  hint?: string;
  error?: string;
}

function FieldShell({
  id,
  label,
  hint,
  error,
  children,
}: FieldProps & { id: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium text-foreground">
        {label}
      </label>
      {children}
      {error ? (
        <p id={`${id}-error`} role="alert" className="text-sm text-danger">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-sm text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function TextField({
  label,
  hint,
  error,
  id,
  className,
  ...props
}: ComponentProps<"input"> & FieldProps & { id: string }) {
  return (
    <FieldShell id={id} label={label} hint={hint} error={error}>
      <input
        id={id}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
        className={fieldClasses(Boolean(error), className)}
        {...props}
      />
    </FieldShell>
  );
}

export function TextAreaField({
  label,
  hint,
  error,
  id,
  className,
  ...props
}: ComponentProps<"textarea"> & FieldProps & { id: string }) {
  return (
    <FieldShell id={id} label={label} hint={hint} error={error}>
      <textarea
        id={id}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
        className={fieldClasses(Boolean(error), cx("resize-y", className))}
        {...props}
      />
    </FieldShell>
  );
}

/* ------------------------------------------------------------------ states ---- */

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <Card className="flex flex-col items-center gap-3 border-dashed py-14 text-center">
      <h2 className="text-base font-semibold text-foreground">{title}</h2>
      <p className="max-w-md text-sm text-muted">{description}</p>
      {action}
    </Card>
  );
}

export function ErrorPanel({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <Card className="border-danger/40 bg-danger-soft">
      <div className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold text-danger">{title}</h2>
        <p className="text-sm text-foreground/80">{description}</p>
        {action}
      </div>
    </Card>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span
        aria-hidden="true"
        className="size-4 animate-spin rounded-full border-2 border-current border-t-transparent"
      />
      <span className="sr-only">{label}</span>
    </span>
  );
}
