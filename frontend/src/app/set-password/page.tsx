"use client";

import { Check, KeyRound, ShieldAlert } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Form";
import { InlineError, Spinner } from "@/components/ui/Feedback";
import { cn } from "@/lib/cn";
import { api, errorMessage, setToken } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/lib/toast";

/** Mirrors the backend's rule (8..72 bytes) plus advice it does not enforce. */
function checks(password: string) {
  return [
    { label: "At least 8 characters", ok: password.length >= 8 },
    { label: "A letter and a number", ok: /[A-Za-z]/.test(password) && /\d/.test(password) },
    {
      label: "At most 72 bytes",
      ok: new TextEncoder().encode(password).length <= 72,
    },
  ];
}

export default function SetPasswordPage() {
  const { user, loading, signOut } = useAuth();
  const router = useRouter();
  const toast = useToast();

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const rules = useMemo(() => checks(next), [next]);
  const satisfied = rules.every((rule) => rule.ok);
  const matches = next.length > 0 && next === confirm;

  useEffect(() => {
    if (loading) return;
    if (!user) router.replace("/login");
    // Somebody who has already changed it has no business here.
    else if (!user.must_change_password) router.replace("/dashboard");
  }, [user, loading, router]);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);

    if (!matches) {
      setError("The two passwords do not match.");
      return;
    }

    setSubmitting(true);
    try {
      await api.auth.changePassword(current, next);
      // The backend invalidates every token issued before the change, so the
      // one we hold is already dead. Sign in again with the new password.
      setToken(null);
      toast.success("Password updated", "Sign in with your new password.");
      router.replace("/login");
      // Clears the in-memory user without a second redirect.
      window.setTimeout(() => signOut(), 0);
    } catch (cause) {
      setError(errorMessage(cause));
      setSubmitting(false);
    }
  }

  if (loading || !user) {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="flex min-h-dvh items-center justify-center px-5 py-12">
      <div className="w-full max-w-md animate-fade-up">
        <div className="mb-6 flex items-center gap-3">
          <span className="inline-flex size-11 shrink-0 items-center justify-center rounded-xl bg-warning-soft text-warning">
            <ShieldAlert className="size-5" aria-hidden />
          </span>
          <div>
            <h1 className="text-[19px] font-semibold tracking-tight text-content">
              Set your own password
            </h1>
            <p className="text-[13px] text-muted">
              Signed in as {user.name}. The rest of the portal unlocks once this is done.
            </p>
          </div>
        </div>

        <div className="rounded-card border border-line bg-surface p-5 card-shadow">
          <form onSubmit={onSubmit} className="space-y-4" noValidate>
            <Field label="Current password" htmlFor="current" required>
              <Input
                id="current"
                type="password"
                autoComplete="current-password"
                autoFocus
                required
                icon={<KeyRound className="size-4" />}
                value={current}
                onChange={(event) => setCurrent(event.target.value)}
              />
            </Field>

            <Field label="New password" htmlFor="next" required>
              <Input
                id="next"
                type="password"
                autoComplete="new-password"
                required
                value={next}
                onChange={(event) => setNext(event.target.value)}
                invalid={next.length > 0 && !satisfied}
              />
            </Field>

            <ul className="space-y-1.5">
              {rules.map((rule) => (
                <li
                  key={rule.label}
                  className={cn(
                    "flex items-center gap-2 text-[12.5px] transition-colors",
                    rule.ok ? "text-success" : "text-subtle",
                  )}
                >
                  <span
                    className={cn(
                      "inline-flex size-4 items-center justify-center rounded-full border transition-colors",
                      rule.ok ? "border-transparent bg-success text-white" : "border-line-strong",
                    )}
                    aria-hidden
                  >
                    {rule.ok ? <Check className="size-2.5" strokeWidth={3.5} /> : null}
                  </span>
                  {rule.label}
                </li>
              ))}
            </ul>

            <Field
              label="Confirm new password"
              htmlFor="confirm"
              required
              error={confirm.length > 0 && !matches ? "These do not match." : null}
            >
              <Input
                id="confirm"
                type="password"
                autoComplete="new-password"
                required
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
                invalid={confirm.length > 0 && !matches}
              />
            </Field>

            {error ? <InlineError>{error}</InlineError> : null}

            <Button
              type="submit"
              size="lg"
              className="w-full"
              loading={submitting}
              disabled={!satisfied || !matches || current.length === 0}
            >
              Update password
            </Button>
          </form>
        </div>

        <button
          type="button"
          onClick={signOut}
          className="mt-4 w-full text-center text-[13px] text-subtle transition-colors hover:text-content"
        >
          Sign out instead
        </button>
      </div>
    </div>
  );
}
