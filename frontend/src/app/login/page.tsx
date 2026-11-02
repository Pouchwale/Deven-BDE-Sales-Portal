"use client";

import { ArrowRight, Eye, EyeOff, Lock, UserRound } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { FloatingPouches } from "@/components/auth/FloatingPouches";
import { PouchSpinner } from "@/components/auth/PouchSpinner";
import { usePouchMagnet } from "@/components/auth/usePouchMagnet";
import { BrandLogo } from "@/components/layout/BrandLogo";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Form";
import { InlineError, Spinner } from "@/components/ui/Feedback";
import { ApiError, SERVER_UNAVAILABLE } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useAuth } from "@/lib/auth";

/** Shown for every failed sign-in. The server already refuses to say whether
 *  an account exists; the page must not leak it through its wording either. */
const GENERIC_FAILURE = "Incorrect username or password.";

function signInErrorMessage(cause: unknown): string {
  if (cause instanceof ApiError) {
    // Wrong credentials: one message for every case (unknown account, wrong
    // password, malformed body), so the form never reveals whether an account
    // exists. This is the ONLY branch that is a verdict on what was typed.
    if (cause.status === 401 || cause.status === 422) return GENERIC_FAILURE;
    // The app's OWN sign-in limiter: too many attempts for this account/IP.
    if (cause.code === "RATE_LIMITED") return "Too many sign-in attempts. Try again later.";
    // A 429 WITHOUT that code is the platform throttling a hibernating or busy
    // backend (Render answers "hibernate-rate-limited") - a service state, not
    // a bad password. Retyping the password will not help; waiting will.
    if (cause.status === 429) {
      return "The server is waking up or busy (HTTP 429). Wait a minute, then try again.";
    }
    // The request never reached a running backend.
    if (cause.code === "NETWORK_ERROR" || cause.status === 0) {
      return "Cannot reach the server. Check your connection and try again.";
    }
    // Gateway/unavailable while the instance starts or restarts.
    if (cause.code === SERVER_UNAVAILABLE || [502, 503, 504].includes(cause.status)) {
      return "The server is starting up or temporarily unavailable. Try again in a minute.";
    }
    // A genuine server-side error. Report the status without any detail.
    if (cause.status >= 500) {
      return `The server hit an error (HTTP ${cause.status}). Try again shortly; if it keeps happening, contact your administrator.`;
    }
  }
  return "Sign-in failed. Please try again.";
}

/** The backend did not answer with a real verdict: asleep, waking, restarting
 *  or unreachable. Not about the credentials, so the sign-in loop waits and
 *  retries. A real server error carries the portal's own error code; a gateway
 *  failure, a dead socket or a platform throttle does not. */
function serverUnavailable(cause: ApiError): boolean {
  if (cause.code === "NETWORK_ERROR" || cause.code === SERVER_UNAVAILABLE) return true;
  if ([502, 503, 504].includes(cause.status)) return true;
  // A hibernating/throttled instance (Render's "hibernate-rate-limited")
  // answers 429 with no app error code, and wakes within about a minute - so
  // retry it. The app's own limiter uses RATE_LIMITED and must NOT be retried:
  // that only prolongs the lockout.
  if (cause.status === 429 && cause.code !== "RATE_LIMITED") return true;
  return cause.status === 500 && cause.code === "UNKNOWN";
}

/** How long the pouches take to lift off after a successful sign-in. */
const TAKEOFF_MS = 820;

function prefersReducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** A sleeping server wakes within about a minute; keep trying that long. */
const WAKE_RETRY_MS = 90_000;
const WAKE_RETRY_EVERY_MS = 5_000;

