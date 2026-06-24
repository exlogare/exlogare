import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import * as Dialog from "@radix-ui/react-dialog";
import {
  AlertTriangle,
  ChevronDown,
  Github,
  Hash,
  Home,
  KeyRound,
  Link2,
  Loader2,
  MessageSquare,
  Plus,
  RefreshCw,
  Send,
  Settings as SettingsIcon,
  Trash2,
  X,
} from "lucide-react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import clsx from "clsx";
import { api } from "../lib/api";
import { toast } from "../lib/toast";
import { useConfirm } from "../components/ui/ConfirmDialog";
import AddChannelDialog, {
  ChannelKind,
} from "../components/AddChannelDialog";
import SyncProjectsDialog from "../components/SyncProjectsDialog";
import ApiKeysDialog from "../components/ApiKeysDialog";
import OutboundWebhooks from "../components/OutboundWebhooks";
import type {
  BitbucketConnection,
  GitFlicConnection,
  FeedbackChannelKey,
  FeedbackPolicy,
  GitHubConnection,
  GitLabConnection,
  NotificationConnection,
} from "../lib/types";
import { useCapabilities } from "../lib/capabilities";
import { useAuth } from "../lib/auth";

const CHANNEL_ORDER: ChannelKind[] = ["telegram", "slack", "matrix"];

