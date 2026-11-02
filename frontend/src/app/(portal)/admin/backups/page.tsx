"use client";

import {
  Activity,
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  Code2,
  Database,
  Download,
  HardDrive,
  History,
  Plus,
  RotateCcw,
  Save,
  ShieldCheck,
  Trash2,
  Archive,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
  CreateBackupDialog,
  DeleteBackupDialog,
  RestoreDialog,
  formatBytes,
} from "@/components/admin/BackupDialogs";
import { StatTile, StatTileSkeleton } from "@/components/dashboard/StatTile";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { EmptyState, ErrorState, InlineError, TableSkeleton } from "@/components/ui/Feedback";
import { Field, Input, Switch } from "@/components/ui/Form";
import { TBody, TD, TH, THead, TR, Table, TableWrap } from "@/components/ui/Table";
import { Tabs } from "@/components/ui/Tabs";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { formatDateTime, formatRelative } from "@/lib/format";
import { isSuperAdmin } from "@/lib/roles";
import { useToast } from "@/lib/toast";
import { useAsync } from "@/lib/useAsync";
import type { BackupJob, BackupOverview, BackupRecord } from "@/types/api";

const ACTIVE = ["QUEUED", "RUNNING"];

export default function BackupsPage() {
  const { user } = useAuth();
  if (!user) return null;
  if (!isSuperAdmin(user.role)) {
    return (
      <Card>
        <EmptyState
          icon={ShieldCheck}
          title="Super Admin only"
          description="Back-Up & Restore is available to the Super Admin."
        />
      </Card>
    );
  }
  return <BackupsInner />;
}