export default function LoginPage() {
  const { user, loading, signIn } = useAuth();
  const router = useRouter();

  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [waking, setWaking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [leaving, setLeaving] = useState(false);
  const panelRef = useRef<HTMLElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  usePouchMagnet(panelRef, !loading);

  // Already signed in? Do not make them look at a sign-in form. (Not while
  // this page is signing someone in: that path plays the take-off first.)
  useEffect(() => {
    if (!loading && user && !submitting) {
      router.replace(user.must_change_password ? "/set-password" : "/dashboard");
    }
  }, [user, loading, submitting, router]);

  // Put the cursor in the username box the moment the form is on screen,
  // so typing can start straight away. Desktop only: on a phone a focused
  // box pops the keyboard over the page before anyone asked for it.
  useEffect(() => {
    if (!window.matchMedia("(hover: hover) and (pointer: fine)").matches) return;
    const root = document.documentElement;
    function focusWhenShown() {
      if (root.dataset.intro === "play") return; // still hidden under the intro
      const box = document.getElementById("identifier");
      if (box && (document.activeElement === document.body || document.activeElement === null)) {
        box.focus({ preventScroll: true });
      }
    }
    focusWhenShown();
    const watch = new MutationObserver(focusWhenShown);
    watch.observe(root, { attributes: true, attributeFilter: ["data-intro"] });
    return () => watch.disconnect();
  }, [loading]);

  /** Wrong details: the form gives a short shake and the pouches droop. */
  function signalFailure() {
    if (prefersReducedMotion()) return;
    formRef.current?.animate(
      [
        { transform: "translateX(0)" },
        { transform: "translateX(-10px)" },
        { transform: "translateX(9px)" },
        { transform: "translateX(-6px)" },
        { transform: "translateX(3px)" },
        { transform: "translateX(0)" },
      ],
      { duration: 460, easing: "ease-out" },
    );
    let order = 0;
    document.querySelectorAll<HTMLElement>(".pw-pouch-in .pw-flutter").forEach((pouch) => {
      if (pouch.getClientRects().length === 0) return; // the layout not on screen
      pouch.animate(
        [
          { transform: "none" },
          { transform: "translateY(11px) rotate(10deg) scaleY(0.94)", offset: 0.35 },
          { transform: "translateY(7px) rotate(7deg)", offset: 0.65 },
          { transform: "none" },
        ],
        { duration: 1200, delay: order++ * 70, easing: "cubic-bezier(0.34, 1.56, 0.64, 1)" },
      );
    });
  }

  async function attemptSignIn(withIdentifier: string, withPassword: string) {
    setError(null);
    setWaking(false);
    setSubmitting(true);
    const giveUpAt = Date.now() + WAKE_RETRY_MS;
    for (;;) {
      try {
        const signedIn = await signIn(withIdentifier.trim(), withPassword);
        // The pouches take off on the wind, then the dashboard slides in
        // (html[data-arrive] in globals.css). The flag clears itself.
        if (!prefersReducedMotion()) {
          setLeaving(true);
          await new Promise((resolve) => setTimeout(resolve, TAKEOFF_MS));
          const root = document.documentElement;
          root.dataset.arrive = "signin";
          window.setTimeout(() => delete root.dataset.arrive, 1600);
        }
        router.replace(signedIn.must_change_password ? "/set-password" : "/dashboard");
        return;
      } catch (cause) {
        // The server is asleep or restarting: wait and try again rather than
        // report a failure the person can do nothing about.
        if (cause instanceof ApiError && serverUnavailable(cause) && Date.now() < giveUpAt) {
          setWaking(true);
          await new Promise((resolve) => setTimeout(resolve, WAKE_RETRY_EVERY_MS));
          continue;
        }
        setWaking(false);
        setError(signInErrorMessage(cause));
        setSubmitting(false);
        signalFailure();
        return;
      }
    }
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    await attemptSignIn(identifier, password);
  }

  // Once signed in this page shows a spinner and redirects - except while
  // it is the one signing in, when it stays up for the take-off.
  if (loading || (user && !submitting)) {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <div className={cn("pw-pink-actions grid min-h-dvh lg:grid-cols-[1.05fr_1fr]", leaving && "pw-takeoff")}>
      {/* ------------------------------------------------ brand panel */}
      <aside
        ref={panelRef}
        className="relative hidden overflow-hidden border-r-2 border-ink bg-pw-ink lg:flex lg:flex-col lg:p-12"
      >
        <div className="relative flex items-center gap-3">
          <BrandLogo height={44} tone="onDark" />
          <div className="border-l-2 border-white/25 pl-3">
            <p className="font-[family-name:var(--font-display)] text-[17px] font-black uppercase leading-none text-white">BDE &amp; Sales Portal</p>
            <p className="mt-1 text-[12px] font-extrabold uppercase leading-tight tracking-[0.06em] text-pw-yellow">Pouchwale · Mehsana</p>
          </div>
        </div>

        {/* The pouches are the picture: what the company makes, in its own colours. */}
        <FloatingPouches layout="panel" leaving={leaving} className="relative mx-auto mt-6 min-h-0 w-full max-w-[560px] flex-1" />
      </aside>

      {/* ------------------------------------------------------- form */}
      <main className="flex items-center justify-center px-5 py-12 sm:px-8">
        {/* pw-reveal: waits for the intro to start lifting, then rises in
            just behind the pouches (globals.css). */}
        <div className="pw-reveal w-full max-w-sm animate-fade-up">
          {/* Phone: the same pouches, in a short band above the form. */}
          <div className="relative mb-7 overflow-hidden rounded-[20px] border-2 border-ink bg-pw-ink card-shadow lg:hidden">
            <div className="absolute left-4 top-4 z-10">
              <BrandLogo height={26} tone="onDark" />
            </div>
            <FloatingPouches layout="band" leaving={leaving} className="relative mx-auto mt-10 aspect-[16/9] w-full max-w-[420px]" />
          </div>

          <h1 className="text-[30px] font-black leading-none text-content">Welcome back</h1>
          <p className="mt-2 text-[13.5px] font-semibold text-muted">
            Sign in to the BDE &amp; Sales Portal.
          </p>

          <form ref={formRef} onSubmit={onSubmit} className="mt-7 space-y-4" noValidate>
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
                placeholder="Your username, e.g. parag"
                icon={<UserRound className="size-4" />}
                value={identifier}
                onChange={(event) => setIdentifier(event.target.value)}
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
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  invalid={Boolean(error)}
                  className="pr-10"
                />
                {/* Shows only what was just typed, to check it before signing in. */}
                <button
                  type="button"
                  onClick={() => setShowPassword((shown) => !shown)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  aria-pressed={showPassword}
                  title={showPassword ? "Hide password" : "Show password"}
                  className="absolute right-1 top-1/2 grid size-8 -translate-y-1/2 place-items-center rounded-md text-subtle transition-colors hover:bg-surface-hover hover:text-content"
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
            {waking ? (
              <p className="text-[12.5px] text-muted" role="status">
                The server is starting up - this can take up to a minute. Signing you in
                as soon as it is ready…
              </p>
            ) : null}

            {/* Disabled while it works, like the stock loading state, but with
                the brand's own pouch spinner instead of a ring. */}
            <Button type="submit" size="lg" className="w-full" disabled={submitting} aria-busy={submitting}>
              {submitting ? <PouchSpinner /> : null}
              {waking ? "Starting server…" : submitting ? "Signing in…" : "Sign in"}
              {submitting ? null : <ArrowRight className="size-4" aria-hidden />}
            </Button>
          </form>
        </div>
      </main>
    </div>
  );
}