export default function IntegrationsPage() {
  const { t } = useTranslation();
  const confirm = useConfirm();
  const ci = useQuery({
    queryKey: ["integrations", "gitlab"],
    queryFn: () => api<GitLabConnection[]>("/api/integrations/gitlab/connections"),
  });
  const ciGh = useQuery({
    queryKey: ["integrations", "github"],
    queryFn: () => api<GitHubConnection[]>("/api/integrations/github/connections"),
  });
  const ciBb = useQuery({
    queryKey: ["integrations", "bitbucket"],
    queryFn: () =>
      api<BitbucketConnection[]>("/api/integrations/bitbucket/connections"),
  });
  const ciGf = useQuery({
    queryKey: ["integrations", "gitflic"],
    queryFn: () =>
      api<GitFlicConnection[]>("/api/integrations/gitflic/connections"),
  });
  const notify = useQuery({
    queryKey: ["integrations", "notifications"],
    queryFn: () => api<NotificationConnection[]>("/api/integrations/notifications"),
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      const awaitingLink = data.some(
        (c) => c.channel === "telegram" && !c.target,
      );
      return awaitingLink ? 3_000 : false;
    },
    refetchIntervalInBackground: false,
  });
  const caps = useCapabilities();
  const { me } = useAuth();
  const isAdmin = me?.role === "owner" || me?.role === "admin";
  const maxApiKeys = caps.data?.max_api_keys ?? null;
  const currentApiKeys = caps.data?.current_api_keys ?? 0;

  const [addDialog, setAddDialog] = useState<ChannelKind | null>(null);
  const [syncOpen, setSyncOpen] = useState(false);
  const [syncOpenGh, setSyncOpenGh] = useState(false);
  const [syncOpenBb, setSyncOpenBb] = useState(false);
  const [oauthOpen, setOauthOpen] = useState(false);
  const [oauthOpenGh, setOauthOpenGh] = useState(false);
  const [oauthOpenBb, setOauthOpenBb] = useState(false);
  const [apiKeysOpen, setApiKeysOpen] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);
  const [retrying, setRetrying] = useState<string | null>(null);

  async function retryWebhook(id: string) {
    setRetrying(id);
    try {
      const res = await api<{ ok: boolean; webhook_registered: boolean; detail: string | null }>(
        `/api/integrations/telegram/${id}/retry-webhook`,
        { method: "POST" },
      );
      if (res.ok) {
        toast.success(t("integrations.webhook_retry_ok"));
      } else {
        toast.error({
          title: t("integrations.webhook_retry_failed"),
          message: res.detail ?? undefined,
        });
      }
      await notify.refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    } finally {
      setRetrying(null);
    }
  }

  async function testChannel(id: string) {
    setTesting(id);
    try {
      const res = await api<{ ok: boolean; detail: string | null }>(
        `/api/integrations/notifications/${id}/test`,
        { method: "POST" },
      );
      if (res.ok) {
        toast.success(t("integrations.test_sent"));
      } else {
        toast.error({
          title: t("integrations.test_failed"),
          message: res.detail ?? undefined,
        });
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    } finally {
      setTesting(null);
    }
  }

  async function removeChannel(id: string) {
    const ok = await confirm({
      title: t("integrations.remove_channel_confirm_title"),
      message: t("integrations.remove_channel_confirm_msg"),
      confirmLabel: t("common.remove"),
      tone: "danger",
    });
    if (!ok) return;
    try {
      await api(`/api/integrations/notifications/${id}`, { method: "DELETE" });
      toast.success(t("integrations.channel_removed"));
      await notify.refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  async function enableGitlab(id: string) {
    try {
      await api(`/api/integrations/gitlab/watch/${id}`, {
        method: "PATCH",
        body: { enabled: true },
      });
      toast.success(t("integrations.gitlab_enabled"));
      await ci.refetch();
      await caps.refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  async function disableGitlab(id: string) {
    const ok = await confirm({
      title: t("integrations.disable_gitlab_confirm_title"),
      message: t("integrations.disable_gitlab_confirm_msg"),
      confirmLabel: t("common.disable"),
      tone: "warning",
    });
    if (!ok) return;
    try {
      await api(`/api/integrations/gitlab/watch/${id}`, { method: "DELETE" });
      toast.success(t("integrations.gitlab_disabled"));
      await ci.refetch();
      await caps.refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  async function changeGitlabMode(id: string, newMode: string) {
    try {
      await api(`/api/integrations/gitlab/watch/${id}`, {
        method: "PATCH",
        body: { mode: newMode },
      });
      toast.success(t("integrations.gitlab_mode_changed"));
      await ci.refetch();
      await caps.refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  async function updateFeedbackOverride(
    connId: string,
    key: FeedbackChannelKey,
    value: boolean | "inherit",
  ) {
    const fieldKey =
      key === "mr_comment"
        ? "feedback_mr_comment"
        : key === "commit_comment"
          ? "feedback_commit_comment"
          : key === "status_check"
            ? "feedback_status_check"
            : "feedback_issue";
    try {
      await api(`/api/integrations/gitlab/watch/${connId}`, {
        method: "PATCH",
        body: { [fieldKey]: value },
      });
      await ci.refetch();
      toast.success(t("integrations.feedback_saved"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  async function resetFeedbackOverride(connId: string) {
    try {
      await api(`/api/integrations/gitlab/watch/${connId}`, {
        method: "PATCH",
        body: {
          feedback_mr_comment: "inherit",
          feedback_commit_comment: "inherit",
          feedback_issue: "inherit",
          feedback_status_check: "inherit",
        },
      });
      await ci.refetch();
      toast.success(t("integrations.feedback_reset"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  function toGitLabConnectionShape(
    c: GitHubConnection,
  ): GitLabConnection {
    return {
      ...c,
      gitlab_user: c.github_user
        ? (c.github_user as Record<string, unknown>)
        : null,
    };
  }

  function bitbucketToGitLabShape(c: BitbucketConnection): GitLabConnection {
    return {
      ...c,
      gitlab_user: c.bitbucket_user
        ? (c.bitbucket_user as Record<string, unknown>)
        : null,
    };
  }

  async function enableGithub(id: string) {
    try {
      await api(`/api/integrations/github/watch/${id}`, {
        method: "PATCH",
        body: { enabled: true },
      });
      toast.success(t("integrations.github_enabled"));
      void ciGh.refetch();
      void caps.refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  async function disableGithub(id: string) {
    const ok = await confirm({
      title: t("integrations.disable_github_confirm_title"),
      message: t("integrations.disable_github_confirm_msg"),
      confirmLabel: t("common.disable"),
      tone: "warning",
    });
    if (!ok) return;
    try {
      await api(`/api/integrations/github/watch/${id}`, { method: "DELETE" });
      toast.success(t("integrations.github_disabled"));
      void ciGh.refetch();
      void caps.refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  async function changeGithubMode(id: string, newMode: string) {
    try {
      await api(`/api/integrations/github/watch/${id}`, {
        method: "PATCH",
        body: { mode: newMode },
      });
      toast.success(t("integrations.github_mode_changed"));
      void ciGh.refetch();
      void caps.refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  async function updateGithubFeedback(
    connId: string,
    key: FeedbackChannelKey,
    value: boolean | "inherit",
  ) {
    const fieldKey =
      key === "mr_comment"
        ? "feedback_mr_comment"
        : key === "commit_comment"
          ? "feedback_commit_comment"
          : key === "status_check"
            ? "feedback_status_check"
            : "feedback_issue";
    try {
      await api(`/api/integrations/github/watch/${connId}`, {
        method: "PATCH",
        body: { [fieldKey]: value },
      });
      void ciGh.refetch();
      toast.success(t("integrations.feedback_saved"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  async function resetGithubFeedback(connId: string) {
    try {
      await api(`/api/integrations/github/watch/${connId}`, {
        method: "PATCH",
        body: {
          feedback_mr_comment: "inherit",
          feedback_commit_comment: "inherit",
          feedback_issue: "inherit",
          feedback_status_check: "inherit",
        },
      });
      void ciGh.refetch();
      toast.success(t("integrations.feedback_reset"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  async function deleteGithub(id: string) {
    const conn = (ciGh.data ?? []).find((c) => c.id === id);
    const isBase = conn?.external_project_id === null;
    const childCount = isBase
      ? (ciGh.data ?? []).filter(
          (c) =>
            c.external_project_id !== null && c.base_url === conn?.base_url,
        ).length
      : 0;
    const ok = await confirm({
      title: t("integrations.delete_github_confirm_title"),
      message:
        isBase && childCount > 0
          ? t("integrations.delete_github_instance_confirm_msg", {
              count: childCount,
            })
          : t("integrations.delete_github_confirm_msg"),
      confirmLabel: t("common.delete"),
      tone: "danger",
    });
    if (!ok) return;
    try {
      await api(`/api/integrations/github/watch/${id}?purge=true`, {
        method: "DELETE",
      });
      toast.success(t("integrations.github_deleted"));
      void ciGh.refetch();
      void caps.refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  async function enableBitbucket(id: string) {
    try {
      await api(`/api/integrations/bitbucket/watch/${id}`, {
        method: "PATCH",
        body: { enabled: true },
      });
      toast.success(t("integrations.bitbucket_enabled"));
      void ciBb.refetch();
      void caps.refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  async function disableBitbucket(id: string) {
    const ok = await confirm({
      title: t("integrations.disable_bitbucket_confirm_title"),
      message: t("integrations.disable_bitbucket_confirm_msg"),
      confirmLabel: t("common.disable"),
      tone: "warning",
    });
    if (!ok) return;
    try {
      await api(`/api/integrations/bitbucket/watch/${id}`, { method: "DELETE" });
      toast.success(t("integrations.bitbucket_disabled"));
      void ciBb.refetch();
      void caps.refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  async function changeBitbucketMode(id: string, newMode: string) {
    try {
      await api(`/api/integrations/bitbucket/watch/${id}`, {
        method: "PATCH",
        body: { mode: newMode },
      });
      toast.success(t("integrations.bitbucket_mode_changed"));
      void ciBb.refetch();
      void caps.refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  async function updateBitbucketFeedback(
    connId: string,
    key: FeedbackChannelKey,
    value: boolean | "inherit",
  ) {
    const fieldKey =
      key === "mr_comment"
        ? "feedback_mr_comment"
        : key === "commit_comment"
          ? "feedback_commit_comment"
          : key === "status_check"
            ? "feedback_status_check"
            : "feedback_issue";
    try {
      await api(`/api/integrations/bitbucket/watch/${connId}`, {
        method: "PATCH",
        body: { [fieldKey]: value },
      });
      void ciBb.refetch();
      toast.success(t("integrations.feedback_saved"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  async function resetBitbucketFeedback(connId: string) {
    try {
      await api(`/api/integrations/bitbucket/watch/${connId}`, {
        method: "PATCH",
        body: {
          feedback_mr_comment: "inherit",
          feedback_commit_comment: "inherit",
          feedback_issue: "inherit",
          feedback_status_check: "inherit",
        },
      });
      void ciBb.refetch();
      toast.success(t("integrations.feedback_reset"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  async function deleteBitbucket(id: string) {
    const conn = (ciBb.data ?? []).find((c) => c.id === id);
    const isBase = conn?.external_project_id === null;
    const childCount = isBase
      ? (ciBb.data ?? []).filter(
          (c) =>
            c.external_project_id !== null && c.base_url === conn?.base_url,
        ).length
      : 0;
    const ok = await confirm({
      title: t("integrations.delete_bitbucket_confirm_title"),
      message:
        isBase && childCount > 0
          ? t("integrations.delete_bitbucket_instance_confirm_msg", {
              count: childCount,
            })
          : t("integrations.delete_bitbucket_confirm_msg"),
      confirmLabel: t("common.delete"),
      tone: "danger",
    });
    if (!ok) return;
    try {
      await api(`/api/integrations/bitbucket/watch/${id}?purge=true`, {
        method: "DELETE",
      });
      toast.success(t("integrations.bitbucket_deleted"));
      void ciBb.refetch();
      void caps.refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  async function deleteGitlab(id: string) {
    const conn = (ci.data ?? []).find((c) => c.id === id);
    const isBase = conn?.external_project_id === null;
    const childCount = isBase
      ? (ci.data ?? []).filter(
          (c) =>
            c.external_project_id !== null && c.base_url === conn?.base_url,
        ).length
      : 0;
    const ok = await confirm({
      title: t("integrations.delete_gitlab_confirm_title"),
      message:
        isBase && childCount > 0
          ? t("integrations.delete_gitlab_instance_confirm_msg", {
              count: childCount,
            })
          : t("integrations.delete_gitlab_confirm_msg"),
      confirmLabel: t("common.delete"),
      tone: "danger",
    });
    if (!ok) return;
    try {
      await api(`/api/integrations/gitlab/watch/${id}?purge=true`, {
        method: "DELETE",
      });
      toast.success(t("integrations.gitlab_deleted"));
      await ci.refetch();
      await caps.refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  const gitlabConns = ci.data ?? [];
  const baseConns = gitlabConns.filter((c) => c.external_project_id === null);
  const projectConns = gitlabConns.filter((c) => c.external_project_id !== null);
  const hasBaseConnection = baseConns.length > 0;
  const maxGitlabRepos = caps.data?.max_gitlab_repos ?? null;
  const enabledGitlabCount = caps.data?.current_gitlab_repos ?? 0;
  const repoLimitKnown = maxGitlabRepos != null;
  const canEnableAnotherRepo =
    !repoLimitKnown || enabledGitlabCount < maxGitlabRepos;

  const baseOauthConnection = useMemo(
    () =>
      gitlabConns.find(
        (c) => c.external_project_id === null && c.oauth_app_editable === true,
      ),
    [gitlabConns],
  );
  const availableModes = caps.data?.gitlab_modes ?? [];
  const availableGithubModes = caps.data?.github_modes ?? [];

  const githubConns = ciGh.data ?? [];
  const baseGhConns = githubConns.filter((c) => c.external_project_id === null);
  const projectGhConns = githubConns.filter((c) => c.external_project_id !== null);
  const hasGhBase = baseGhConns.length > 0;
  const maxGhRepos = caps.data?.max_github_repos ?? null;
  const enabledGhCount = caps.data?.current_github_repos ?? 0;
  const canEnableAnotherGhRepo =
    maxGhRepos == null || enabledGhCount < maxGhRepos;
  const baseOauthConnectionGh = useMemo(
    () =>
      githubConns.find(
        (c) => c.external_project_id === null && c.oauth_app_editable === true,
      ),
    [githubConns],
  );

  const bitbucketConns = ciBb.data ?? [];
  const baseBbConns = bitbucketConns.filter(
    (c) => c.external_project_id === null,
  );
  const projectBbConns = bitbucketConns.filter(
    (c) => c.external_project_id !== null,
  );
  const hasBbCloudBase = baseBbConns.some((c) =>
    c.base_url.startsWith("https://bitbucket.org"),
  );
  const maxBbRepos = caps.data?.max_bitbucket_repos ?? null;
  const enabledBbCount = caps.data?.current_bitbucket_repos ?? 0;
  const canEnableAnotherBbRepo =
    maxBbRepos == null || enabledBbCount < maxBbRepos;
  const baseOauthConnectionBb = useMemo(
    () =>
      bitbucketConns.find(
        (c) => c.external_project_id === null && c.oauth_app_editable === true,
      ),
    [bitbucketConns],
  );
  const availableBitbucketModes = caps.data?.bitbucket_modes ?? [];

  const channels = notify.data ?? [];
  const grouped: Record<ChannelKind, NotificationConnection[]> = {
    telegram: [],
    slack: [],
    matrix: [],
  };
  for (const c of channels) {
    const kind = c.channel as ChannelKind;
    if (kind in grouped) grouped[kind].push(c);
  }

  return (
    <div className="space-y-8">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold">{t("integrations.title")}</h1>
          <p className="text-sm text-slate-500">{t("integrations.subtitle")}</p>
        </div>
        <Link to="/onboarding" className="btn-primary">
          <Plus className="h-4 w-4" /> {t("integrations.add_new")}
        </Link>
      </header>

      <section className="card">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Link2 className="h-5 w-5 text-brand-600" />
            <h2 className="text-lg font-semibold">{t("integrations.gitlab")}</h2>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {baseOauthConnection && (
              <button
                className="btn-secondary inline-flex items-center gap-1"
                onClick={() => setOauthOpen(true)}
              >
                <KeyRound className="h-4 w-4" />
                {t("integrations.oauth_app_open")}
              </button>
            )}
            {hasBaseConnection && (
              <button
                className="btn-secondary inline-flex items-center gap-1"
                onClick={() => setSyncOpen(true)}
              >
                <RefreshCw className="h-4 w-4" />
                {t("integrations.sync_projects")}
              </button>
            )}
          </div>
        </div>

        <RepoLimitBar current={enabledGitlabCount} max={maxGitlabRepos} />

        {ci.isLoading ? (
          <p className="mt-4 text-sm text-slate-500">{t("common.loading")}</p>
        ) : gitlabConns.length === 0 ? (
          <p className="mt-4 text-sm text-slate-500">
            {t("integrations.gitlab_empty")}
          </p>
        ) : (
          <div className="mt-4 space-y-3">
            {baseConns.map((c) => (
              <GitLabBaseRow
                key={c.id}
                connection={c}
                onDelete={() => deleteGitlab(c.id)}
              />
            ))}
            {projectConns.map((c) => (
              <GitLabProjectCard
                key={c.id}
                connection={c}
                availableModes={availableModes}
                canEnableAnotherRepo={canEnableAnotherRepo}
                onEnable={() => enableGitlab(c.id)}
                onDisable={() => disableGitlab(c.id)}
                onDelete={() => deleteGitlab(c.id)}
                onModeChange={(mode) => changeGitlabMode(c.id, mode)}
                onFeedbackChange={updateFeedbackOverride}
                onFeedbackReset={resetFeedbackOverride}
              />
            ))}
          </div>
        )}
      </section>

      <section className="card">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Link2 className="h-5 w-5 text-slate-800 dark:text-slate-100" />
            <h2 className="text-lg font-semibold">{t("integrations.github")}</h2>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {baseOauthConnectionGh && (
              <button
                type="button"
                className="btn-secondary inline-flex items-center gap-1"
                onClick={() => setOauthOpenGh(true)}
              >
                <KeyRound className="h-4 w-4" />
                {t("integrations.oauth_app_open")}
              </button>
            )}
            {hasGhBase && (
              <button
                type="button"
                className="btn-secondary inline-flex items-center gap-1"
                onClick={() => setSyncOpenGh(true)}
              >
                <RefreshCw className="h-4 w-4" />
                {t("integrations.sync_projects")}
              </button>
            )}
          </div>
        </div>
        <RepoLimitBar
          current={enabledGhCount}
          max={maxGhRepos}
          labelKey="integrations.github_repo_limit_label"
          ariaKey="integrations.github_repo_limit_bar_aria"
        />
        {ciGh.isLoading ? (
          <p className="mt-4 text-sm text-slate-500">{t("common.loading")}</p>
        ) : githubConns.length === 0 ? (
          <p className="mt-4 text-sm text-slate-500">
            {t("integrations.github_empty")}
          </p>
        ) : (
          <div className="mt-4 space-y-3">
            {baseGhConns.map((c) => (
              <GitLabBaseRow
                key={c.id}
                connection={toGitLabConnectionShape(c)}
                onDelete={() => deleteGithub(c.id)}
              />
            ))}
            {projectGhConns.map((c) => (
              <GitLabProjectCard
                key={c.id}
                connection={toGitLabConnectionShape(c)}
                availableModes={availableGithubModes}
                canEnableAnotherRepo={canEnableAnotherGhRepo}
                onEnable={() => enableGithub(c.id)}
                onDisable={() => disableGithub(c.id)}
                onDelete={() => deleteGithub(c.id)}
                onModeChange={(mode) => changeGithubMode(c.id, mode)}
                onFeedbackChange={updateGithubFeedback}
                onFeedbackReset={resetGithubFeedback}
              />
            ))}
          </div>
        )}
      </section>

      <section className="card">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Link2 className="h-5 w-5 text-[#0052CC]" />
            <h2 className="text-lg font-semibold">
              {t("integrations.bitbucket")}
            </h2>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {baseOauthConnectionBb && (
              <button
                type="button"
                className="btn-secondary inline-flex items-center gap-1"
                onClick={() => setOauthOpenBb(true)}
              >
                <KeyRound className="h-4 w-4" />
                {t("integrations.oauth_app_open")}
              </button>
            )}
            {hasBbCloudBase && (
              <button
                type="button"
                className="btn-secondary inline-flex items-center gap-1"
                onClick={() => setSyncOpenBb(true)}
              >
                <RefreshCw className="h-4 w-4" />
                {t("integrations.sync_projects")}
              </button>
            )}
          </div>
        </div>
        <RepoLimitBar
          current={enabledBbCount}
          max={maxBbRepos}
          labelKey="integrations.bitbucket_repo_limit_label"
          ariaKey="integrations.bitbucket_repo_limit_bar_aria"
        />
        {ciBb.isLoading ? (
          <p className="mt-4 text-sm text-slate-500">{t("common.loading")}</p>
        ) : bitbucketConns.length === 0 ? (
          <p className="mt-4 text-sm text-slate-500">
            {t("integrations.bitbucket_empty")}
          </p>
        ) : (
          <div className="mt-4 space-y-3">
            {baseBbConns.map((c) => (
              <GitLabBaseRow
                key={c.id}
                connection={bitbucketToGitLabShape(c)}
                onDelete={() => deleteBitbucket(c.id)}
              />
            ))}
            {projectBbConns.map((c) => (
              <GitLabProjectCard
                key={c.id}
                connection={bitbucketToGitLabShape(c)}
                availableModes={availableBitbucketModes}
                canEnableAnotherRepo={canEnableAnotherBbRepo}
                onEnable={() => enableBitbucket(c.id)}
                onDisable={() => disableBitbucket(c.id)}
                onDelete={() => deleteBitbucket(c.id)}
                onModeChange={(mode) => changeBitbucketMode(c.id, mode)}
                onFeedbackChange={updateBitbucketFeedback}
                onFeedbackReset={resetBitbucketFeedback}
              />
            ))}
          </div>
        )}
      </section>

      <GitFlicSection
        connections={ciGf.data ?? []}
        loading={ciGf.isLoading}
        refresh={() => ciGf.refetch()}
      />

      <section className="card">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <MessageSquare className="h-5 w-5 text-brand-600" />
              <h2 className="text-lg font-semibold">{t("integrations.messengers")}</h2>
            </div>
            <p className="mt-1 text-sm text-slate-500">
              {t("integrations.messengers_subtitle")}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              className="btn-secondary"
              onClick={() => setAddDialog("telegram")}
            >
              <Plus className="h-4 w-4" /> {t("integrations.add_telegram")}
            </button>
            <button
              className="btn-secondary"
              onClick={() => setAddDialog("slack")}
            >
              <Plus className="h-4 w-4" /> {t("integrations.add_slack")}
            </button>
            <button
              className="btn-secondary"
              onClick={() => setAddDialog("matrix")}
            >
              <Plus className="h-4 w-4" /> {t("integrations.add_matrix")}
            </button>
          </div>
        </div>

        {notify.isLoading ? (
          <p className="text-sm text-slate-500">{t("common.loading")}</p>
        ) : channels.length === 0 ? (
          <p className="text-sm text-slate-500">{t("integrations.messengers_empty")}</p>
        ) : (
          <div className="space-y-6">
            {CHANNEL_ORDER.map((kind) => {
              const rows = grouped[kind];
              if (rows.length === 0) return null;
              return (
                <div key={kind}>
                  <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                    {t(`onboarding.${kind}`)}
                  </div>
                  <div className="space-y-3">
                    {rows.map((n) => (
                      <ChannelCard
                        key={n.id}
                        connection={n}
                        testing={testing === n.id}
                        retrying={retrying === n.id}
                        onTest={() => testChannel(n.id)}
                        onRetry={() => retryWebhook(n.id)}
                        onRemove={() => removeChannel(n.id)}
                      />
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {isAdmin && (
        <section className="card">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <KeyRound className="h-5 w-5 text-brand-600" />
                <h2 className="text-lg font-semibold">
                  {t("integrations.api_keys_title")}
                </h2>
              </div>
              <p className="mt-1 text-sm text-slate-500">
                {t("integrations.api_keys_subtitle")}
              </p>
              <p className="mt-1 text-xs text-slate-500">
                {maxApiKeys !== null
                  ? t("settings.api_tokens_count", {
                      current: currentApiKeys,
                      max: maxApiKeys,
                    })
                  : t("integrations.api_keys_count_unlimited", {
                      current: currentApiKeys,
                    })}
              </p>
            </div>
            <button
              className="btn-primary inline-flex items-center gap-1"
              onClick={() => setApiKeysOpen(true)}
            >
              <SettingsIcon className="h-4 w-4" />
              {t("integrations.api_keys_manage")}
            </button>
          </div>
        </section>
      )}

      {isAdmin && <OutboundWebhooks />}

      {isAdmin && (
        <ApiKeysDialog open={apiKeysOpen} onOpenChange={setApiKeysOpen} />
      )}

      {addDialog && (
        <AddChannelDialog
          kind={addDialog}
          open={addDialog !== null}
          onOpenChange={(o) => {
            if (!o) setAddDialog(null);
          }}
          onCreated={() => {
            void notify.refetch();
          }}
        />
      )}

      <SyncProjectsDialog
        open={syncOpen}
        onOpenChange={setSyncOpen}
        connections={gitlabConns}
        ciProvider="gitlab"
        onSynced={() => {
          void ci.refetch();
          void caps.refetch();
        }}
      />
      <SyncProjectsDialog
        open={syncOpenGh}
        onOpenChange={setSyncOpenGh}
        connections={githubConns}
        ciProvider="github"
        onSynced={() => {
          void ciGh.refetch();
          void caps.refetch();
        }}
      />
      <SyncProjectsDialog
        open={syncOpenBb}
        onOpenChange={setSyncOpenBb}
        connections={bitbucketConns}
        ciProvider="bitbucket"
        onSynced={() => {
          void ciBb.refetch();
          void caps.refetch();
        }}
      />

      {baseOauthConnection && (
        <GitLabOauthAppDialog
          open={oauthOpen}
          onOpenChange={setOauthOpen}
          connection={baseOauthConnection}
          redirectUri={caps.data?.gitlab_oauth_redirect_uri ?? ""}
          ciProvider="gitlab"
          onSaved={() => {
            void ci.refetch();
            void caps.refetch();
            setOauthOpen(false);
          }}
        />
      )}
      {baseOauthConnectionGh && (
        <GitLabOauthAppDialog
          open={oauthOpenGh}
          onOpenChange={setOauthOpenGh}
          connection={toGitLabConnectionShape(baseOauthConnectionGh)}
          redirectUri={caps.data?.github_oauth_redirect_uri ?? ""}
          ciProvider="github"
          onSaved={() => {
            void ciGh.refetch();
            void caps.refetch();
            setOauthOpenGh(false);
          }}
        />
      )}
      {baseOauthConnectionBb && (
        <GitLabOauthAppDialog
          open={oauthOpenBb}
          onOpenChange={setOauthOpenBb}
          connection={bitbucketToGitLabShape(baseOauthConnectionBb)}
          redirectUri={caps.data?.bitbucket_oauth_redirect_uri ?? ""}
          ciProvider="bitbucket"
          onSaved={() => {
            void ciBb.refetch();
            void caps.refetch();
            setOauthOpenBb(false);
          }}
        />
      )}
    </div>
  );
}

function RepoLimitBar({
  current,
  max,
  labelKey = "integrations.gitlab_repo_limit_label",
  ariaKey = "integrations.gitlab_repo_limit_bar_aria",
}: {
  current: number;
  max: number | null;
  labelKey?: string;
  ariaKey?: string;
}) {
  const { t } = useTranslation();

  if (max == null) {
    // Unlimited plan — show a compact counter only.
    return (
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="font-medium text-slate-700 dark:text-slate-200">
          {t(labelKey)}
        </span>
        <span className="tabular-nums text-slate-500">{current}</span>
      </div>
    );
  }

  const pct = max === 0 ? 0 : Math.min(100, Math.round((current / max) * 100));
  // Colour tiers: calm brand → amber warning → rose when maxed out.
  const barColor =
    pct >= 100
      ? "bg-rose-500"
      : pct >= 80
        ? "bg-amber-500"
        : "bg-brand-500";

  return (
    <div
      className="space-y-1.5"
      aria-label={t(ariaKey, {
        current,
        max,
      })}
    >
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="font-medium text-slate-700 dark:text-slate-200">
          {t(labelKey)}
        </span>
        <span className="tabular-nums text-slate-500 dark:text-slate-400">
          <span
            className={clsx(
              "font-semibold",
              pct >= 100 && "text-rose-600 dark:text-rose-400",
              pct >= 80 && pct < 100 && "text-amber-600 dark:text-amber-400",
            )}
          >
            {current}
          </span>
          <span> / {max}</span>
        </span>
      </div>
      <div
        className="h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuenow={current}
      >
        <div
          className={clsx(
            "h-full rounded-full transition-[width] duration-500 ease-out",
            barColor,
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function GitLabBaseRow({
  connection,
  onDelete,
}: {
  connection: GitLabConnection;
  onDelete: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div
      className={clsx(
        "flex items-center gap-3 rounded-xl border border-slate-200 bg-slate-50/60 p-3",
        "dark:border-slate-800 dark:bg-slate-900/40",
      )}
    >
      <Link2 className="h-4 w-4 flex-shrink-0 text-brand-600" />
      <div className="flex-1 min-w-0">
        <div className="truncate text-sm font-medium">
          {connection.base_url}
        </div>
        <div className="truncate text-xs text-slate-500">
          {t("integrations.gitlab_instance_row_hint")}
        </div>
      </div>
      <GitlabStatusBadge status={connection.status} />
      <button
        className="btn-danger"
        onClick={onDelete}
        aria-label={t("common.delete")}
        title={t("common.delete")}
      >
        <Trash2 className="h-4 w-4" />
      </button>
    </div>
  );
}

function GitLabProjectCard({
  connection: c,
  availableModes,
  canEnableAnotherRepo,
  onEnable,
  onDisable,
  onDelete,
  onModeChange,
  onFeedbackChange,
  onFeedbackReset,
}: {
  connection: GitLabConnection;
  availableModes: string[];
  canEnableAnotherRepo: boolean;
  onEnable: () => void;
  onDisable: () => void;
  onDelete: () => void;
  onModeChange: (mode: string) => void;
  onFeedbackChange: (
    connId: string,
    key: FeedbackChannelKey,
    value: boolean | "inherit",
  ) => void;
  onFeedbackReset: (connId: string) => void;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const canChangeMode = availableModes.length > 1;
  const isInactive = c.enabled === false;

  return (
    <div
      className={clsx(
        "group rounded-xl border border-slate-200 bg-white",
        "dark:border-slate-800 dark:bg-slate-900",
        "transition-all duration-200",
        "hover:-translate-y-0.5 hover:border-brand-400 hover:shadow-md",
        "dark:hover:border-brand-500",
        isInactive && "opacity-80",
      )}
    >
      <button
        type="button"
        onClick={() => setExpanded((x) => !x)}
        className="flex w-full items-center gap-3 p-4 text-left"
        aria-expanded={expanded}
      >
        <div className="flex-1 min-w-0">
          <div className="truncate text-sm font-medium text-slate-900 dark:text-slate-100">
            {c.external_project_name ?? c.base_url}
          </div>
          <div className="truncate text-xs text-slate-500">
            {c.base_url}
          </div>
        </div>
        <span className="badge-slate hidden sm:inline-flex">
          {t(`integrations.mode_${c.mode}`, c.mode)}
        </span>
        <GitlabStatusBadge status={c.status} />
        {isInactive && (
          <span className="badge-yellow">
            {t("integrations.gitlab_inactive")}
          </span>
        )}
        <ChevronDown
          className={clsx(
            "h-4 w-4 flex-shrink-0 text-slate-400 transition-transform duration-200",
            expanded && "rotate-180",
          )}
        />
      </button>

      <div
        className={clsx(
          "grid transition-[grid-template-rows] duration-300 ease-out",
          expanded ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
        )}
      >
        <div className="overflow-hidden">
          <div className="space-y-4 border-t border-slate-200 p-4 dark:border-slate-800">
            {canChangeMode && !isInactive && (
              <div className="flex items-center gap-2">
                <label
                  htmlFor={`gitlab-mode-${c.id}`}
                  className="text-xs font-medium text-slate-500"
                >
                  {t("integrations.gitlab_mode_select")}
                </label>
                <select
                  id={`gitlab-mode-${c.id}`}
                  className="h-9 rounded-md border border-slate-300 bg-white px-2 text-xs dark:border-slate-700 dark:bg-slate-900"
                  value={c.mode}
                  onChange={(e) => onModeChange(e.target.value)}
                >
                  {availableModes.map((m) => (
                    <option key={m} value={m}>
                      {t(`integrations.mode_${m}`, m)}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {!isInactive && c.feedback_effective && (
              <FeedbackOverrideRow
                connection={c}
                onChange={onFeedbackChange}
                onReset={onFeedbackReset}
              />
            )}

            <div className="flex flex-wrap items-center gap-2">
              {isInactive ? (
                <button
                  className="btn-primary"
                  disabled={!canEnableAnotherRepo}
                  onClick={onEnable}
                >
                  {t("integrations.gitlab_enable")}
                </button>
              ) : (
                <button className="btn-secondary" onClick={onDisable}>
                  {t("common.disable")}
                </button>
              )}
              <button className="btn-danger ml-auto" onClick={onDelete}>
                <Trash2 className="h-4 w-4" />
                <span>{t("common.delete")}</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

const CHANNEL_ICONS: Record<ChannelKind, typeof MessageSquare> = {
  telegram: MessageSquare,
  slack: Hash,
  matrix: Home,
};

function ChannelCard({
  connection: n,
  testing,
  retrying,
  onTest,
  onRetry,
  onRemove,
}: {
  connection: NotificationConnection;
  testing: boolean;
  retrying: boolean;
  onTest: () => void;
  onRetry: () => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  const kind = n.channel as ChannelKind;
  const Icon = CHANNEL_ICONS[kind] ?? MessageSquare;

  const primaryLabel = (() => {
    if (kind === "telegram") {
      return n.bot_username
        ? `@${n.bot_username}`
        : (n.target ?? `(${t("common.pending").toLowerCase()})`);
    }
    if (kind === "slack") {
      return n.target ?? n.endpoint ?? `(${t("common.pending").toLowerCase()})`;
    }
    return n.target ?? n.endpoint ?? `(${t("common.pending").toLowerCase()})`;
  })();

  const subLabel = (() => {
    if (kind === "telegram") {
      if (n.target) return `chat: ${n.target}`;
      if (n.link_code) return `/link ${n.link_code}`;
      return null;
    }
    if (kind === "slack") {
      return n.endpoint
        ? maskUrl(n.endpoint)
        : null;
    }
    return n.endpoint ?? null;
  })();

  const tgBroken =
    kind === "telegram" && (n.webhook_registered === false || !n.target);

  return (
    <div
      className={clsx(
        "rounded-xl border border-slate-200 bg-white",
        "dark:border-slate-800 dark:bg-slate-900",
        "transition-all duration-200",
        "hover:-translate-y-0.5 hover:border-brand-400 hover:shadow-md",
        "dark:hover:border-brand-500",
      )}
    >
      <button
        type="button"
        onClick={() => setExpanded((x) => !x)}
        className="flex w-full items-center gap-3 p-4 text-left"
        aria-expanded={expanded}
      >
        <Icon className="h-5 w-5 flex-shrink-0 text-brand-600" aria-hidden />
        <div className="flex-1 min-w-0">
          <div className="truncate text-sm font-medium text-slate-900 dark:text-slate-100">
            {primaryLabel}
          </div>
          {subLabel && (
            <div className="truncate text-xs text-slate-500">{subLabel}</div>
          )}
        </div>
        {tgBroken && (
          <AlertTriangle
            className="h-4 w-4 flex-shrink-0 text-amber-500"
            aria-hidden
          />
        )}
        <span
          className={
            n.status === "active" ? "badge-green" : "badge-yellow"
          }
        >
          {n.status}
        </span>
        <ChevronDown
          className={clsx(
            "h-4 w-4 flex-shrink-0 text-slate-400 transition-transform duration-200",
            expanded && "rotate-180",
          )}
        />
      </button>

      <div
        className={clsx(
          "grid transition-[grid-template-rows] duration-300 ease-out",
          expanded ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
        )}
      >
        <div className="overflow-hidden">
          <div className="space-y-4 border-t border-slate-200 p-4 dark:border-slate-800">
            <ChannelDetails connection={n} />

            {tgBroken && (
              <div className="flex items-start gap-2 rounded-md bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-900/20 dark:text-amber-200">
                <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0" />
                <span>
                  {n.webhook_registered === false
                    ? t("integrations.webhook_not_registered_hint")
                    : t("integrations.awaiting_link_hint", {
                        code: n.link_code ?? "",
                        bot: n.bot_username
                          ? `@${n.bot_username}`
                          : t("integrations.bot"),
                      })}
                </span>
              </div>
            )}

            <div className="flex flex-wrap items-center gap-2">
              {kind === "telegram" && n.webhook_registered === false && (
                <button
                  className="btn-secondary inline-flex items-center gap-1"
                  disabled={retrying}
                  onClick={onRetry}
                >
                  {retrying ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <RefreshCw className="h-4 w-4" />
                  )}
                  {t("integrations.webhook_retry")}
                </button>
              )}
              <button
                className="btn-secondary inline-flex items-center gap-1"
                disabled={testing}
                onClick={onTest}
              >
                {testing ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Send className="h-4 w-4" />
                )}
                {t("common.test")}
              </button>
              <button
                className="btn-danger ml-auto inline-flex items-center gap-1"
                onClick={onRemove}
                aria-label={t("common.remove")}
              >
                <Trash2 className="h-4 w-4" />
                <span>{t("common.remove")}</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ChannelDetails({ connection: n }: { connection: NotificationConnection }) {
  const { t } = useTranslation();
  const kind = n.channel as ChannelKind;
  const rows: { label: string; value: string | null }[] = [];

  if (kind === "telegram") {
    rows.push({
      label: t("integrations.channel_details_bot"),
      value: n.bot_username ? `@${n.bot_username}` : null,
    });
    rows.push({
      label: t("integrations.channel_details_chat"),
      value: n.target,
    });
    if (!n.target && n.link_code) {
      rows.push({
        label: t("integrations.channel_details_link_code"),
        value: `/link ${n.link_code}`,
      });
    }
    rows.push({
      label: t("integrations.channel_details_webhook"),
      value:
        n.webhook_registered == null
          ? null
          : n.webhook_registered
            ? t("integrations.channel_details_webhook_yes")
            : t("integrations.channel_details_webhook_no"),
    });
  } else if (kind === "slack") {
    rows.push({
      label: t("integrations.channel_details_endpoint"),
      value: n.endpoint ? maskUrl(n.endpoint) : null,
    });
    rows.push({
      label: t("integrations.channel_details_channel"),
      value: n.target,
    });
  } else {
    rows.push({
      label: t("integrations.channel_details_homeserver"),
      value: n.endpoint,
    });
    rows.push({
      label: t("integrations.channel_details_room"),
      value: n.target,
    });
  }

  const visible = rows.filter((r) => r.value);
  if (visible.length === 0) return null;

  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-xs sm:grid-cols-2">
      {visible.map((r) => (
        <div key={r.label} className="flex items-start gap-2">
          <dt className="w-24 flex-shrink-0 text-slate-500">{r.label}</dt>
          <dd className="min-w-0 truncate text-slate-800 dark:text-slate-200">
            {r.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function maskUrl(url: string): string {
  // Keep scheme + host visible; collapse the long path/token to dots.
  try {
    const u = new URL(url);
    return `${u.protocol}//${u.host}/…`;
  } catch {
    return url.length > 40 ? `${url.slice(0, 32)}…` : url;
  }
}

function GitLabOauthAppDialog({
  open,
  onOpenChange,
  connection,
  redirectUri,
  onSaved,
  ciProvider = "gitlab",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  connection: GitLabConnection;
  redirectUri: string;
  onSaved: () => void;
  ciProvider?: "gitlab" | "github" | "bitbucket";
}) {
  const { t } = useTranslation();
  const [clientId, setClientId] = useState(connection.oauth_client_id ?? "");
  const [secret, setSecret] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) {
      setClientId(connection.oauth_client_id ?? "");
      setSecret("");
    }
  }, [open, connection.id, connection.oauth_client_id]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      const body: { client_id: string; client_secret?: string } = {
        client_id: clientId.trim(),
      };
      if (secret.trim()) body.client_secret = secret.trim();
      await api(`/api/integrations/${ciProvider}/oauth-app/${connection.id}`, {
        method: "PATCH",
        body,
      });
      toast.success(t("integrations.oauth_app_saved"));
      setSecret("");
      onSaved();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-slate-900/40 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(92vw,32rem)] -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-slate-200 bg-white p-6 shadow-xl focus:outline-none dark:border-slate-800 dark:bg-slate-900">
          <div className="mb-4 flex items-start gap-3">
            <KeyRound className="mt-0.5 h-5 w-5 text-brand-600" aria-hidden />
            <div className="flex-1">
              <Dialog.Title className="text-base font-semibold">
                {t("integrations.oauth_app_title")}
              </Dialog.Title>
              <Dialog.Description className="mt-1 text-sm text-slate-500">
                {t("integrations.oauth_app_intro")}
              </Dialog.Description>
            </div>
            <Dialog.Close
              className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800"
              aria-label={t("common.close")}
            >
              <X className="h-4 w-4" />
            </Dialog.Close>
          </div>

          {redirectUri ? (
            <p className="mb-4 text-xs text-slate-500">
              {t("integrations.oauth_app_redirect_label")}{" "}
              <code className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] dark:bg-slate-800">
                {redirectUri}
              </code>
            </p>
          ) : null}

          <form onSubmit={onSubmit} className="grid gap-3">
            <div>
              <label className="label" htmlFor="gitlab-oauth-client-id">
                {t("integrations.oauth_app_client_id")}
              </label>
              <input
                id="gitlab-oauth-client-id"
                className="input"
                value={clientId}
                onChange={(e) => setClientId(e.target.value)}
                autoComplete="off"
              />
            </div>
            <div>
              <label className="label" htmlFor="gitlab-oauth-secret">
                {t("integrations.oauth_app_secret")}
              </label>
              <input
                id="gitlab-oauth-secret"
                type="password"
                className="input"
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                placeholder={t("integrations.oauth_app_secret_placeholder")}
                autoComplete="new-password"
              />
            </div>
            <div className="mt-2 flex justify-end gap-2">
              <Dialog.Close className="btn-secondary">
                {t("common.cancel")}
              </Dialog.Close>
              <button
                type="submit"
                className="btn-primary inline-flex items-center gap-2"
                disabled={saving || !clientId.trim()}
              >
                {saving && <Loader2 className="h-4 w-4 animate-spin" />}
                {t("integrations.oauth_app_save")}
              </button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function GitlabStatusBadge({ status }: { status: GitLabConnection["status"] }) {
  if (status === "active") return <span className="badge-green">{status}</span>;
  if (status === "pending_manual")
    return <span className="badge-yellow">{status}</span>;
  if (status === "error") return <span className="badge-red">{status}</span>;
  return <span className="badge-slate">{status}</span>;
}

const FEEDBACK_CHANNELS: FeedbackChannelKey[] = [
  "mr_comment",
  "commit_comment",
  "issue",
  "status_check",
];

function FeedbackOverrideRow({
  connection,
  onChange,
  onReset,
}: {
  connection: GitLabConnection;
  onChange: (
    connId: string,
    key: FeedbackChannelKey,
    value: boolean | "inherit",
  ) => void;
  onReset: (connId: string) => void;
}) {
  const { t } = useTranslation();
  const effective: FeedbackPolicy = connection.feedback_effective ?? {
    mr_comment: true,
    commit_comment: true,
    issue: true,
    status_check: true,
  };
  const override = connection.feedback_override ?? {};
  const hasOverride = Object.keys(override).length > 0;

  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs dark:border-slate-800 dark:bg-slate-900/40">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="font-medium text-slate-700 dark:text-slate-300">
          {t("integrations.feedback_title")}
        </div>
        {hasOverride && (
          <button
            className="text-xs text-brand-600 hover:underline"
            onClick={() => onReset(connection.id)}
          >
            {t("integrations.feedback_reset")}
          </button>
        )}
      </div>
      <div className="flex flex-wrap gap-4">
        {FEEDBACK_CHANNELS.map((key) => {
          const hasSpecificOverride = key in override;
          const checked = effective[key];
          const tenantBlocked = !checked && !hasSpecificOverride;
          return (
            <label
              key={key}
              className="inline-flex items-center gap-2"
              title={
                tenantBlocked
                  ? t("integrations.feedback_blocked_by_tenant")
                  : undefined
              }
            >
              <input
                type="checkbox"
                checked={checked}
                disabled={tenantBlocked}
                onChange={(e) => onChange(connection.id, key, e.target.checked)}
              />
              <span>{t(`integrations.feedback_${key}`)}</span>
              {hasSpecificOverride && (
                <span className="rounded bg-brand-100 px-1 text-[10px] uppercase text-brand-700 dark:bg-brand-900/40 dark:text-brand-200">
                  {t("integrations.feedback_override_label")}
                </span>
              )}
            </label>
          );
        })}
      </div>
      <p className="mt-2 text-[11px] text-slate-500">
        {t("integrations.feedback_per_connection_hint")}
      </p>
    </div>
  );
}

function GitFlicSection({
  connections,
  loading,
  refresh,
}: {
  connections: GitFlicConnection[];
  loading: boolean;
  refresh: () => void;
}) {
  const { t } = useTranslation();
  const caps = useCapabilities();
  const confirm = useConfirm();
  const [syncOpen, setSyncOpen] = useState(false);
  const [baseUrl, setBaseUrl] = useState("https://gitflic.ru");
  const [busy, setBusy] = useState(false);

  const baseConn = connections.find((c) => c.external_project_id === null);
  const projectConns = connections.filter((c) => c.external_project_id !== null);
  const availableModes = caps.data?.gitflic_modes ?? [];
  const maxGfRepos = caps.data?.max_gitflic_repos ?? null;
  const enabledGfCount = projectConns.filter((c) => c.enabled !== false).length;
  const canEnableAnotherGfRepo =
    maxGfRepos === null || enabledGfCount < maxGfRepos;

  function gitflicToGitLabShape(c: GitFlicConnection): GitLabConnection {
    return {
      ...c,
      gitlab_user: c.gitflic_user,
    };
  }

  async function connect() {
    setBusy(true);
    try {
      const res = await api<{ authorize_url: string }>(
        "/api/integrations/gitflic/oauth/init",
        { method: "POST", body: { base_url: baseUrl } },
      );
      window.location.href = res.authorize_url;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    } finally {
      setBusy(false);
    }
  }

  async function deleteGitflic(id: string) {
    const ok = await confirm({
      title: t("integrations.gitflic_disconnect_confirm"),
      confirmLabel: t("common.delete"),
      tone: "danger",
    });
    if (!ok) return;
    try {
      await api(`/api/integrations/gitflic/connections/${id}`, {
        method: "DELETE",
      });
      toast.success(t("integrations.gitflic_disconnected"));
      refresh();
      void caps.refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  async function enableGitflic(id: string) {
    try {
      await api(`/api/integrations/gitflic/connections/${id}/mode`, {
        method: "PATCH",
        body: { enabled: true },
      });
      toast.success(t("integrations.gitflic_enabled"));
      refresh();
      void caps.refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  async function disableGitflic(id: string) {
    const ok = await confirm({
      title: t("integrations.disable_gitflic_confirm_title"),
      confirmLabel: t("common.disable"),
    });
    if (!ok) return;
    try {
      await api(`/api/integrations/gitflic/connections/${id}/mode`, {
        method: "PATCH",
        body: { enabled: false },
      });
      toast.success(t("integrations.gitflic_disabled"));
      refresh();
      void caps.refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  async function changeGitflicMode(id: string, newMode: string) {
    try {
      await api(`/api/integrations/gitflic/connections/${id}/mode`, {
        method: "PATCH",
        body: { mode: newMode },
      });
      toast.success(t("integrations.gitflic_mode_changed"));
      refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  return (
    <section className="card">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Link2 className="h-5 w-5 text-rose-600" />
          <h2 className="text-lg font-semibold">{t("integrations.gitflic")}</h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {!baseConn && (
            <>
              <input
                className="input w-56 text-sm"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="https://gitflic.ru"
              />
              <button
                type="button"
                className="btn-primary inline-flex items-center gap-1"
                onClick={connect}
                disabled={busy}
              >
                {t("integrations.gitflic_connect")}
              </button>
            </>
          )}
          {baseConn && (
            <button
              type="button"
              className="btn-secondary inline-flex items-center gap-1"
              onClick={() => setSyncOpen(true)}
            >
              <RefreshCw className="h-4 w-4" />
              {t("integrations.sync_projects")}
            </button>
          )}
        </div>
      </div>
      <RepoLimitBar
        current={enabledGfCount}
        max={maxGfRepos}
        labelKey="integrations.gitflic_repo_limit_label"
        ariaKey="integrations.gitflic_repo_limit_bar_aria"
      />
      {loading ? (
        <p className="mt-4 text-sm text-slate-500">{t("common.loading")}</p>
      ) : connections.length === 0 ? (
        <p className="mt-4 text-sm text-slate-500">
          {t("integrations.gitflic_empty")}
        </p>
      ) : (
        <div className="mt-4 space-y-3">
          {baseConn && (
            <GitLabBaseRow
              connection={gitflicToGitLabShape(baseConn)}
              onDelete={() => deleteGitflic(baseConn.id)}
            />
          )}
          {projectConns.map((c) => (
            <GitLabProjectCard
              key={c.id}
              connection={gitflicToGitLabShape(c)}
              availableModes={availableModes}
              canEnableAnotherRepo={canEnableAnotherGfRepo}
              onEnable={() => enableGitflic(c.id)}
              onDisable={() => disableGitflic(c.id)}
              onDelete={() => deleteGitflic(c.id)}
              onModeChange={(mode) => changeGitflicMode(c.id, mode)}
              onFeedbackChange={() => {
              }}
              onFeedbackReset={() => {}}
            />
          ))}
        </div>
      )}

      <SyncProjectsDialog
        open={syncOpen}
        onOpenChange={setSyncOpen}
        connections={connections}
        ciProvider="gitflic"
        onSynced={refresh}
      />
    </section>
  );
}