function BackupsInner() {
  const toast = useToast();
  const [tab, setTab] = useState("history");
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const overview = useAsync((signal) => api.backups.overview(signal), [nonce]);
  const backups = useAsync((signal) => api.backups.list(signal), [nonce]);
  const activity = useAsync((signal) => api.backups.activity(signal), [nonce]);

  const [creating, setCreating] = useState(false);
  const [restoring, setRestoring] = useState<{ backupId?: string } | null>(null);
  const [deleting, setDeleting] = useState<BackupRecord | null>(null);
  const [job, setJob] = useState<BackupJob | null>(null);

  // A job already running (another tab, or after a page refresh) comes from
  // the server; one started here is tracked locally.
  const current = job ?? overview.data?.active_job ?? null;

  // Follow the job until it ends; the server keeps its state, so a refresh
  // or a lost connection only pauses this, never the work.
  useEffect(() => {
    if (!current || !ACTIVE.includes(current.status)) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await api.backups.job(current.id);
        setJob(next);
        if (!ACTIVE.includes(next.status)) {
          window.clearInterval(timer);
          reload();
          if (next.status === "SUCCEEDED") toast.success(`${next.title} finished`, "Verified and saved.");
          else toast.error(`${next.title} did not complete`, next.error ?? next.step);
        }
      } catch {
        /* a restore may sign everyone out - the banner says so */
      }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [current, reload, toast]);

  const data = overview.data;
  const busy = Boolean(current && ACTIVE.includes(current.status));

  return (
    <>
      <PageHeader
        title="Back-Up & Restore"
        description="Protect the portal's database and code. Every backup is checked before it counts, and every restore takes a safety backup first."
        actions={
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => setRestoring({})} disabled={busy || !data?.storage.available}>
              <RotateCcw className="size-4" aria-hidden />
              Restore backup
            </Button>
            <Button onClick={() => setCreating(true)} disabled={busy || !data?.storage.available || !data?.tools.compatible}>
              <Plus className="size-4" aria-hidden />
              Create backup
            </Button>
          </div>
        }
      />

      {current ? <JobBanner job={current} onDismiss={() => setJob(null)} /> : null}

      {data && (!data.storage.available || data.tools.problem || data.storage.alert) ? (
        <div className="mb-4 space-y-2">
          {!data.storage.available ? (
            <Notice tone="warning" title="Backup storage is not ready">
              {data.storage.problem} Set a folder under <b>Storage</b>.
            </Notice>
          ) : null}
          {data.tools.problem ? (
            <Notice tone="danger" title="PostgreSQL tools">
              {data.tools.problem}
            </Notice>
          ) : null}
          {data.storage.alert ? <Notice tone="warning" title="Storage">{data.storage.alert}</Notice> : null}
        </div>
      ) : null}

      <div className="mb-5 grid grid-cols-2 gap-2.5 sm:gap-3.5 lg:grid-cols-4">
        {overview.loading && !data ? (
          Array.from({ length: 8 }).map((_, i) => <StatTileSkeleton key={i} index={i} />)
        ) : data ? (
          <>
            <StatTile index={0} label="Total backups" value={data.counts.total} hint={`${data.counts.verified} verified`} icon={Archive} />
            <StatTile index={1} label="Database backups" value={data.counts.database} hint="PostgreSQL dumps" icon={Database} />
            <StatTile index={2} label="Full project backups" value={data.counts.full} hint="Code + database ZIPs" icon={Code2} />
            <StatTile
              index={3}
              label="Storage used"
              value={formatBytes(data.storage.used_bytes)}
              hint={data.storage.max_bytes ? `of ${formatBytes(data.storage.max_bytes)} allowed` : "No limit set"}
              icon={HardDrive}
              accent={data.storage.alert ? "warning" : "neutral"}
            />
            <StatTile
              index={4}
              label="Latest backup"
              value={data.latest_backup ? formatRelative(data.latest_backup.created_at) : "None yet"}
              hint={data.latest_backup ? (data.latest_backup.kind === "FULL" ? "Full project" : "Database") : "Create the first one"}
              icon={CheckCircle2}
              accent={data.latest_backup ? "success" : "warning"}
            />
            <StatTile
              index={5}
              label="Latest restore"
              value={data.latest_restore ? formatRelative(data.latest_restore.at) : "Never"}
              hint={data.latest_restore ? (data.latest_restore.action === "BACKUP_RESTORED" ? "Succeeded" : "Failed") : "No restore has run"}
              icon={RotateCcw}
              accent={data.latest_restore?.action === "BACKUP_RESTORE_FAILED" ? "danger" : "neutral"}
            />
            <StatTile index={6} label="Storage destination" value={data.storage.available ? data.storage.label : "Not set"} hint={data.storage.available ? "Writable" : "Needs attention"} icon={HardDrive} accent={data.storage.available ? "neutral" : "warning"} />
            <StatTile
              index={7}
              label="Verification"
              value={data.counts.failed || data.counts.missing ? "Attention" : data.counts.total ? "All verified" : "—"}
              hint={`${data.counts.failed} failed · ${data.counts.missing} missing files`}
              icon={ShieldCheck}
              accent={data.counts.failed || data.counts.missing ? "warning" : "success"}
            />
          </>
        ) : overview.error ? (
          <div className="col-span-full">
            <ErrorState error={overview.error} onRetry={overview.reload} />
          </div>
        ) : null}
      </div>

      <Tabs
        className="mb-4 w-fit"
        active={tab}
        onChange={setTab}
        tabs={[
          { key: "history", label: "Backup history", count: backups.data?.length },
          { key: "storage", label: "Storage" },
          { key: "settings", label: "Settings" },
          { key: "activity", label: "Activity", count: activity.data?.length },
        ]}
      />

      {tab === "history" ? (
        <HistoryTable
          loading={backups.loading && !backups.data}
          error={backups.error}
          rows={backups.data ?? []}
          busy={busy}
          onRetry={backups.reload}
          onVerify={async (b) => {
            try {
              setJob(await api.backups.verify(b.id));
            } catch (cause) {
              toast.error("Could not verify", errorMessage(cause));
            }
          }}
          onRestore={(b) => setRestoring({ backupId: b.id })}
          onDelete={setDeleting}
        />
      ) : null}
      {tab === "storage" && data ? <StoragePanel data={data} onSaved={reload} /> : null}
      {tab === "settings" && data ? <SettingsPanel settings={data.settings} onSaved={reload} /> : null}
      {tab === "activity" ? <ActivityList items={activity.data ?? []} loading={activity.loading && !activity.data} /> : null}

      {creating ? <CreateBackupDialog onClose={() => setCreating(false)} onStarted={setJob} /> : null}
      {restoring ? (
        <RestoreDialog
          backups={backups.data ?? []}
          initialBackupId={restoring.backupId}
          onClose={() => setRestoring(null)}
          onStarted={setJob}
        />
      ) : null}
      {deleting ? (
        <DeleteBackupDialog
          backup={deleting}
          onClose={() => setDeleting(null)}
          onDone={() => {
            toast.success("Backup deleted", "Its record stays in the history.");
            reload();
          }}
        />
      ) : null}
    </>
  );
}

