import type { Metadata } from "next";
import Link from "next/link";

import { StatusScreen, linkButtonClass } from "./_status/StatusScreen";

export const metadata: Metadata = {
  title: "Page not found",
};

export default function NotFound() {
  return (
    <StatusScreen
      kind="not-found"
      title="We couldn't find that page"
      description="The link may be out of date, or the page may have moved. Head back to your dashboard to carry on."
      actions={
        <Link href="/dashboard" className={linkButtonClass.primary}>
          Go to dashboard
        </Link>
      }
    />
  );
}
