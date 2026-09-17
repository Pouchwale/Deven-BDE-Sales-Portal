"use client";

import { ArrowRight, Lock, Network, ShieldCheck, UserRound } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { BrandLogo } from "@/components/layout/BrandLogo";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Form";
import { InlineError, Spinner } from "@/components/ui/Feedback";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

/** Shown for every failed sign-in. The server already refuses to say whether
 *  an account exists; the page must not leak it through its wording either. */
const GENERIC_FAILURE = "Incorrect username or password.";

function signInErrorMessage(cause: unknown): string {
  if (cause instanceof ApiError) {
    if (cause.code === "RATE_LIMITED") return "Too many sign-in attempts. Try again later.";
    if (cause.code === "NETWORK_ERROR") return cause.message;
    if (cause.status === 401 || cause.status === 422) return GENERIC_FAILURE;
  }
  return "Sign-in failed. Please try again.";
}

export default function LoginPage() {
  const { user, loading, signIn } = useAuth();
  const router = useRouter();

  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Already signed in? Do not make them look at a sign-in form.
  useEffect(() => {
    if (!loading && user) {
      router.replace(user.must_change_password ? "/set-password" : "/dashboard");
    }
  }, [user, loading, router]);

  async function attemptSignIn(withIdentifier: string, withPassword: string) {
    setError(null);
    setSubmitting(true);
    try {
      const signedIn = await signIn(withIdentifier.trim(), withPassword);
      router.replace(signedIn.must_change_password ? "/set-password" : "/dashboard");
    } catch (cause) {
      setError(signInErrorMessage(cause));
      setSubmitting(false);
    }
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    await attemptSignIn(identifier, password);
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
          <BrandLogo size={44} className="ring-2 ring-white/15" />
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
            <BrandLogo size={48} />
          </div>

          <h1 className="text-[22px] font-semibold tracking-tight text-content">Welcome back</h1>
          <p className="mt-1.5 text-[13.5px] text-muted">
            Sign in to the BDE &amp; Sales Portal.
          </p>

          <form onSubmit={onSubmit} className="mt-7 space-y-4" noValidate>
            <Field label="Username or email" htmlFor="identifier" required>
              <Input
                id="identifier"
                name="username"
                type="text"
                autoComplete="username"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                autoFocus
                required
                placeholder="Enter your name here"
                icon={<UserRound className="size-4" />}
                value={identifier}
                onChange={(event) => setIdentifier(event.target.value)}
                invalid={Boolean(error)}
              />
            </Field>

            <Field label="Password" htmlFor="password" required>
              {/* No show-password toggle, by policy: a password is never
                  rendered as plain text on any screen. */}
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
                placeholder="Your password"
                icon={<Lock className="size-4" />}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                invalid={Boolean(error)}
              />
            </Field>

            {error ? <InlineError>{error}</InlineError> : null}

            <Button type="submit" size="lg" className="w-full" loading={submitting}>
              {submitting ? "Signing in…" : "Sign in"}
              {submitting ? null : <ArrowRight className="size-4" aria-hidden />}
            </Button>
          </form>
        </div>
      </main>
    </div>
  );
}
