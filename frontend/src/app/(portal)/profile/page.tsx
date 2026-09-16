"use client";

import { Info, Lock, ShieldCheck } from "lucide-react";
import { useState } from "react";

import { PageHeader } from "@/components/layout/PageHeader";
import { Avatar } from "@/components/ui/Avatar";
import { RoleBadge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { InlineError } from "@/components/ui/Feedback";
import { Field, Input } from "@/components/ui/Form";
import { api, errorMessage, setToken } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDate } from "@/lib/format";
import { useToast } from "@/lib/toast";
import { phoneError } from "@/lib/phone";

export default function ProfilePage() {
  const { user, setUser, signOut } = useAuth();
  const toast = useToast();

  // Initialised from the signed-in user rather than synced in an effect. The
  // portal layout guarantees a user before this renders, and after a save the
  // form already holds the new values, so there is nothing to sync back.
  const [name, setName] = useState(() => user?.name ?? "");
  const [phone, setPhone] = useState(() => user?.phone ?? "");
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileError, setProfileError] = useState<string | null>(null);

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [savingPassword, setSavingPassword] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);

  if (!user) return null;

  async function saveProfile(event: React.FormEvent) {
    event.preventDefault();
    setProfileError(null);
    setSavingProfile(true);
    try {
      const updated = await api.users.updateMe({ name, phone: phone || null });
      setUser(updated);
      toast.success("Profile updated");
    } catch (cause) {
      setProfileError(errorMessage(cause));
    } finally {
      setSavingProfile(false);
    }
  }

  async function changePassword(event: React.FormEvent) {
    event.preventDefault();
    setPasswordError(null);

    if (next !== confirm) {
      setPasswordError("The two new passwords do not match.");
      return;
    }

    setSavingPassword(true);
    try {
      await api.auth.changePassword(current, next);
      // Every token issued before the change is now invalid, including ours.
      setToken(null);
      toast.success("Password changed", "Sign in again with your new password.");
      signOut();
    } catch (cause) {
      setPasswordError(errorMessage(cause));
      setSavingPassword(false);
    }
  }

  return (
    <>
      <PageHeader
        title="My profile"
        description="Your own details. Role, reporting line and account status are not editable here — by anyone."
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* ------------------------------------------------ identity */}
        <Card className="lg:col-span-1">
          <CardBody className="flex flex-col items-center py-8 text-center">
            <Avatar name={user.name} size="xl" />
            <p className="mt-3.5 text-[16px] font-semibold text-content">{user.name}</p>
            <p className="text-[13px] text-muted">{user.email}</p>
            <div className="mt-3">
              <RoleBadge role={user.role} />
            </div>
            {user.title ? (
              <p className="mt-2 text-[12.5px] text-subtle">{user.title}</p>
            ) : null}
            <p className="mt-4 text-[11.5px] text-subtle">
              Member since {formatDate(user.created_at)}
            </p>
          </CardBody>
        </Card>

        <div className="space-y-4 lg:col-span-2">
          {/* --------------------------------------------- details */}
          <Card>
            <CardHeader className="block">
              <CardTitle>Your details</CardTitle>
              <CardDescription>Name and phone number are yours to change.</CardDescription>
            </CardHeader>
            <CardBody>
              <form onSubmit={saveProfile} className="space-y-4" noValidate>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <Field label="Full name" htmlFor="profile-name" required>
                    <Input
                      id="profile-name"
                      required
                      minLength={2}
                      value={name}
                      onChange={(event) => setName(event.target.value)}
                    />
                  </Field>
                  <Field
                    label="Phone"
                    htmlFor="profile-phone"
                    error={phoneError(phone) ?? undefined}
                  >
                    <Input
                      id="profile-phone"
                      value={phone}
                      onChange={(event) => setPhone(event.target.value)}
                    />
                  </Field>
                </div>

                <Field label="Email address" htmlFor="profile-email" hint="Ask an administrator to change this.">
                  <Input id="profile-email" value={user.email} disabled readOnly />
                </Field>

                {profileError ? <InlineError>{profileError}</InlineError> : null}

                <div className="flex justify-end">
                  <Button type="submit" loading={savingProfile}>
                    Save changes
                  </Button>
                </div>
              </form>
            </CardBody>
          </Card>

          {/* -------------------------------------------- password */}
          <Card>
            <CardHeader className="block">
              <CardTitle>Password</CardTitle>
              <CardDescription>
                Changing it signs you out of every device immediately.
              </CardDescription>
            </CardHeader>
            <CardBody>
              <form onSubmit={changePassword} className="space-y-4" noValidate>
                <Field label="Current password" htmlFor="current-password" required>
                  <Input
                    id="current-password"
                    type="password"
                    autoComplete="current-password"
                    required
                    icon={<Lock className="size-4" />}
                    value={current}
                    onChange={(event) => setCurrent(event.target.value)}
                  />
                </Field>

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <Field label="New password" htmlFor="new-password" required hint="At least 8 characters.">
                    <Input
                      id="new-password"
                      type="password"
                      autoComplete="new-password"
                      required
                      minLength={8}
                      value={next}
                      onChange={(event) => setNext(event.target.value)}
                    />
                  </Field>
                  <Field
                    label="Confirm new password"
                    htmlFor="confirm-password"
                    required
                    error={confirm.length > 0 && next !== confirm ? "These do not match." : null}
                  >
                    <Input
                      id="confirm-password"
                      type="password"
                      autoComplete="new-password"
                      required
                      value={confirm}
                      onChange={(event) => setConfirm(event.target.value)}
                      invalid={confirm.length > 0 && next !== confirm}
                    />
                  </Field>
                </div>

                {passwordError ? <InlineError>{passwordError}</InlineError> : null}

                <div className="flex justify-end">
                  <Button
                    type="submit"
                    variant="secondary"
                    loading={savingPassword}
                    disabled={!current || next.length < 8 || next !== confirm}
                  >
                    Change password
                  </Button>
                </div>
              </form>
            </CardBody>
          </Card>

          {/* ------------------------------------ what you cannot do */}
          <Card>
            <CardBody className="flex items-start gap-3">
              <span className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg bg-surface-2 text-subtle">
                <ShieldCheck className="size-4" aria-hidden />
              </span>
              <div>
                <p className="text-[13.5px] font-medium text-content">
                  Why you cannot change your own role
                </p>
                <p className="mt-1 text-[12.5px] leading-relaxed text-muted">
                  Self-service is a separate, deliberately smaller path than
                  administration. Nobody — including the Super Admin — can change their
                  own role, manager or active status, which is what stops the
                  installation locking itself out or anyone quietly promoting
                  themselves.
                </p>
                <p className="mt-2 flex items-start gap-1.5 text-[12px] text-subtle">
                  <Info className="mt-px size-3.5 shrink-0" aria-hidden />
                  Ask an administrator if any of those need to change.
                </p>
              </div>
            </CardBody>
          </Card>
        </div>
      </div>
    </>
  );
}
