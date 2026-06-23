import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Building2,
  FileSearch,
  MessageSquare,
  Trash2,
  UserPlus,
  Users,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import clsx from "clsx";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useConfirm } from "../components/ui/ConfirmDialog";
import { toast } from "../lib/toast";

type Member = { user_id: string; email: string; role: string };

type FeedbackDefaults = {
  mr_comment: boolean;
  commit_comment: boolean;
  issue: boolean;
  status_check: boolean;
};

type TenantCurrent = {
  id: string;
  name: string;
  slug: string;
  feedback_defaults: FeedbackDefaults;
};

type TabId = "general" | "team" | "audit";

export default function SettingsPage() {
  const { t } = useTranslation();
  const { me } = useAuth();
  const qc = useQueryClient();
  const isAdmin = me?.role === "owner" || me?.role === "admin";

  const [searchParams, setSearchParams] = useSearchParams();
  const tabFromUrl = searchParams.get("tab") as TabId | null;

  const tabs: Array<{ id: TabId; label: string; visible: boolean }> = useMemo(
    () => [
      { id: "general", label: t("settings.tabs.general"), visible: true },
      { id: "team", label: t("settings.tabs.team"), visible: true },
      { id: "audit", label: t("settings.tabs.audit"), visible: !!isAdmin },
    ],
    [t, isAdmin],
  );

  const activeTab: TabId = (() => {
    if (tabFromUrl && tabs.some((tab) => tab.id === tabFromUrl && tab.visible)) {
      return tabFromUrl;
    }
    return "general";
  })();

  const tenant = useQuery({
    queryKey: ["tenant-current"],
    queryFn: () => api<TenantCurrent>("/api/tenants/current"),
  });

  const members = useQuery({
    queryKey: ["members"],
    queryFn: () => api<Member[]>("/api/tenants/current/members"),
    enabled: activeTab === "team",
  });

  const [tenantName, setTenantName] = useState(me?.tenant.name ?? "");

  async function rename() {
    try {
      const res = await api<TenantCurrent>("/api/tenants/current", {
        method: "POST",
        body: { name: tenantName },
      });
      toast.success(t("settings.renamed", { name: res.name }));
      await qc.invalidateQueries({ queryKey: ["me"] });
      await qc.invalidateQueries({ queryKey: ["tenant-current"] });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  async function updateFeedbackDefault(
    key: keyof FeedbackDefaults,
    value: boolean,
  ) {
    try {
      await api<TenantCurrent>("/api/tenants/current", {
        method: "PATCH",
        body: { feedback_defaults: { [key]: value } },
      });
      await qc.invalidateQueries({ queryKey: ["tenant-current"] });
      toast.success(t("settings.feedback_saved"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  function setActiveTab(id: TabId) {
    if (id === "general") {
      searchParams.delete("tab");
    } else {
      searchParams.set("tab", id);
    }
    setSearchParams(searchParams, { replace: true });
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">{t("settings.title")}</h1>
        <p className="text-sm text-slate-500">{t("settings.subtitle")}</p>
      </header>

      <div
        role="tablist"
        aria-label={t("settings.title")}
        className="flex flex-wrap gap-1 border-b border-slate-200 dark:border-slate-800"
      >
        {tabs
          .filter((tab) => tab.visible)
          .map((tab) => (
            <button
              key={tab.id}
              role="tab"
              type="button"
              aria-selected={activeTab === tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={clsx(
                "-mb-px border-b-2 px-4 py-2 text-sm font-medium transition-colors",
                activeTab === tab.id
                  ? "border-brand-600 text-brand-700 dark:text-brand-400"
                  : "border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200",
              )}
            >
              {tab.label}
            </button>
          ))}
      </div>

      {activeTab === "general" && (
        <GeneralTab
          tenantName={tenantName}
          onTenantNameChange={setTenantName}
          onRename={rename}
          tenant={tenant.data}
          isAdmin={isAdmin}
          onUpdateFeedbackDefault={updateFeedbackDefault}
        />
      )}

      {activeTab === "team" && (
        <TeamTab
          members={members.data ?? []}
          membersLoading={members.isLoading}
          currentUserId={me?.user.id ?? null}
          isAdmin={isAdmin}
          onRefetchMembers={async () => {
            await members.refetch();
          }}
        />
      )}

      {activeTab === "audit" && isAdmin && <AuditTab />}
    </div>
  );
}

function GeneralTab({
  tenantName,
  onTenantNameChange,
  onRename,
  tenant,
  isAdmin,
  onUpdateFeedbackDefault,
}: {
  tenantName: string;
  onTenantNameChange: (value: string) => void;
  onRename: () => void;
  tenant: TenantCurrent | undefined;
  isAdmin: boolean;
  onUpdateFeedbackDefault: (
    key: keyof FeedbackDefaults,
    value: boolean,
  ) => void | Promise<void>;
}) {
  const { t } = useTranslation();

  return (
    <div className="space-y-6">
      <section className="card space-y-3">
        <div className="flex items-center gap-2">
          <Building2 className="h-5 w-5 text-brand-600" aria-hidden />
          <h2 className="text-lg font-semibold">{t("settings.team")}</h2>
        </div>
        <p className="text-sm text-slate-500">{t("settings.team_desc")}</p>
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-[16rem] flex-1">
            <label className="label" htmlFor="settings-team-name">
              {t("settings.team_name")}
            </label>
            <input
              id="settings-team-name"
              className="input"
              value={tenantName}
              onChange={(e) => onTenantNameChange(e.target.value)}
            />
          </div>
          <button
            className="btn-primary"
            onClick={onRename}
            disabled={!tenantName.trim()}
          >
            {t("common.save")}
          </button>
        </div>
      </section>

      {isAdmin && (
        <section className="card space-y-4">
          <div>
            <div className="flex items-center gap-2">
              <MessageSquare className="h-5 w-5 text-brand-600" aria-hidden />
              <h2 className="text-lg font-semibold">
                {t("settings.feedback_title")}
              </h2>
            </div>
            <p className="mt-1 text-sm text-slate-500">
              {t("settings.feedback_desc")}
            </p>
          </div>
          {tenant ? (
            <div className="flex flex-col gap-2">
              {(
                ["mr_comment", "commit_comment", "issue", "status_check"] as const
              ).map((key) => (
                <label
                  key={key}
                  className="flex items-start gap-3 rounded-lg border border-slate-200 p-3 text-sm transition-colors hover:border-brand-300 dark:border-slate-800 dark:hover:border-brand-600"
                >
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={tenant.feedback_defaults[key]}
                    onChange={(e) =>
                      void onUpdateFeedbackDefault(key, e.target.checked)
                    }
                  />
                  <div>
                    <div className="font-medium">
                      {t(`settings.feedback_${key}`)}
                    </div>
                    <div className="text-xs text-slate-500">
                      {t(`settings.feedback_${key}_hint`)}
                    </div>
                  </div>
                </label>
              ))}
              <p className="text-xs text-slate-500">
                {t("settings.feedback_kill_switch")}
              </p>
            </div>
          ) : (
            <p className="text-sm text-slate-500">{t("common.loading")}</p>
          )}
        </section>
      )}
    </div>
  );
}

function TeamTab({
  members,
  membersLoading,
  currentUserId,
  isAdmin,
  onRefetchMembers,
}: {
  members: Member[];
  membersLoading: boolean;
  currentUserId: string | null;
  isAdmin: boolean;
  onRefetchMembers: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const confirm = useConfirm();

  const [inviteEmail, setInviteEmail] = useState("");
  const [invitePassword, setInvitePassword] = useState("");
  const [inviteRole, setInviteRole] = useState("member");
  const [inviting, setInviting] = useState(false);

  async function invite() {
    setInviting(true);
    try {
      await api("/api/tenants/current/invites", {
        method: "POST",
        body: { email: inviteEmail, password: invitePassword, role: inviteRole },
      });
      toast.success(t("settings.invite_sent"));
      setInviteEmail("");
      setInvitePassword("");
      await onRefetchMembers();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("settings.invite_failed"));
    } finally {
      setInviting(false);
    }
  }

  async function removeMember(userId: string) {
    const ok = await confirm({
      title: t("settings.remove_member_title"),
      message: t("settings.remove_member_msg"),
      confirmLabel: t("common.remove"),
      tone: "danger",
    });
    if (!ok) return;
    try {
      await api(`/api/tenants/current/members/${userId}`, { method: "DELETE" });
      toast.success(t("settings.member_removed"));
      await onRefetchMembers();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  return (
    <div className="space-y-6">
      <section className="card space-y-4">
        <div>
          <div className="flex items-center gap-2">
            <Users className="h-5 w-5 text-brand-600" aria-hidden />
            <h2 className="text-lg font-semibold">{t("settings.members")}</h2>
          </div>
          <p className="mt-1 text-sm text-slate-500">
            {t("settings.team_members_desc")}
          </p>
        </div>

        {isAdmin && (
          <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50/60 p-3 dark:border-slate-700 dark:bg-slate-900/40">
            <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
              <UserPlus className="h-4 w-4" />
              {t("settings.invite_teammate")}
            </div>
            <div className="flex flex-wrap items-end gap-2">
              <div className="min-w-[16rem] flex-1">
                <label className="label" htmlFor="settings-invite-email">
                  {t("common.email")}
                </label>
                <input
                  id="settings-invite-email"
                  className="input"
                  type="email"
                  value={inviteEmail}
                  onChange={(e) => setInviteEmail(e.target.value)}
                  placeholder="alice@example.com"
                />
              </div>
              <div className="min-w-[12rem] flex-1">
                <label className="label" htmlFor="settings-invite-password">
                  {t("auth.password")}
                </label>
                <input
                  id="settings-invite-password"
                  className="input"
                  type="password"
                  value={invitePassword}
                  onChange={(e) => setInvitePassword(e.target.value)}
                  autoComplete="new-password"
                  minLength={8}
                />
              </div>
              <div>
                <label className="label" htmlFor="settings-invite-role">
                  {t("common.role")}
                </label>
                <select
                  id="settings-invite-role"
                  className="input"
                  value={inviteRole}
                  onChange={(e) => setInviteRole(e.target.value)}
                >
                  <option value="member">{t("settings.member")}</option>
                  <option value="admin">{t("settings.admin")}</option>
                </select>
              </div>
              <button
                className="btn-primary"
                onClick={invite}
                disabled={!inviteEmail || invitePassword.length < 8 || inviting}
              >
                {inviting ? t("settings.invite_sending") : t("settings.invite")}
              </button>
            </div>
          </div>
        )}

        {membersLoading ? (
          <p className="text-sm text-slate-500">{t("common.loading")}</p>
        ) : members.length === 0 ? (
          <p className="text-sm text-slate-500">{t("settings.no_members")}</p>
        ) : (
          <ul className="space-y-2">
            {members.map((m) => (
              <li
                key={m.user_id}
                className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-3 transition-colors hover:border-brand-300 dark:border-slate-800 dark:bg-slate-900 dark:hover:border-brand-600"
              >
                <Avatar email={m.email} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{m.email}</div>
                  <div className="text-xs text-slate-500">
                    {m.user_id === currentUserId ? (
                      <span>{t("settings.you")}</span>
                    ) : null}
                  </div>
                </div>
                <RoleBadge role={m.role} />
                {isAdmin && m.user_id !== currentUserId && (
                  <button
                    className="btn-secondary"
                    onClick={() => removeMember(m.user_id)}
                    aria-label={t("common.remove")}
                    title={t("common.remove")}
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function Avatar({ email }: { email: string }) {
  const initials = email
    .split("@")[0]
    .split(/[._-]/)
    .filter(Boolean)
    .slice(0, 2)
    .map((piece) => piece[0]?.toUpperCase() ?? "")
    .join("") || email[0]?.toUpperCase() || "?";
  return (
    <div
      className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full bg-brand-100 text-xs font-semibold text-brand-700 dark:bg-brand-900/40 dark:text-brand-200"
      aria-hidden
    >
      {initials}
    </div>
  );
}

function RoleBadge({ role }: { role: string }) {
  const tone =
    role === "owner"
      ? "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200"
      : role === "admin"
        ? "bg-brand-100 text-brand-700 dark:bg-brand-900/40 dark:text-brand-200"
        : "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300";
  return (
    <span className={clsx("rounded px-2 py-0.5 text-xs font-medium", tone)}>
      {role}
    </span>
  );
}

function AuditTab() {
  const { t } = useTranslation();

  return (
    <section className="card flex flex-wrap items-start justify-between gap-3">
      <div>
        <div className="flex items-center gap-2">
          <FileSearch className="h-5 w-5 text-brand-600" aria-hidden />
          <h2 className="text-lg font-semibold">{t("settings.audit_log")}</h2>
        </div>
        <p className="mt-1 max-w-xl text-sm text-slate-500">
          {t("settings.audit_log_desc")}
        </p>
      </div>
      <Link
        to="/dashboard/audit"
        className="btn-primary inline-flex items-center gap-2"
      >
        <FileSearch className="h-4 w-4" />
        {t("settings.audit_log_open")}
      </Link>
    </section>
  );
}