/* -------------------------------------------------------------- pieces */
function Notice({ tone, title, children }: { tone: "warning" | "danger"; title: string; children: React.ReactNode }) {
  return (
    <div
      className={cn(
        "flex gap-3 rounded-card border px-4 py-3",
        tone === "danger" ? "border-danger/25 bg-danger-soft" : "border-warning/30 bg-warning-soft",
      )}
    >
      <AlertTriangle className={cn("mt-0.5 size-4 shrink-0", tone === "danger" ? "text-danger" : "text-warning")} aria-hidden />
      <div className="min-w-0 text-[13px]">
        <p className="font-semibold text-content">{title}</p>
        <p className="mt-0.5 leading-relaxed text-muted">{children}</p>
      </div>
    </div>
  );
}

function JobBanner({ job, onDismiss }: { job: BackupJob; onDismiss: () => void }) {
  const running = ACTIVE.includes(job.status);
  const failed = job.status === "FAILED" || job.status === "INTERRUPTED";
  return (
    <Card className="mb-4 overflow-hidden">
      <div className="flex flex-wrap items-center gap-3 px-4 py-3">
        <span
          className={cn(
            "grid size-8 place-items-center rounded-lg",
            failed ? "bg-danger-soft text-danger" : running ? "bg-brand-600/10 text-brand-700 dark:text-brand-300" : "bg-success-soft text-success",
          )}
        >
          {failed ? <AlertTriangle className="size-4" aria-hidden /> : running ? <Activity className="size-4 animate-pulse" aria-hidden /> : <CheckCircle2 className="size-4" aria-hidden />}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[13.5px] font-semibold text-content">
            {job.title} — {running ? job.step : job.status === "SUCCEEDED" ? "completed" : job.status.toLowerCase()}
          </p>
          <p className="text-[12px] text-muted">
            {failed
              ? job.error ?? job.step
              : running
                ? job.kind === "RESTORE"
                  ? "A safety backup is taken first. You may be asked to sign in again when it finishes."
                  : "Runs on the server — you can leave this page."
                : formatDateTime(job.finished_at)}
          </p>
        </div>
        {!running ? (
          <Button size="sm" variant="ghost" onClick={onDismiss}>
            Dismiss
          </Button>
        ) : null}
      </div>
      <div className="h-1 bg-surface-2">
        <div
          className={cn("h-full transition-[width] duration-500", failed ? "bg-danger" : "bg-brand-600")}
          style={{ width: `${running ? Math.max(job.percent, 4) : 100}%` }}
        />
      </div>
    </Card>
  );
}

function StatusBadge({ row }: { row: BackupRecord }) {
  if (row.deleted_at) return <Badge className="border-line bg-surface-2 text-subtle">Deleted</Badge>;
  if (row.status === "VERIFIED" && row.file_present === false)
    return <Badge className="border-transparent bg-warning-soft text-warning">File missing</Badge>;
  if (row.status === "VERIFIED")
    return (
      <Badge className="border-transparent bg-success-soft text-success">
        <ShieldCheck className="size-3" aria-hidden />
        Verified
      </Badge>
    );
  if (row.status === "RUNNING") return <Badge className="border-line bg-surface-2 text-muted">In progress</Badge>;
  return (
    <Badge className="border-transparent bg-danger-soft text-danger" title={row.error ?? undefined}>
      Failed
    </Badge>
  );
}

const SOURCE_LABEL: Record<BackupRecord["source"], string> = {
  manual: "Manual",
  scheduled: "Scheduled",
  "pre-restore": "Safety (before restore)",
  uploaded: "Uploaded",
};

