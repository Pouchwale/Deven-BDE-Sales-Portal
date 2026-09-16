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

import { ApiError, api, getToken, setToken } from "@/lib/api";
import type { User } from "@/types/api";

interface AuthContextValue {
  user: User | null;
  /** True until the initial /auth/me has settled — routes wait on this
   *  rather than flashing the sign-in page at an authenticated user. */
  loading: boolean;
  signIn: (email: string, password: string) => Promise<User>;
  signOut: () => void;
  refresh: () => Promise<void>;
  setUser: (user: User) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUserState] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  useEffect(() => {
    const controller = new AbortController();

    async function restore() {
      if (!getToken()) {
        setLoading(false);
        return;
      }
      try {
        setUserState(await api.auth.me(controller.signal));
      } catch (error) {
        // An expired or revoked token is the normal case here, not a fault.
        if (error instanceof ApiError && error.isAuthFailure) setToken(null);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    void restore();
    return () => controller.abort();
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const response = await api.auth.login(email, password);
    setToken(response.access_token);
    setUserState(response.user);
    return response.user;
  }, []);

  const signOut = useCallback(() => {
    setToken(null);
    setUserState(null);
    router.replace("/login");
  }, [router]);

  const refresh = useCallback(async () => {
    if (!getToken()) return;
    try {
      setUserState(await api.auth.me());
    } catch (error) {
      if (error instanceof ApiError && error.isAuthFailure) {
        setToken(null);
        setUserState(null);
      }
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
