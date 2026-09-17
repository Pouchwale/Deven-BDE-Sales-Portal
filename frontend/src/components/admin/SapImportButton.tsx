"use client";

import { FileSpreadsheet, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { api, errorMessage, type SapImportResult, type SapSyncStatus } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { formatDateTime, formatRelative } from "@/lib/format";
import { pollWhileVisible } from "@/lib/visiblePoll";
import { isSuperAdmin } from "@/lib/roles";
import { useToast } from "@/lib/toast";

/** How often the link status is re-read. Matched to the backend's own 30s file
 *  check - polling twice as fast only produced twice the requests. */
const STATUS_POLL_MS = 30_000;

type File_ = SapImportResult["files"][number];

function describe(file: File_): string {
  const parts = [
    `${file.customers_created} new customers`,
    `${file.customers_updated} updated`,
    `${file.owners_changed} reassigned`,
    `${file.lines_created} new invoice lines`,
    `${file.leads_created} new leads`,
  ];
  if (file.error_count) {
    const first = file.errors.find((error) => error.type !== "skipped");
    parts.push(
      `${file.error_count} row(s) rejected and left unchanged` +
        (first ? ` (${first.customer ?? `row ${first.row}`}: ${first.message})` : ""),
    );
  }
  if (file.unmatched_sales_people.length) {
    parts.push(`unmatched sales people: ${file.unmatched_sales_people.join(", ")}`);
  }
  return parts.join(", ") + ".";
}

/**
 * SAP customer import, Super Admin only - renders nothing for anyone else,
 * matching the /admin/sap-* endpoints.
 *
 *   status line      - is the live workbook linked, and when did it last sync
 *   Sync now         - import the linked workbook immediately
 *   Upload SAP file  - pick an Excel/CSV export from this computer
 */
export function SapImportButton({ onImported }: { onImported?: () => void }) {
  const toast = useToast();
  const { user } = useAuth();
  const allowed = isSuperAdmin(user?.role);
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<"upload" | "sync" | null>(null);
  const [status, setStatus] = useState<SapSyncStatus | null>(null);
  const lastSynced = useRef<string | null>(null);
  const onImportedRef = useRef(onImported);
  useEffect(() => {
    onImportedRef.current = onImported;
  }, [onImported]);

  useEffect(() => {
    if (!allowed) return;
    let cancelled = false;
    const controller = new AbortController();

    async function poll() {
      try {
        const next = await api.admin.sapSyncStatus(controller.signal);
        if (cancelled) return;
        // A background sync landed since the last look: refresh the page's data.
        if (lastSynced.current && next.last_synced_at !== lastSynced.current) {
          onImportedRef.current?.();
        }
        lastSynced.current = next.last_synced_at;
        setStatus(next);
      } catch {
        // The status line is informational; a failed poll just tries again.
      }
    }

    void poll();
    // Nobody reads a status line in a background tab; catch up on return.
    const stopPolling = pollWhileVisible(() => void poll(), STATUS_POLL_MS);
    return () => {
      cancelled = true;
      controller.abort();
      stopPolling();
    };
  }, [allowed]);

  if (!allowed) return null;

  async function run(kind: "upload" | "sync", call: () => Promise<SapImportResult>) {
    setBusy(kind);
    try {
      const result = await call();
      for (const file of result.files) {
        const notify = file.error_count ? toast.error : toast.success;
        notify(`Imported ${file.filename}`, describe(file));
      }
      onImported?.();
      setStatus(await api.admin.sapSyncStatus());
    } catch (cause) {
      toast.error("SAP import failed", errorMessage(cause));
    } finally {
      setBusy(null);
    }
  }

  const linked = Boolean(status?.linked);
  const healthy = linked && status?.file_found && !status.last_error;

  return (
    <div className="flex flex-col items-start gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <input
          ref={input}
          type="file"
          accept=".xlsx,.xlsm,.csv"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) void run("upload", () => api.admin.sapUpload(file));
          }}
        />
        <Button
          onClick={() => void run("sync", api.admin.sapImport)}
          loading={busy === "sync"}
          disabled={busy !== null}
        >
          <FileSpreadsheet className="size-4" aria-hidden />
          Sync now
        </Button>
        <Button
          variant="secondary"
          onClick={() => input.current?.click()}
          loading={busy === "upload"}
          disabled={busy !== null}
        >
          <Upload className="size-4" aria-hidden />
          Upload SAP file
        </Button>
      </div>

      {status ? (
        <p
          className="flex max-w-xl items-start gap-1.5 text-[12px] leading-snug text-muted"
          title={status.linked_file_name ?? undefined}
        >
          <span
            aria-hidden
            className={cn(
              "mt-1 size-2 shrink-0 rounded-full",
              healthy ? "bg-success" : linked ? "bg-danger" : "bg-subtle",
            )}
          />
          <span>
            {!linked ? (
              "No live workbook linked (SAP_DATA_FILE) - use Upload SAP file."
            ) : !status.file_found ? (
              <>Linked workbook not found: {status.linked_file_name}</>
            ) : (
              <>
                Linked to <span className="font-medium text-content">{status.linked_file_name}</span>
                {status.auto_sync
                  ? ` · auto-sync every ${status.interval_seconds}s`
                  : " · auto-sync off"}
                {" · file saved "}
                {formatDateTime(status.file_saved_at)}
                {" · last synced "}
                {formatRelative(status.last_synced_at)}
                {status.last_result ? ` (${status.last_result.total_rows} rows)` : ""}
                {status.last_error ? (
                  <span className="block text-danger">
                    Last sync failed {formatRelative(status.last_error_at)}: {status.last_error}.
                    Nothing was changed; it retries automatically.
                  </span>
                ) : null}
                {status.last_result?.not_in_file.length ? (
                  <span className="block">
                    {status.last_result.not_in_file.length} portal customer(s) are no longer in the
                    workbook and were left unchanged.
                  </span>
                ) : null}
              </>
            )}
          </span>
        </p>
      ) : null}
    </div>
  );
}