function HistoryTable({
  rows,
  loading,
  error,
  busy,
  onRetry,
  onVerify,
  onRestore,
  onDelete,
}: {
  rows: BackupRecord[];
  loading: boolean;
  error: unknown;
  busy: boolean;
  onRetry: () => void;
  onVerify: (b: BackupRecord) => void;
  onRestore: (b: BackupRecord) => void;
  onDelete: (b: BackupRecord) => void;
}) {
  if (error) return <ErrorState error={error} onRetry={onRetry} />;
  return (
    <Card className="overflow-hidden">
      {loading ? (
        <TableSkeleton rows={5} columns={8} />
      ) : rows.length === 0 ? (
        <EmptyState icon={History} title="No backups yet" description="Create the first backup — it takes under a minute." />
      ) : (
        <TableWrap>
          <Table>
            <THead>
              <tr>
                <TH>Backup</TH>
                <TH className="md:max-2xl:hidden">Type</TH>
                <TH>Created</TH>
                <TH align="right">Size</TH>
                <TH className="md:max-xl:hidden">Created by</TH>
                <TH className="md:max-2xl:hidden">Checksum</TH>
                <TH>Status</TH>
                <TH align="right">Actions</TH>
              </tr>
            </THead>
            <TBody>
              {rows.map((row) => {
                const usable = row.status === "VERIFIED" && row.file_present !== false;
                return (
                  <TR key={row.id}>
                    <TD>
                      <span className="block max-w-[16rem] truncate font-medium text-content" title={row.file ?? ""}>
                        {row.file?.split("/").pop() ?? "—"}
                      </span>
                      <span className="text-[11.5px] text-subtle">
                        {SOURCE_LABEL[row.source]} · {row.file?.split("/")[0] ?? "—"}
                      </span>
                    </TD>
                    <TD data-label="Type" className="md:max-2xl:hidden">
                      <span className="inline-flex items-center gap-1.5 text-[13px] text-muted">
                        {row.kind === "FULL" ? <Code2 className="size-3.5" aria-hidden /> : <Database className="size-3.5" aria-hidden />}
                        {row.kind === "FULL" ? "Full project" : "Database"}
                      </span>
                    </TD>
                    <TD data-label="Created" className="whitespace-nowrap text-muted" title={formatDateTime(row.created_at)}>
                      {formatDateTime(row.created_at)}
                    </TD>
                    <TD data-label="Size" align="right" className="whitespace-nowrap tabular-nums text-muted">
                      {formatBytes(row.size_bytes)}
                    </TD>
                    <TD data-label="Created by" className="text-muted md:max-xl:hidden">
                      {row.created_by?.name ?? "Schedule"}
                    </TD>
                    <TD data-label="Checksum" className="md:max-2xl:hidden">
                      <code className="text-[11.5px] text-subtle" title={row.sha256 ?? undefined}>
                        {row.sha256 ? `${row.sha256.slice(0, 10)}…` : "—"}
                      </code>
                    </TD>
                    <TD data-label="Status">
                      <StatusBadge row={row} />
                    </TD>
                    <TD data-actions align="right">
                      <span className="flex items-center justify-end gap-1">
                        {usable ? (
                          <a
                            href={api.backups.downloadUrl(row.id)}
                            className="grid size-9 place-items-center rounded-lg text-muted transition-colors hover:bg-surface-hover hover:text-content"
                            title="Download"
                            aria-label="Download"
                          >
                            <Download className="size-4" aria-hidden />
                          </a>
                        ) : null}
                        {row.file_present !== false && !row.deleted_at ? (
                          <IconAction label="Verify again" disabled={busy} onClick={() => onVerify(row)} icon={ShieldCheck} />
                        ) : null}
                        {usable ? <IconAction label="Restore" disabled={busy} onClick={() => onRestore(row)} icon={RotateCcw} /> : null}
                        {!row.deleted_at ? (
                          <IconAction label="Delete" disabled={busy} onClick={() => onDelete(row)} icon={Trash2} danger />
                        ) : null}
                      </span>
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        </TableWrap>
      )}
    </Card>
  );
}

function IconAction({
  label,
  onClick,
  icon: Icon,
  disabled,
  danger,
}: {
  label: string;
  onClick: () => void;
  icon: React.ComponentType<{ className?: string }>;
  disabled?: boolean;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
      className={cn(
        "grid size-9 place-items-center rounded-lg text-muted transition-colors disabled:opacity-40",
        danger ? "hover:bg-danger-soft hover:text-danger" : "hover:bg-surface-hover hover:text-content",
      )}
    >
      <Icon className="size-4" aria-hidden />
    </button>
  );
}

function StoragePanel({ data, onSaved }: { data: BackupOverview; onSaved: () => void }) {
  const toast = useToast();
  const [path, setPath] = useState(data.storage.path ?? "");
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const used = data.storage.used_bytes;
  const max = data.storage.max_bytes;
  const share = max ? Math.min(used / max, 1) : null;

  async function test() {
    setChecking(true);
    setResult(null);
    try {
      const answer = await api.backups.testStorage(path);
      setResult({ ok: answer.ok, text: answer.ok ? answer.message ?? "Ready." : answer.problem ?? "Not usable." });
    } catch (cause) {
      setResult({ ok: false, text: errorMessage(cause) });
    } finally {
      setChecking(false);
    }
  }

  async function save() {
    setChecking(true);
    try {
      await api.backups.saveSettings({ storage_path: path });
      toast.success("Storage saved", "Backups will be written there.");
      onSaved();
    } catch (cause) {
      setResult({ ok: false, text: errorMessage(cause) });
    } finally {
      setChecking(false);
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
      <Card>
        <CardHeader className="block">
          <CardTitle>Storage destination</CardTitle>
          <CardDescription>
            A permanent folder on this server, outside the project. On Render this must be a persistent disk&apos;s
            mount path — the app&apos;s normal disk is wiped on every deploy.
          </CardDescription>
        </CardHeader>
        <CardBody className="space-y-3">
          <Field label="Folder" htmlFor="storage-path" hint="Created if it does not exist, then tested by writing a file.">
            <Input
              id="storage-path"
              value={path}
              placeholder="D:\GP3\backups   or   /var/data/backups"
              onChange={(e) => {
                setPath(e.target.value);
                setResult(null);
              }}
            />
          </Field>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => void test()} loading={checking} disabled={!path.trim()}>
              Test folder
            </Button>
            <Button onClick={() => void save()} disabled={!path.trim() || checking || path === data.storage.path}>
              <Save className="size-4" aria-hidden />
              Save
            </Button>
          </div>
          {result ? (
            result.ok ? (
              <p className="flex items-center gap-2 text-[12.5px] text-success">
                <CheckCircle2 className="size-4" aria-hidden />
                {result.text}
              </p>
            ) : (
              <InlineError>{result.text}</InlineError>
            )
          ) : null}
          <p className="text-[12px] text-subtle">
            Inside it: <code>full-project/</code> · <code>database-only/</code> · <code>restore-staging/</code> ·{" "}
            <code>backup-logs/</code>
          </p>
        </CardBody>
      </Card>
      <Card>
        <CardHeader className="block">
          <CardTitle>Usage</CardTitle>
          <CardDescription>{data.storage.available ? data.storage.path : "Not available"}</CardDescription>
        </CardHeader>
        <CardBody className="space-y-3 text-[13px]">
          <div>
            <div className="mb-1.5 flex justify-between text-muted">
              <span>Backups</span>
              <span className="tabular-nums">
                {formatBytes(used)}
                {max ? ` / ${formatBytes(max)}` : ""}
              </span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-surface-2">
              <div
                className={cn("h-full rounded-full", data.storage.alert ? "bg-warning" : "bg-brand-600")}
                style={{ width: `${share != null ? Math.max(share * 100, 2) : 2}%` }}
              />
            </div>
          </div>
          <dl className="grid grid-cols-2 gap-y-1.5">
            <dt className="text-subtle">Free on disk</dt>
            <dd className="text-right tabular-nums text-content">{formatBytes(data.storage.free_bytes)}</dd>
            <dt className="text-subtle">Last successful write</dt>
            <dd className="text-right text-content">{data.latest_backup ? formatRelative(data.latest_backup.finished_at ?? data.latest_backup.created_at) : "—"}</dd>
            <dt className="text-subtle">Last verification</dt>
            <dd className="text-right text-content">
              {data.latest_backup?.verification ? (data.latest_backup.verification.ok ? "Passed" : "Failed") : "—"}
            </dd>
            <dt className="text-subtle">Database</dt>
            <dd className="truncate text-right text-content" title={data.target.identity}>
              {data.target.database} ({data.target.environment})
            </dd>
            <dt className="text-subtle">pg_dump</dt>
            <dd className="text-right text-content">{data.tools.pg_dump ? `v${data.tools.pg_dump}` : "not found"}</dd>
          </dl>
        </CardBody>
      </Card>
    </div>
  );
}

function SettingsPanel({ settings, onSaved }: { settings: Record<string, unknown>; onSaved: () => void }) {
  const toast = useToast();
  const [form, setForm] = useState({
    auto_daily: Boolean(settings["backup.auto_daily"]),
    auto_time: String(settings["backup.auto_time"] ?? "02:30"),
    keep_daily: Number(settings["backup.keep_daily"] ?? 7),
    keep_weekly: Number(settings["backup.keep_weekly"] ?? 4),
    keep_monthly: Number(settings["backup.keep_monthly"] ?? 6),
    max_storage_gb: Number(settings["backup.max_storage_gb"] ?? 20),
    alert_percent: Number(settings["backup.alert_percent"] ?? 80),
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => setForm((f) => ({ ...f, [key]: value }));

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await api.backups.saveSettings(form);
      toast.success("Backup settings saved");
      onSaved();
    } catch (cause) {
      setError(cause);
    } finally {
      setSaving(false);
    }
  }

  const number = (key: "keep_daily" | "keep_weekly" | "keep_monthly" | "max_storage_gb" | "alert_percent", label: string, hint: string) => (
    <Field label={label} htmlFor={key} hint={hint}>
      <Input id={key} type="number" min={0} value={form[key]} onChange={(e) => set(key, Number(e.target.value))} />
    </Field>
  );

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader className="block">
          <CardTitle className="flex items-center gap-2">
            <CalendarClock className="size-4 text-subtle" aria-hidden />
            Automatic backups
          </CardTitle>
          <CardDescription>
            A database backup made by the server itself every day — no browser needs to be open. On this PC the
            Windows task already backs up the live database each night; this one backs up the database this portal
            runs on.
          </CardDescription>
        </CardHeader>
        <CardBody className="space-y-4">
          <Switch checked={form.auto_daily} onChange={(v) => set("auto_daily", v)} label="Daily database backup" />
          <Field label="Time (IST)" htmlFor="auto_time">
            <Input id="auto_time" type="time" value={form.auto_time} onChange={(e) => set("auto_time", e.target.value)} />
          </Field>
        </CardBody>
      </Card>
      <Card>
        <CardHeader className="block">
          <CardTitle>Retention & storage</CardTitle>
          <CardDescription>
            Only scheduled backups are pruned, and the newest verified backup is never removed.
          </CardDescription>
        </CardHeader>
        <CardBody className="grid gap-3 sm:grid-cols-3">
          {number("keep_daily", "Daily", "Days kept")}
          {number("keep_weekly", "Weekly", "Weeks kept")}
          {number("keep_monthly", "Monthly", "Months kept")}
          {number("max_storage_gb", "Storage limit (GB)", "0 = no limit")}
          {number("alert_percent", "Alert at (%)", "Of the limit")}
        </CardBody>
      </Card>
      <div className="flex items-center gap-3 lg:col-span-2">
        <Button onClick={() => void save()} loading={saving}>
          <Save className="size-4" aria-hidden />
          Save settings
        </Button>
        {error ? <InlineError>{errorMessage(error)}</InlineError> : null}
      </div>
    </div>
  );
}

const ACTION_LABEL: Record<string, string> = {
  BACKUP_CREATED: "Backup created",
  BACKUP_FAILED: "Backup failed",
  BACKUP_VERIFIED: "Backup verified",
  BACKUP_DELETED: "Backup deleted",
  BACKUP_DOWNLOADED: "Backup downloaded",
  BACKUP_STORAGE_CHANGED: "Storage changed",
  BACKUP_RESTORE_STARTED: "Restore started",
  BACKUP_RESTORED: "Restore completed",
  BACKUP_RESTORE_FAILED: "Restore failed",
  RESTORE_FILE_UPLOADED: "File uploaded for restore",
  RETENTION_PRUNED: "Old scheduled backups pruned",
};

function ActivityList({ items, loading }: { items: Array<{ at: string; action: string; actor: { name: string } | null; [k: string]: unknown }>; loading: boolean }) {
  return (
    <Card className="overflow-hidden">
      {loading ? (
        <TableSkeleton rows={5} columns={3} />
      ) : items.length === 0 ? (
        <EmptyState icon={Activity} title="Nothing yet" description="Every backup, restore and setting change is listed here." />
      ) : (
        <ul className="divide-y divide-line">
          {items.map((item, index) => {
            const bad = /FAILED/.test(item.action);
            const detail = (item.error as string) || (item.detail as string) || (item.source as string) || (item.kind as string) || "";
            return (
              <li key={`${item.at}-${index}`} className="flex items-start gap-3 px-5 py-3">
                <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", bad ? "bg-danger" : "bg-success")} aria-hidden />
                <div className="min-w-0 flex-1">
                  <p className="text-[13.5px] font-medium text-content">{ACTION_LABEL[item.action] ?? item.action}</p>
                  <p className="truncate text-[12px] text-muted">
                    {item.actor?.name ?? "Schedule"}
                    {detail ? ` · ${detail}` : ""}
                  </p>
                </div>
                <span className="whitespace-nowrap text-[12px] text-subtle" title={formatDateTime(item.at)}>
                  {formatRelative(item.at)}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
