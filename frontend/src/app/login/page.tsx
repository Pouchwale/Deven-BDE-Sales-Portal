"use client";

import { ArrowRight, Eye, EyeOff, Lock, Mail, Network, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Form";
import { InlineError, Spinner } from "@/components/ui/Feedback";
import { ApiError, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const SHOW_DEMO_HINT = process.env.NEXT_PUBLIC_SHOW_DEMO_HINT === "true";
const DEMO_PASSWORD = process.env.NEXT_PUBLIC_DEMO_PASSWORD ?? "ChangeMe@123";

// Headcounts deliberately left out. This page is unauthenticated, so it has no
// way to read the real numbers, and the ones that used to be written here went
// stale the first time somebody was deactivated - the sign-in screen was still
// promising "22 people" after one of them left.
const DEMO_ACCOUNTS = [
  { email: "owner@pouchwale.com", role: "Super Admin — owns the installation" },
  { email: "shail.patel@pouchwale.com", role: "Admin — sees everyone" },
  { email: "navya.rupawat@pouchwale.com", role: "Manager — sees her own reports" },
  { email: "ramanesh.nair@pouchwale.com", role: "Manager — sees his own branch" },
  { email: "parth.fulvani@pouchwale.com", role: "BDE — sees only himself" },
];

export default function LoginPage() {
  const { user, loading, signIn } = useAuth();
  const router = useRouter();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Already signed in? Do not make them look at a sign-in form.
  useEffect(() => {
    if (!loading && user) {
      router.replace(user.must_change_password ? "/set-password" : "/dashboard");
    }
  }, [user, loading, router]);

  async function attemptSignIn(withEmail: string, withPassword: string) {
    setError(null);
    setSubmitting(true);
    try {
      const signedIn = await signIn(withEmail.trim(), withPassword);
      router.replace(signedIn.must_change_password ? "/set-password" : "/dashboard");
    } catch (cause) {
      setError(
        cause instanceof ApiError && cause.code === "RATE_LIMITED"
          ? cause.message
          : errorMessage(cause),
      );
      setSubmitting(false);
    }
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    await attemptSignIn(email, password);
  }

  /** One click straight into the portal, for the seeded shortcuts below. */
  async function signInAs(accountEmail: string) {
    setEmail(accountEmail);
    setPassword(DEMO_PASSWORD);
    await attemptSignIn(accountEmail, DEMO_PASSWORD);
  }

  if (loading || user) {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="grid min-h-dvh lg:grid-cols-[1.05fr_1fr]">
      {/* ------------------------------------------------ brand panel */}
      <aside className="relative hidden overflow-hidden bg-brand-950 lg:flex lg:flex-col lg:justify-between lg:p-12">
        <div className="absolute inset-0 auth-grid opacity-[0.07]" aria-hidden />
        <div
          className="absolute -left-24 top-1/4 size-[30rem] rounded-full bg-brand-500/25 blur-[110px]"
          aria-hidden
        />
        <div
          className="absolute -bottom-24 right-0 size-[26rem] rounded-full bg-brand-400/15 blur-[110px]"
          aria-hidden
        />

        <div className="relative flex items-center gap-3">
          <span className="inline-flex size-10 items-center justify-center rounded-xl bg-white/10 text-sm font-bold text-white backdrop-blur">
            BP
          </span>
          <div>
            <p className="text-[15px] font-semibold leading-tight text-white">BDE &amp; Sales Portal</p>
            <p className="text-[12.5px] leading-tight text-brand-200/80">Pouchwale · Mehsana</p>
          </div>
        </div>

        <div className="relative max-w-md">
          <h2 className="text-[26px] font-semibold leading-tight text-white">
            One reporting chain,
            <br />
            enforced everywhere.
          </h2>
          <p className="mt-3 text-[14px] leading-relaxed text-brand-100/75">
            Managers see their own subtree at any depth — nothing sideways, nothing above.
            Every list, every counter and every dashboard applies the same scope.
          </p>

          <ul className="mt-8 space-y-3.5">
            {[
              { icon: Network, text: "Reporting hierarchy three levels deep" },
              { icon: ShieldCheck, text: "Nobody can act on someone who outranks them" },
              { icon: Lock, text: "Every authority-gated change is audited" },
            ].map(({ icon: Icon, text }) => (
              <li key={text} className="flex items-center gap-3 text-[13.5px] text-brand-100/85">
                <span className="inline-flex size-7 shrink-0 items-center justify-center rounded-lg bg-white/10 backdrop-blur">
                  <Icon className="size-3.5 text-brand-100" aria-hidden />
                </span>
                {text}
              </li>
            ))}
          </ul>
        </div>

        {/* No counts here. A hardcoded "17 accounts" on the sign-in page is a
            number nobody maintains, and it contradicted every screen behind
            the login the moment the lead book grew. */}
        <p className="relative text-[12px] text-brand-200/50">
          Leads, references and customer feedback in one place.
        </p>
      </aside>

      {/* ------------------------------------------------------- form */}
      <main className="flex items-center justify-center px-5 py-12 sm:px-8">
        <div className="w-full max-w-sm animate-fade-up">
          <div className="mb-8 lg:hidden">
            <span className="inline-flex size-11 items-center justify-center rounded-xl bg-brand-600 text-sm font-bold text-white">
              BP
            </span>
          </div>

          <h1 className="text-[22px] font-semibold tracking-tight text-content">Welcome back</h1>
          <p className="mt-1.5 text-[13.5px] text-muted">
            Sign in to the BDE &amp; Sales Portal.
          </p>

          <form onSubmit={onSubmit} className="mt-7 space-y-4" noValidate>
            <Field label="Email address" htmlFor="email" required>
              <Input
                id="email"
                name="email"
                type="email"
                autoComplete="username"
                autoFocus
                required
                placeholder="you@pouchwale.com"
                icon={<Mail className="size-4" />}
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                invalid={Boolean(error)}
              />
            </Field>

            <Field label="Password" htmlFor="password" required>
              <div className="relative">
                <Input
                  id="password"
                  name="password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  placeholder="Your password"
                  icon={<Lock className="size-4" />}
                  className="pr-10"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  invalid={Boolean(error)}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((shown) => !shown)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded-md p-1 text-subtle transition-colors hover:text-content"
                >
                  {showPassword ? (
                    <EyeOff className="size-4" aria-hidden />
                  ) : (
                    <Eye className="size-4" aria-hidden />
                  )}
                </button>
              </div>
            </Field>

            {error ? <InlineError>{error}</InlineError> : null}

            <Button type="submit" size="lg" className="w-full" loading={submitting}>
              {submitting ? "Signing in…" : "Sign in"}
              {submitting ? null : <ArrowRight className="size-4" aria-hidden />}
            </Button>
          </form>

          {SHOW_DEMO_HINT ? (
            <div className="mt-8 rounded-xl border border-line bg-surface-2 p-3.5">
              <p className="text-[11.5px] font-semibold uppercase tracking-wider text-subtle">
                Sign in as
              </p>
              <ul className="mt-2 space-y-1">
                {DEMO_ACCOUNTS.map((account) => (
                  <li key={account.email}>
                    {/* One click signs straight in — no typing, no second step. */}
                    <button
                      type="button"
                      disabled={submitting}
                      onClick={() => void signInAs(account.email)}
                      data-testid="quick-signin"
                      data-email={account.email}
                      className="group flex w-full items-center justify-between gap-3 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-surface-hover disabled:opacity-60"
                    >
                      <span className="min-w-0">
                        <span className="block truncate font-mono text-[12px] text-content">
                          {account.email}
                        </span>
                        <span className="block truncate text-[11.5px] text-subtle">
                          {account.role}
                        </span>
                      </span>
                      <ArrowRight
                        className="size-3.5 shrink-0 text-subtle opacity-0 transition-opacity group-hover:opacity-100"
                        aria-hidden
                      />
                    </button>
                  </li>
                ))}
              </ul>
              <p className="mt-2 px-2 text-[11.5px] text-subtle">
                All seeded accounts share the password{" "}
                <span className="font-mono text-muted">{DEMO_PASSWORD}</span>.
              </p>
            </div>
          ) : null}
        </div>
      </main>
    </div>
  );
}
