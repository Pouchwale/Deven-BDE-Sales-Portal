"use client";

import { AlertTriangle, Check, Code2, Database, FileArchive, ShieldCheck, Upload } from "lucide-react";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { InlineError, Spinner } from "@/components/ui/Feedback";
import { Field, Input, PasswordInput, Select } from "@/components/ui/Form";
import { Modal } from "@/components/ui/Modal";
import { api, errorMessage } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";
import type { BackupJob, BackupRecord, RestoreInspection } from "@/types/api";

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

/* ------------------------------------------------------------ create */
export function CreateBackupDialog({
  onClose,
  onStarted,
}: {
  onClose: () => void;
  onStarted: (job: BackupJob) => void;
}) {
  const [type, setType] = useState<"database" | "full">("database");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      onStarted(await api.backups.create(type));
      onClose();
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }

  const options = [
    {
      key: "database" as const,
      icon: Database,
      title: "Only database",
      text: "A complete PostgreSQL dump: every table, record, key, index and sequence. Small and quick.",
      file: "GP3_Database_YYYYMMDD_HHMMSS.dump",
    },
    {
      key: "full" as const,
      icon: Code2,
      title: "With code — full project",
      text: "The backend, frontend, scripts and docs with their folder structure, plus the database dump and a manifest. No secrets are included.",
      file: "GP3_Full_Backup_YYYYMMDD_HHMMSS.zip",
    },
  ];

  return (
    <Modal
      open
      onClose={onClose}
      size="lg"
      title="Create a backup"
      description="It runs in the background — you can leave this page and come back."
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={() => void start()} loading={busy}>
            Create backup
          </Button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2" role="radiogroup" aria-label="Backup type">
        {options.map((option) => {
          const selected = type === option.key;
          const Icon = option.icon;
          return (
            <button
              key={option.key}
              type="button"
              role="radio"
              aria-checked={selected}
              onClick={() => setType(option.key)}
              className={cn(
                "relative flex flex-col gap-2 rounded-xl border p-4 text-left transition-[border-color,box-shadow,background-color]",
                selected
                  ? "border-brand-500 bg-brand-600/5 ring-4 ring-brand-500/12"
                  : "border-line hover:border-line-strong hover:bg-surface-hover",
              )}
            >
              {selected ? (
                <span className="absolute right-3 top-3 grid size-5 place-items-center rounded-full bg-brand-600 text-white">
                  <Check className="size-3" aria-hidden />
                </span>
              ) : null}
              <span className="grid size-9 place-items-center rounded-lg bg-surface-2 text-brand-700 dark:text-brand-300">
                <Icon className="size-[18px]" aria-hidden />
              </span>
              <span className="text-[14px] font-semibold text-content">{option.title}</span>
              <span className="text-[12.5px] leading-relaxed text-muted">{option.text}</span>
              <code className="mt-1 truncate text-[11px] text-subtle">{option.file}</code>
            </button>
          );
        })}
      </div>
      {error ? (
        <div className="mt-3">
          <InlineError>{errorMessage(error)}</InlineError>
        </div>
      ) : null}
    </Modal>
  );
}

/* ------------------------------------------------------------ delete */
export function DeleteBackupDialog({
  backup,
  onClose,
  onDone,
}: {
  backup: BackupRecord;
  onClose: () => void;
  onDone: () => void;
}) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const name = backup.file?.split("/").pop() ?? "this backup";

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await api.backups.remove(backup.id, password);
      onDone();
      onClose();
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      size="sm"
      title="Delete this backup?"
      description={`${name} is removed from storage. Its record stays in the history.`}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="danger" onClick={() => void submit()} loading={busy} disabled={!password}>
            Delete
          </Button>
        </>
      }
    >
      <Field label="Your password" htmlFor="delete-password" required hint="Required for anything that removes data.">
        <PasswordInput
          id="delete-password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
      </Field>
      {error ? (
        <div className="mt-3">
          <InlineError>{errorMessage(error)}</InlineError>
        </div>
      ) : null}
    </Modal>
  );
}

/* ----------------------------------------------------------- restore */
type Step = "choose" | "review" | "confirm";

