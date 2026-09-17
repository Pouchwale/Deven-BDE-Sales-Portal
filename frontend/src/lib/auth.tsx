"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { ApiError, api, clearLegacyToken } from "@/lib/api";
import type { User } from "@/types/api";

interface AuthContextValue {
  user: User | null;
  /** True until the initial /auth/me has settled — routes wait on this
   *  rather than flashing the sign-in page at an authenticated user. */
  loading: boolean;
  signIn: (identifier: string, password: string) => Promise<User>;
  /** Ends the session on the server, then returns to the sign-in page. */
  signOut: () => void;
  refresh: () => Promise<void>;
  setUser: (user: User) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * The session is an HttpOnly cookie the page cannot see, so "am I signed in"
 * has exactly one honest answer: ask the server. Nothing about the session is
 * stored in the browser by this code.
 */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUserState] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  useEffect(() => {
    // Older builds kept a bearer token in localStorage. It is useless now and
    // should not linger on shared machines.
    clearLegacyToken();

    const controller = new AbortController();

    async function restore() {
      try {
        setUserState(await api.auth.me(controller.signal));
      } catch (error) {
        // No session, or an expired/revoked one, is the normal case here.
        if (error instanceof ApiError && error.isAuthFailure) setUserState(null);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    void restore();
    return () => controller.abort();
  }, []);

  const signIn = useCallback(async (identifier: string, password: string) => {
    const response = await api.auth.login(identifier, password);
    setUserState(response.user);
    return response.user;
  }, []);

  const signOut = useCallback(() => {
    setUserState(null);
    router.replace("/login");
    // Idempotent on the server; a failure here (offline, already expired)
    // must not keep somebody looking at a signed-in screen.
    api.auth.logout().catch(() => {});
  }, [router]);

  const refresh = useCallback(async () => {
    try {
      setUserState(await api.auth.me());
    } catch (error) {
      if (error instanceof ApiError && error.isAuthFailure) setUserState(null);
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ user, loading, signIn, signOut, refresh, setUser: setUserState }),
    [user, loading, signIn, signOut, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
