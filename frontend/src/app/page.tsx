"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { Spinner } from "@/components/ui/Feedback";
import { useAuth } from "@/lib/auth";

/**
 * The root path. Sends you where you actually belong rather than being a
 * page in its own right — which is why hitting the backend's port shows a
 * 404: the API has no `/`, and this is the app that does.
 */
export default function IndexPage() {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (loading) return;
    router.replace(user ? "/dashboard" : "/login");
  }, [user, loading, router]);

  return (
    <div className="flex min-h-dvh items-center justify-center">
      <div className="flex items-center gap-2.5 text-sm text-muted">
        <Spinner />
        Loading…
      </div>
    </div>
  );
}