export function RestoreDialog({
  backups,
  initialBackupId,
  onClose,
  onStarted,
}: {
  backups: BackupRecord[];
  initialBackupId?: string;
  onClose: () => void;
  onStarted: (job: BackupJob) => void;
}) {
  const restorable = backups.filter((b) => b.status === "VERIFIED" && b.file_present !== false);
  const [step, setStep] = useState<Step>("choose");
  const [mode, setMode] = useState<"history" | "upload">("history");
  const [backupId, setBackupId] = useState(initialBackupId ?? restorable[0]?.id ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [source, setSource] = useState<{ backup_id?: string; upload_id?: string } | null>(null);
  const [inspection, setInspection] = useState<RestoreInspection | null>(null);
  const [password, setPassword] = useState("");
  const [typed, setTyped] = useState("");
  const [otherSource, setOtherSource] = useState(false);
  const [understood, setUnderstood] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  async function inspect() {
    setBusy(true);
    setError(null);
    try {
      if (mode === "upload") {
        if (!file) return;
        const uploaded = await api.backups.upload(file);
        setSource({ upload_id: uploaded.upload.upload_id });
        setInspection(uploaded.inspection);
      } else {
        const chosen = { backup_id: backupId };
        setSource(chosen);
        setInspection(await api.backups.inspect(chosen));
      }
      setStep("review");
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }

  async function restore() {
    if (!source || !inspection) return;
    setBusy(true);
    setError(null);
    try {
      onStarted(
        await api.backups.restore({
          ...source,
          password,
          confirm_database: typed,
          allow_other_source: otherSource,
        }),
      );
      onClose();
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }

  const target = inspection?.target.database ?? "";
  const ready =
    Boolean(inspection?.restorable) &&
    typed === target &&
    Boolean(password) &&
    understood &&
    (inspection?.same_source || otherSource);

  const footer =
    step === "choose" ? (
      <>
        <Button variant="secondary" onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button
          onClick={() => void inspect()}
          loading={busy}
          disabled={mode === "upload" ? !file : !backupId}
        >
          {mode === "upload" ? "Upload and check" : "Check this backup"}
        </Button>
      </>
    ) : step === "review" ? (
      <>
        <Button variant="secondary" onClick={() => setStep("choose")} disabled={busy}>
          Back
        </Button>
        <Button onClick={() => setStep("confirm")} disabled={!inspection?.restorable}>
          Continue
        </Button>
      </>
    ) : (
      <>
        <Button variant="secondary" onClick={() => setStep("review")} disabled={busy}>
          Back
        </Button>
        <Button variant="danger" onClick={() => void restore()} loading={busy} disabled={!ready}>
          Restore database
        </Button>
      </>
    );

  return (
    <Modal
      open
      onClose={onClose}
      size="xl"
      title="Restore a backup"
      description="Nothing changes until the last step. A fresh safety backup is always taken first."
      footer={footer}
    >
      <ol className="mb-4 flex items-center gap-2 text-[12px]" aria-label="Steps">
        {(["choose", "review", "confirm"] as Step[]).map((s, index) => (
          <li key={s} className="flex items-center gap-2">
            <span
              className={cn(
                "grid size-5 place-items-center rounded-full text-[11px] font-semibold",
                step === s ? "bg-brand-600 text-white" : "bg-surface-2 text-subtle",
              )}
            >
              {index + 1}
            </span>
            <span className={step === s ? "font-medium text-content" : "text-subtle"}>
              {s === "choose" ? "Choose" : s === "review" ? "Check" : "Confirm"}
            </span>
            {index < 2 ? <span className="mx-1 h-px w-5 bg-line" aria-hidden /> : null}
          </li>
        ))}
      </ol>

      {step === "choose" ? (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-2 rounded-lg bg-surface-2 p-1">
            {(["history", "upload"] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className={cn(
                  "h-9 rounded-md text-[13px] font-medium transition-colors",
                  mode === m ? "bg-surface text-content shadow-sm" : "text-muted hover:text-content",
                )}
              >
                {m === "history" ? "From backup history" : "Upload a file"}
              </button>
            ))}
          </div>
          {mode === "history" ? (
            restorable.length === 0 ? (
              <p className="text-[13px] text-muted">There is no verified backup to restore yet.</p>
            ) : (
              <Field label="Backup" htmlFor="restore-backup">
                <Select id="restore-backup" value={backupId} onChange={(e) => setBackupId(e.target.value)}>
                  {restorable.map((b) => (
                    <option key={b.id} value={b.id}>
                      {(b.file?.split("/").pop() ?? b.id) + " · " + formatDateTime(b.created_at)}
                    </option>
                  ))}
                </Select>
              </Field>
            )
          ) : (
            <div>
              <input
                ref={fileInput}
                type="file"
                accept=".dump,.zip"
                className="hidden"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
              <button
                type="button"
                onClick={() => fileInput.current?.click()}
                className="flex w-full flex-col items-center gap-2 rounded-xl border border-dashed border-line-strong px-4 py-8 text-center transition-colors hover:bg-surface-hover"
              >
                <Upload className="size-5 text-subtle" aria-hidden />
                <span className="text-[13.5px] font-medium text-content">
                  {file ? file.name : "Choose a .dump or a full-project .zip"}
                </span>
                <span className="text-[12px] text-subtle">
                  {file ? formatBytes(file.size) : "The file is checked before anything can be restored."}
                </span>
              </button>
            </div>
          )}
        </div>
      ) : null}

      {step === "review" && inspection ? <Inspection inspection={inspection} /> : null}

      {step === "confirm" && inspection ? (
        <div className="space-y-4">
          <div className="flex gap-3 rounded-lg border border-danger/25 bg-danger-soft px-3 py-3">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden />
            <p className="text-[12.5px] leading-relaxed text-content">
              Every record in <b>{inspection.target.identity}</b> ({inspection.target.environment}) is replaced by
              the backup. Anything entered after the backup was taken is lost — except in the safety backup taken
              first, which you can restore from the history.
            </p>
          </div>
          {!inspection.same_source ? (
            <label className="flex items-start gap-2.5 rounded-lg border border-warning/30 bg-warning-soft px-3 py-2.5 text-[12.5px] text-content">
              <input
                type="checkbox"
                className="mt-0.5 size-4 accent-[var(--brand-600)]"
                checked={otherSource}
                onChange={(e) => setOtherSource(e.target.checked)}
              />
              <span>
                This backup came from a <b>different database</b> ({inspection.dump.archive_dbname ?? "unknown"}). I
                want to load that environment&apos;s data into <b>{target}</b>.
              </span>
            </label>
          ) : null}
          <Field
            label={`Type the database name to confirm: ${target}`}
            htmlFor="confirm-db"
            required
            error={typed && typed !== target ? "That is not the target database's name." : null}
          >
            <Input id="confirm-db" autoComplete="off" value={typed} onChange={(e) => setTyped(e.target.value)} />
          </Field>
          <Field label="Your password" htmlFor="restore-password" required>
            <PasswordInput
              id="restore-password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
          <label className="flex items-start gap-2.5 text-[12.5px] text-muted">
            <input
              type="checkbox"
              className="mt-0.5 size-4 accent-[var(--brand-600)]"
              checked={understood}
              onChange={(e) => setUnderstood(e.target.checked)}
            />
            <span>I understand the current data will be replaced, and I may need to sign in again afterwards.</span>
          </label>
        </div>
      ) : null}

      {busy && step === "choose" && mode === "upload" ? (
        <p className="mt-3 flex items-center gap-2 text-[12.5px] text-muted">
          <Spinner className="size-3.5" /> Uploading and checking the file…
        </p>
      ) : null}
      {error ? (
        <div className="mt-3">
          <InlineError>{errorMessage(error)}</InlineError>
        </div>
      ) : null}
    </Modal>
  );
}

function Inspection({ inspection }: { inspection: RestoreInspection }) {
  const rows: [string, string][] = [
    ["File", inspection.source.label],
    ["Type", inspection.source.kind === "FULL" ? "Full project ZIP (its database dump is restored)" : "Database dump"],
    ["Size", formatBytes(inspection.source.size_bytes)],
    ["Backup made", inspection.dump.created ?? inspection.source.created_at ?? "—"],
    ["Came from database", inspection.dump.archive_dbname ?? "—"],
    ["PostgreSQL", inspection.dump.dumped_from ?? "—"],
    [
      "Contents",
      `${inspection.dump.table_data} tables with data · ${inspection.dump.indexes} indexes · ${inspection.dump.constraints} constraints`,
    ],
    ["Will be restored into", `${inspection.target.identity} (${inspection.target.environment})`],
  ];
  const checks: [string, boolean][] = [
    ["PostgreSQL can read the dump", inspection.dump.readable],
    ["Checksum matches", inspection.checksum_ok],
    ...(inspection.source.kind === "FULL"
      ? ([["Every file matches the manifest", inspection.source.manifest_ok !== false]] as [string, boolean][])
      : []),
    ["Same database it was taken from", inspection.same_source],
  ];
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-1 gap-x-4 gap-y-2 text-[13px] sm:grid-cols-[11rem_1fr]">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-subtle">{label}</dt>
            <dd className="break-all font-medium text-content">{value}</dd>
          </div>
        ))}
      </dl>
      <ul className="space-y-1.5">
        {checks.map(([label, ok], index) => (
          <li key={label} className="flex items-center gap-2 text-[13px]">
            {ok ? (
              <ShieldCheck className="size-4 text-success" aria-hidden />
            ) : (
              <AlertTriangle className={cn("size-4", index === checks.length - 1 ? "text-warning" : "text-danger")} aria-hidden />
            )}
            <span className={ok ? "text-content" : "text-muted"}>{label}</span>
          </li>
        ))}
      </ul>
      {!inspection.restorable ? (
        <InlineError>{inspection.dump.problem ?? "This file cannot be restored."}</InlineError>
      ) : null}
      {inspection.source.kind === "FULL" ? (
        <p className="flex items-start gap-2 text-[12px] text-subtle">
          <FileArchive className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          The project code in the ZIP is unpacked for inspection only. Restoring application code is a separate,
          controlled deployment — never done from this page.
        </p>
      ) : null}
    </div>
  );
}
