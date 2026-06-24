import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Check,
  Circle,
  ExternalLink,
  Github,
  GitBranch,
  KeyRound,
  Loader2,
  Server,
  Sparkles,
  Terminal,
  Zap,
} from "lucide-react";
import clsx from "clsx";
import { useTranslation } from "react-i18next";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { toast } from "../lib/toast";
import LangSwitcher from "../components/LangSwitcher";
import IngestQuickstart from "../components/IngestQuickstart";
import type { GitLabProject, WatchProjectsResponse } from "../lib/types";
import { useCapabilities } from "../lib/capabilities";
import { resolveWatchMode } from "../lib/watchMode";
import {
  INGEST_PROVIDERS,
  type IngestProvider,
} from "../lib/ingestSnippet";

type Flavor = "gitlab_com" | "self_hosted";
type Mode = "oauth" | "webhook";
type CiTarget = "gitlab" | "github" | "bitbucket" | "gitflic";
type Track = "git" | "api";

type StepKey =
  | "track"
  | "provider"
  | "flavor"
  | "mode"
  | "connect"
  | "projects"
  | "ci_pick"
  | "token"
  | "quickstart"
  | "messenger"
  | "demo";

const GIT_STEPS: StepKey[] = [
  "track",
  "provider",
  "flavor",
  "mode",
  "connect",
  "projects",
  "messenger",
  "demo",
];

const API_STEPS: StepKey[] = [
  "track",
  "ci_pick",
  "token",
  "quickstart",
  "messenger",
  "demo",
];

type ApiTokenCreated = {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
  token: string;
};

const CI_OPTIONS: ReadonlyArray<{
  id: IngestProvider;
  iconBg: string;
  defaultName: string;
}> = [
  { id: "jenkins", iconBg: "bg-[#d33833]", defaultName: "jenkins-prod" },
  { id: "circleci", iconBg: "bg-slate-900", defaultName: "circleci-prod" },
  { id: "teamcity", iconBg: "bg-[#0a78c2]", defaultName: "teamcity-prod" },
  { id: "drone", iconBg: "bg-[#3a3a3a]", defaultName: "drone-prod" },
  { id: "github_actions", iconBg: "bg-slate-900", defaultName: "github-actions-prod" },
  { id: "gitlab_ci", iconBg: "bg-[#fc6d26]", defaultName: "gitlab-ci-prod" },
  { id: "generic", iconBg: "bg-slate-600", defaultName: "ci-ingest" },
];

export default function OnboardingPage() {
  const { t } = useTranslation();
  const { me, refresh } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const caps = useCapabilities();
  const apiKeysAllowed = caps.data?.api_keys_allowed ?? true;
  const maxApiKeys = caps.data?.max_api_keys ?? null;
  const currentApiKeys = caps.data?.current_api_keys ?? 0;
  const tokenLimitReached =
    maxApiKeys !== null && currentApiKeys >= maxApiKeys;

  const [track, setTrack] = useState<Track | null>(null);
  const [stepKey, setStepKey] = useState<StepKey>("track");

  const stepKeys: StepKey[] = useMemo(
    () => (track === "api" ? API_STEPS : GIT_STEPS),
    [track],
  );
  const stepIndex = Math.max(0, stepKeys.indexOf(stepKey));

  /** Move to a known step in the current track. If the requested step is not */
  function goTo(next: StepKey) {
    if (stepKeys.includes(next)) setStepKey(next);
  }

  const [ciTarget, setCiTarget] = useState<CiTarget>("gitlab");
  const [flavor, setFlavor] = useState<Flavor>("gitlab_com");
  const [mode, setMode] = useState<Mode>("oauth");
  const [baseUrl, setBaseUrl] = useState("https://gitlab.com");

  const [oauthClientId, setOauthClientId] = useState("");
  const [oauthClientSecret, setOauthClientSecret] = useState("");

  const [projectInput, setProjectInput] = useState("");
  const [pat, setPat] = useState("");
  const [bbProjectKey, setBbProjectKey] = useState("");
  const [bbRepoSlug, setBbRepoSlug] = useState("");
  const [bbDcLegacyWarning, setBbDcLegacyWarning] = useState(false);

  const [connectionId, setConnectionId] = useState<string | null>(null);
  const [webhookInfo, setWebhookInfo] = useState<{
    url: string;
    secret: string;
    hookRegistered: boolean;
    registerError?: string | null;
  } | null>(null);

  const [projects, setProjects] = useState<GitLabProject[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(false);
  const [selectedProjects, setSelectedProjects] = useState<Set<string>>(new Set());
  const [watching, setWatching] = useState(false);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [selectedCi, setSelectedCi] = useState<IngestProvider | null>(null);
  const [tokenName, setTokenName] = useState("");
  const [tokenExpiresAt, setTokenExpiresAt] = useState("");
  const [creatingToken, setCreatingToken] = useState(false);
  const [createdToken, setCreatedToken] = useState<ApiTokenCreated | null>(
    null,
  );
  const [tokenError, setTokenError] = useState<string | null>(null);

  useEffect(() => {
    if (params.get("gitlab") === "connected") {
      const cid = params.get("connection_id");
      if (cid) setConnectionId(cid);
      setTrack("git");
      setMode("oauth");
      setCiTarget("gitlab");
      setStepKey("projects");
      setParams({}, { replace: true });
    }
    if (params.get("github") === "connected") {
      const cid = params.get("connection_id");
      if (cid) setConnectionId(cid);
      setTrack("git");
      setMode("oauth");
      setCiTarget("github");
      setBaseUrl("https://github.com");
      setStepKey("projects");
      setParams({}, { replace: true });
    }
    if (params.get("bitbucket") === "connected") {
      const cid = params.get("connection_id");
      if (cid) setConnectionId(cid);
      setTrack("git");
      setMode("oauth");
      setCiTarget("bitbucket");
      setBaseUrl("https://bitbucket.org");
      setStepKey("projects");
      setParams({}, { replace: true });
    }
    if (params.get("bitbucket") === "error") {
      const desc = params.get("error_description") || params.get("error") || "";
      const friendly =
        desc && /scope/i.test(desc)
          ? t("onboarding.bitbucket_oauth_scope_error")
          : desc || t("toast.unknown_error");
      setTrack("git");
      setCiTarget("bitbucket");
      setBaseUrl("https://bitbucket.org");
      setStepKey("connect");
      setError(friendly);
      toast.error(friendly);
      setParams({}, { replace: true });
    }
    if (params.get("gitflic") === "connected") {
      const cid = params.get("connection_id");
      if (cid) setConnectionId(cid);
      setTrack("git");
      setMode("oauth");
      setCiTarget("gitflic");
      setBaseUrl("https://gitflic.ru");
      setStepKey("projects");
      setParams({}, { replace: true });
    }
    if (params.get("gitflic") === "error") {
      const desc = params.get("error") || t("toast.unknown_error");
      setTrack("git");
      setCiTarget("gitflic");
      setBaseUrl("https://gitflic.ru");
      setStepKey("connect");
      setError(desc);
      toast.error(desc);
      setParams({}, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const watchModes =
    ciTarget === "github"
      ? caps.data?.github_modes
      : ciTarget === "bitbucket"
        ? caps.data?.bitbucket_modes
        : ciTarget === "gitflic"
          ? caps.data?.gitflic_modes
          : caps.data?.gitlab_modes;
  const redirectUriForConnect =
    ciTarget === "github"
      ? caps.data?.github_oauth_redirect_uri
      : ciTarget === "bitbucket"
        ? caps.data?.bitbucket_oauth_redirect_uri
        : ciTarget === "gitflic"
          ? caps.data?.gitflic_oauth_redirect_uri
          : caps.data?.gitlab_oauth_redirect_uri;

  async function startOAuth() {
    setError(null);
    setLoading(true);
    try {
      const initEndpoint =
        ciTarget === "github"
          ? "/api/integrations/github/oauth/init"
          : ciTarget === "bitbucket"
            ? "/api/integrations/bitbucket/oauth/init"
            : ciTarget === "gitflic"
              ? "/api/integrations/gitflic/oauth/init"
              : "/api/integrations/gitlab/oauth/init";
      const res = await api<{ authorize_url: string }>(initEndpoint, {
        method: "POST",
        body: {
          base_url: baseUrl,
          client_id: flavor === "self_hosted" ? oauthClientId : undefined,
          client_secret:
            flavor === "self_hosted" ? oauthClientSecret : undefined,
        },
      });
      window.location.href = res.authorize_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : t("toast.unknown_error"));
      setLoading(false);
    }
  }

  async function startWebhookInit() {
    setError(null);
    setLoading(true);
    try {
      if (ciTarget === "bitbucket") {
        const res = await api<{
          connection_id: string;
          webhook_url: string;
          webhook_secret: string;
          hook_registered: boolean;
          instructions: string[];
          legacy_dc_warning: boolean;
          webhook_register_error?: string | null;
        }>("/api/integrations/bitbucket/webhook/init", {
          method: "POST",
          body: {
            base_url: baseUrl,
            project_key: bbProjectKey.trim(),
            repo_slug: bbRepoSlug.trim(),
            personal_access_token: pat || undefined,
          },
        });
        setConnectionId(res.connection_id);
        setWebhookInfo({
          url: res.webhook_url,
          secret: res.webhook_secret,
          hookRegistered: res.hook_registered,
          registerError: res.webhook_register_error ?? null,
        });
        setBbDcLegacyWarning(Boolean(res.legacy_dc_warning));
        goTo("messenger");
      } else {
        const res = await api<{
          connection_id: string;
          webhook_url: string;
          webhook_secret: string;
          hook_registered: boolean;
          instructions: string[];
          webhook_register_error?: string | null;
        }>("/api/integrations/gitlab/webhook/init", {
          method: "POST",
          body: {
            base_url: baseUrl,
            project: projectInput,
            personal_access_token: pat || undefined,
          },
        });
        setConnectionId(res.connection_id);
        setWebhookInfo({
          url: res.webhook_url,
          secret: res.webhook_secret,
          hookRegistered: res.hook_registered,
          registerError: res.webhook_register_error ?? null,
        });
        goTo("messenger");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("toast.unknown_error"));
    } finally {
      setLoading(false);
    }
  }

  async function loadProjects() {
    if (!connectionId) return;
    setProjectsLoading(true);
    try {
      // GitLab uses /projects, GitHub & Bitbucket Cloud use /repos.
      const apiPrefix =
        ciTarget === "github"
          ? "github"
          : ciTarget === "bitbucket"
            ? "bitbucket"
            : ciTarget === "gitflic"
              ? "gitflic"
              : "gitlab";
      const path =
        apiPrefix === "gitlab" || apiPrefix === "gitflic" ? "projects" : "repos";
      const res = await api<GitLabProject[]>(
        `/api/integrations/${apiPrefix}/${path}?connection_id=${connectionId}`,
      );
      setProjects(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("toast.unknown_error"));
    } finally {
      setProjectsLoading(false);
    }
  }

  useEffect(() => {
    if (
      stepKey === "projects" &&
      mode === "oauth" &&
      connectionId &&
      projects.length === 0
    ) {
      void loadProjects();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stepKey, mode, connectionId]);

  async function watchSelected() {
    if (!connectionId || selectedProjects.size === 0) return;
    const watchMode = resolveWatchMode(watchModes);
    if (!watchMode) {
      setError(t("onboarding.watch_mode_unavailable"));
      return;
    }
    setWatching(true);
    try {
      const apiPrefix =
        ciTarget === "github"
          ? "github"
          : ciTarget === "bitbucket"
            ? "bitbucket"
            : ciTarget === "gitflic"
              ? "gitflic"
              : "gitlab";
      const watchBody =
        apiPrefix === "gitflic"
          ? { project_paths: Array.from(selectedProjects), mode: watchMode }
          : { project_ids: Array.from(selectedProjects), mode: watchMode };
      const res = await api<WatchProjectsResponse>(
        `/api/integrations/${apiPrefix}/watch?connection_id=${connectionId}`,
        { method: "POST", body: watchBody },
      );
      if (res.repo_limit_partial) {
        toast.warning(t("integrations.repo_limit_partial"));
      }
      goTo("messenger");
    } catch (err) {
      setError(err instanceof Error ? err.message : t("toast.unknown_error"));
    } finally {
      setWatching(false);
    }
  }

  const canProceedMode = useMemo(() => {
    if (flavor === "self_hosted") return baseUrl.startsWith("http");
    return true;
  }, [flavor, baseUrl]);

  function StepIndicator() {
    const cols = stepKeys.length <= 6 ? "md:grid-cols-6" : "md:grid-cols-8";
    return (
      <ol
        className={clsx(
          "mb-8 grid grid-cols-2 gap-2 sm:grid-cols-4",
          cols,
        )}
      >
        {stepKeys.map((key, i) => {
          const done = i < stepIndex;
          const active = i === stepIndex;
          return (
            <li
              key={key}
              className={clsx(
                "flex flex-col items-center text-center text-xs",
                done && "text-brand-600",
                active && "font-semibold text-slate-900 dark:text-slate-100",
                !done && !active && "text-slate-400",
              )}
            >
              <div
                className={clsx(
                  "mb-2 flex h-8 w-8 items-center justify-center rounded-full border",
                  done && "border-brand-600 bg-brand-600 text-white",
                  active && !done && "border-brand-600 text-brand-600",
                  !done && !active && "border-slate-300",
                )}
              >
                {done ? <Check className="h-4 w-4" /> : i + 1}
              </div>
              {t(`onboarding.steps.${key}`)}
            </li>
          );
        })}
      </ol>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 p-6 dark:bg-slate-950">
      <div className="mx-auto max-w-3xl">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-600 text-white">
            <Zap className="h-5 w-5" />
          </div>
          <div className="flex-1">
            <h1 className="text-xl font-semibold">
              {t("onboarding.welcome", { email: me?.user.email ?? "" })}
            </h1>
            <p className="text-sm text-slate-500">{t("onboarding.subtitle")}</p>
          </div>
          <LangSwitcher />
          <button className="btn-secondary" onClick={() => navigate("/dashboard")}>
            {t("onboarding.skip")}
          </button>
        </div>

        {stepKey === "messenger" && webhookInfo && !webhookInfo.hookRegistered && (
          <WebhookSetupPanel webhookInfo={webhookInfo} ciTarget={ciTarget} />
        )}

        <div className="card">
          <StepIndicator />

          {error && (
            <div className="mb-4 rounded-lg border border-rose-300 bg-rose-50 p-3 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-900/30 dark:text-rose-200">
              {error}
            </div>
          )}

          {stepKey === "track" && (
            <div className="space-y-4">
              <h2 className="text-lg font-semibold">{t("onboarding.track_title")}</h2>
              <p className="text-sm text-slate-500">{t("onboarding.track_subtitle")}</p>
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <button
                  type="button"
                  onClick={() => {
                    setTrack("git");
                    setStepKey("provider");
                  }}
                  className={clsx(
                    "flex items-start gap-3 rounded-xl border p-4 text-left transition-colors",
                    track === "git"
                      ? "border-brand-600 bg-brand-50 dark:bg-brand-900/30"
                      : "border-slate-200 bg-white hover:border-brand-300 dark:border-slate-800 dark:bg-slate-900",
                  )}
                >
                  <div className="mt-0.5 flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-brand-600 text-white">
                    <GitBranch className="h-5 w-5" />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <div className="font-medium">{t("onboarding.track_git_label")}</div>
                      <span className="badge-green text-[10px]">
                        {t("integrations.recommended")}
                      </span>
                    </div>
                    <div className="mt-1 text-sm text-slate-500">
                      {t("onboarding.track_git_desc")}
                    </div>
                    <ul className="mt-2 space-y-1 text-xs text-slate-500">
                      <li className="flex items-start gap-2">
                        <Circle className="mt-1 h-1.5 w-1.5 flex-shrink-0 fill-current" />
                        {t("onboarding.track_git_b1")}
                      </li>
                      <li className="flex items-start gap-2">
                        <Circle className="mt-1 h-1.5 w-1.5 flex-shrink-0 fill-current" />
                        {t("onboarding.track_git_b2")}
                      </li>
                      <li className="flex items-start gap-2">
                        <Circle className="mt-1 h-1.5 w-1.5 flex-shrink-0 fill-current" />
                        {t("onboarding.track_git_b3")}
                      </li>
                    </ul>
                  </div>
                </button>
                <button
                  type="button"
                  disabled={!apiKeysAllowed}
                  onClick={() => {
                    if (!apiKeysAllowed) return;
                    setTrack("api");
                    setStepKey("ci_pick");
                  }}
                  className={clsx(
                    "flex items-start gap-3 rounded-xl border p-4 text-left transition-colors",
                    track === "api"
                      ? "border-brand-600 bg-brand-50 dark:bg-brand-900/30"
                      : "border-slate-200 bg-white hover:border-brand-300 dark:border-slate-800 dark:bg-slate-900",
                    !apiKeysAllowed && "cursor-not-allowed opacity-60",
                  )}
                >
                  <div className="mt-0.5 flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-slate-700 text-white">
                    <Server className="h-5 w-5" />
                  </div>
                  <div>
                    <div className="font-medium">{t("onboarding.track_api_label")}</div>
                    <div className="mt-1 text-sm text-slate-500">
                      {t("onboarding.track_api_desc")}
                    </div>
                    <ul className="mt-2 space-y-1 text-xs text-slate-500">
                      <li className="flex items-start gap-2">
                        <Circle className="mt-1 h-1.5 w-1.5 flex-shrink-0 fill-current" />
                        {t("onboarding.track_api_b1")}
                      </li>
                      <li className="flex items-start gap-2">
                        <Circle className="mt-1 h-1.5 w-1.5 flex-shrink-0 fill-current" />
                        {t("onboarding.track_api_b2")}
                      </li>
                      <li className="flex items-start gap-2">
                        <Circle className="mt-1 h-1.5 w-1.5 flex-shrink-0 fill-current" />
                        {t("onboarding.track_api_b3")}
                      </li>
                    </ul>
                    {!apiKeysAllowed && (
                      <div className="mt-2 text-xs text-amber-600 dark:text-amber-400">
                        {t("onboarding.track_api_disabled")}
                      </div>
                    )}
                  </div>
                </button>
              </div>
            </div>
          )}

          {stepKey === "provider" && (
            <div className="space-y-4">
              <h2 className="text-lg font-semibold">{t("onboarding.provider_title")}</h2>
              <p className="text-sm text-slate-500">{t("onboarding.provider_subtitle")}</p>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <button
                  type="button"
                  onClick={() => {
                    setCiTarget("gitlab");
                    setFlavor("gitlab_com");
                    setBaseUrl("https://gitlab.com");
                    setMode("oauth");
                  }}
                  className={clsx(
                    "flex items-start gap-3 rounded-xl border p-4 text-left transition-colors",
                    ciTarget === "gitlab"
                      ? "border-brand-600 bg-brand-50 dark:bg-brand-900/30"
                      : "border-slate-200 bg-white hover:border-brand-300 dark:border-slate-800 dark:bg-slate-900",
                  )}
                >
                  <div className="mt-0.5 flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-[#fc6d26] text-white">
                    <Zap className="h-5 w-5" />
                  </div>
                  <div>
                    <div className="font-medium">{t("onboarding.provider_gitlab")}</div>
                    <div className="mt-1 text-sm text-slate-500">
                      {t("onboarding.provider_gitlab_desc")}
                    </div>
                  </div>
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setCiTarget("github");
                    setFlavor("gitlab_com");
                    setBaseUrl("https://github.com");
                    setMode("oauth");
                  }}
                  className={clsx(
                    "flex items-start gap-3 rounded-xl border p-4 text-left transition-colors",
                    ciTarget === "github"
                      ? "border-brand-600 bg-brand-50 dark:bg-brand-900/30"
                      : "border-slate-200 bg-white hover:border-brand-300 dark:border-slate-800 dark:bg-slate-900",
                  )}
                >
                  <div className="mt-0.5 flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900">
                    <Github className="h-5 w-5" />
                  </div>
                  <div>
                    <div className="font-medium">{t("onboarding.provider_github")}</div>
                    <div className="mt-1 text-sm text-slate-500">
                      {t("onboarding.provider_github_desc")}
                    </div>
                  </div>
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setCiTarget("bitbucket");
                    setFlavor("gitlab_com");
                    setBaseUrl("https://bitbucket.org");
                    setMode("oauth");
                  }}
                  className={clsx(
                    "flex items-start gap-3 rounded-xl border p-4 text-left transition-colors",
                    ciTarget === "bitbucket"
                      ? "border-brand-600 bg-brand-50 dark:bg-brand-900/30"
                      : "border-slate-200 bg-white hover:border-brand-300 dark:border-slate-800 dark:bg-slate-900",
                  )}
                >
                  <div className="mt-0.5 flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-[#0052CC] text-white">
                    <GitBranch className="h-5 w-5" />
                  </div>
                  <div>
                    <div className="font-medium">{t("onboarding.provider_bitbucket")}</div>
                    <div className="mt-1 text-sm text-slate-500">
                      {t("onboarding.provider_bitbucket_desc")}
                    </div>
                  </div>
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setCiTarget("gitflic");
                    setFlavor("gitlab_com");
                    setBaseUrl("https://gitflic.ru");
                    setMode("oauth");
                  }}
                  className={clsx(
                    "flex items-start gap-3 rounded-xl border p-4 text-left transition-colors",
                    ciTarget === "gitflic"
                      ? "border-brand-600 bg-brand-50 dark:bg-brand-900/30"
                      : "border-slate-200 bg-white hover:border-brand-300 dark:border-slate-800 dark:bg-slate-900",
                  )}
                >
                  <div className="mt-0.5 flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-[#ff5c5c] text-white">
                    <GitBranch className="h-5 w-5" />
                  </div>
                  <div>
                    <div className="font-medium">{t("onboarding.provider_gitflic")}</div>
                    <div className="mt-1 text-sm text-slate-500">
                      {t("onboarding.provider_gitflic_desc")}
                    </div>
                  </div>
                </button>
              </div>
              <div className="flex items-center justify-between pt-4">
                <button className="btn-secondary" onClick={() => goTo("track")}>
                  {t("common.back")}
                </button>
                <button className="btn-primary" onClick={() => goTo("flavor")}>
                  {t("common.continue")}
                </button>
              </div>
            </div>
          )}

          {stepKey === "flavor" && (
            <div className="space-y-4">
              <h2 className="text-lg font-semibold">
                {ciTarget === "github"
                  ? t("onboarding.flavor_title_github")
                  : ciTarget === "bitbucket"
                    ? t("onboarding.flavor_title_bitbucket")
                    : ciTarget === "gitflic"
                      ? t("onboarding.flavor_title_gitflic")
                      : t("onboarding.flavor_title")}
              </h2>
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <FlavorCard
                  label={
                    ciTarget === "github"
                      ? t("onboarding.flavor_saas_github")
                      : ciTarget === "bitbucket"
                        ? t("onboarding.flavor_saas_bitbucket")
                        : ciTarget === "gitflic"
                          ? t("onboarding.flavor_saas_gitflic")
                          : t("onboarding.flavor_saas")
                  }
                  description={
                    ciTarget === "github"
                      ? t("onboarding.flavor_saas_github_desc")
                      : ciTarget === "bitbucket"
                        ? t("onboarding.flavor_saas_bitbucket_desc")
                        : ciTarget === "gitflic"
                          ? t("onboarding.flavor_saas_gitflic_desc")
                          : t("onboarding.flavor_saas_desc")
                  }
                  active={flavor === "gitlab_com"}
                  onClick={() => {
                    setFlavor("gitlab_com");
                    setBaseUrl(
                      ciTarget === "github"
                        ? "https://github.com"
                        : ciTarget === "bitbucket"
                          ? "https://bitbucket.org"
                          : ciTarget === "gitflic"
                            ? "https://gitflic.ru"
                            : "https://gitlab.com",
                    );
                    if (ciTarget === "bitbucket" || ciTarget === "gitflic") {
                      setMode("oauth");
                    }
                  }}
                />
                <FlavorCard
                  label={
                    ciTarget === "github"
                      ? t("onboarding.flavor_self_github")
                      : ciTarget === "bitbucket"
                        ? t("onboarding.flavor_self_bitbucket")
                        : ciTarget === "gitflic"
                          ? t("onboarding.flavor_self_gitflic")
                          : t("onboarding.flavor_self")
                  }
                  description={
                    ciTarget === "github"
                      ? t("onboarding.flavor_self_github_desc")
                      : ciTarget === "bitbucket"
                        ? t("onboarding.flavor_self_bitbucket_desc")
                        : ciTarget === "gitflic"
                          ? t("onboarding.flavor_self_gitflic_desc")
                          : t("onboarding.flavor_self_desc")
                  }
                  active={flavor === "self_hosted"}
                  onClick={() => {
                    setFlavor("self_hosted");
                    if (ciTarget === "bitbucket") {
                      setBaseUrl("https://bitbucket.mycompany.com");
                      setMode("webhook");
                    }
                    if (ciTarget === "gitflic") {
                      setBaseUrl("https://gitflic.mycompany.com");
                      setMode("oauth");
                    }
                  }}
                />
              </div>
              {flavor === "self_hosted" && (
                <div>
                  <label className="label">
                    {ciTarget === "github"
                      ? t("onboarding.base_url_github")
                      : ciTarget === "bitbucket"
                        ? t("onboarding.base_url_bitbucket")
                        : ciTarget === "gitflic"
                          ? t("onboarding.base_url_gitflic")
                          : t("onboarding.base_url")}
                  </label>
                  <input
                    className="input"
                    value={baseUrl}
                    onChange={(e) => setBaseUrl(e.target.value)}
                    placeholder={
                      ciTarget === "github"
                        ? "https://github.mycompany.com"
                        : ciTarget === "bitbucket"
                          ? "https://bitbucket.mycompany.com"
                          : ciTarget === "gitflic"
                            ? "https://gitflic.mycompany.com"
                            : "https://gitlab.mycompany.com"
                    }
                  />
                </div>
              )}
              <div className="flex items-center justify-between pt-4">
                <button className="btn-secondary" onClick={() => goTo("provider")}>
                  {t("common.back")}
                </button>
                <button
                  className="btn-primary"
                  disabled={!canProceedMode}
                  onClick={() => goTo("mode")}
                >
                  {t("common.continue")}
                </button>
              </div>
            </div>
          )}

          {stepKey === "mode" && (
            <div className="space-y-4">
              <h2 className="text-lg font-semibold">{t("onboarding.mode_title")}</h2>
              {ciTarget === "github" && (
                <p className="text-sm text-slate-500">{t("onboarding.github_oauth_only_hint")}</p>
              )}
              {ciTarget === "bitbucket" && flavor === "gitlab_com" && (
                <p className="text-sm text-slate-500">
                  {t("onboarding.bitbucket_cloud_oauth_only_hint")}
                </p>
              )}
              {ciTarget === "bitbucket" && flavor === "self_hosted" && (
                <p className="text-sm text-slate-500">
                  {t("onboarding.bitbucket_dc_webhook_only_hint")}
                </p>
              )}
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                {/* Bitbucket Cloud → OAuth only; DC → webhook only.
                    GitLab keeps both options; GitHub keeps OAuth only. */}
                {!(ciTarget === "bitbucket" && flavor === "self_hosted") && (
                  <ModeCard
                    title={t("integrations.mode_oauth")}
                    bullets={[
                      t("onboarding.mode_oauth_b1"),
                      t("onboarding.mode_oauth_b2"),
                      t("onboarding.mode_oauth_b3"),
                    ]}
                    badge={t("integrations.recommended")}
                    active={mode === "oauth"}
                    onClick={() => setMode("oauth")}
                  />
                )}
                {(ciTarget === "gitlab" ||
                  (ciTarget === "bitbucket" && flavor === "self_hosted")) && (
                  <ModeCard
                    title={t("integrations.mode_webhook")}
                    bullets={[
                      t("onboarding.mode_webhook_b1"),
                      t("onboarding.mode_webhook_b2"),
                      t("onboarding.mode_webhook_b3"),
                    ]}
                    badge={
                      ciTarget === "bitbucket" && flavor === "self_hosted"
                        ? t("integrations.recommended")
                        : undefined
                    }
                    active={mode === "webhook"}
                    onClick={() => setMode("webhook")}
                  />
                )}
              </div>
              <div className="flex items-center justify-between pt-4">
                <button className="btn-secondary" onClick={() => goTo("flavor")}>
                  {t("common.back")}
                </button>
                <button className="btn-primary" onClick={() => goTo("connect")}>
                  {t("common.continue")}
                </button>
              </div>
            </div>
          )}

          {stepKey === "connect" && (
            <div className="space-y-4">
              {mode === "oauth" ? (
                <>
                  <h2 className="text-lg font-semibold">{t("onboarding.connect_oauth_title")}</h2>
                  {ciTarget === "bitbucket" ? (
                    <>
                      <p className="text-sm text-slate-500">
                        {t("onboarding.connect_oauth_self_hint_bitbucket")}
                      </p>
                      <p className="text-sm text-slate-500">
                        {t("onboarding.connect_oauth_scopes_hint_bitbucket")}
                      </p>
                    </>
                  ) : flavor === "self_hosted" ? (
                    <>
                      <p className="text-sm text-slate-500">
                        {ciTarget === "github"
                          ? t("onboarding.connect_oauth_self_hint_github")
                          : t("onboarding.connect_oauth_self_hint")}{" "}
                        <code className="rounded bg-slate-100 px-1 py-0.5 text-xs dark:bg-slate-800">
                          {caps.isPending && !caps.data ? "…" : redirectUriForConnect ?? "—"}
                        </code>
                      </p>
                      <p className="text-sm text-slate-500">
                        {ciTarget === "github"
                          ? t("onboarding.connect_oauth_scopes_hint_github")
                          : t("onboarding.connect_oauth_scopes_hint")}
                      </p>
                      <div>
                        <label className="label">{t("onboarding.app_id")}</label>
                        <input
                          className="input"
                          value={oauthClientId}
                          onChange={(e) => setOauthClientId(e.target.value)}
                        />
                      </div>
                      <div>
                        <label className="label">{t("onboarding.secret")}</label>
                        <input
                          type="password"
                          className="input"
                          value={oauthClientSecret}
                          onChange={(e) => setOauthClientSecret(e.target.value)}
                        />
                      </div>
                    </>
                  ) : (
                    <div className="space-y-4">
                      <p className="text-sm text-slate-600 dark:text-slate-300">
                        {t("onboarding.connect_oauth_cloud_intro", {
                          provider:
                            ciTarget === "github"
                              ? "GitHub"
                              : ciTarget === "gitflic"
                                ? "GitFlic"
                                : "GitLab",
                        })}
                      </p>
                      <ol className="space-y-3 rounded-xl border border-slate-200 bg-white p-4 text-sm dark:border-slate-800 dark:bg-slate-900">
                        <li className="flex gap-3">
                          <span className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full bg-brand-600 text-xs font-semibold text-white">
                            1
                          </span>
                          <div>
                            <div className="font-medium text-slate-800 dark:text-slate-100">
                              {t("onboarding.connect_oauth_step1_title")}
                            </div>
                            <div className="mt-0.5 text-slate-500 dark:text-slate-400">
                              {t("onboarding.connect_oauth_step1_desc")}
                            </div>
                          </div>
                        </li>
                        <li className="flex gap-3">
                          <span className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full bg-brand-600 text-xs font-semibold text-white">
                            2
                          </span>
                          <div>
                            <div className="font-medium text-slate-800 dark:text-slate-100">
                              {t("onboarding.connect_oauth_step2_title")}
                            </div>
                            <div className="mt-0.5 text-slate-500 dark:text-slate-400">
                              {ciTarget === "github"
                                ? t("onboarding.connect_oauth_step2_desc_github")
                                : ciTarget === "gitflic"
                                  ? t("onboarding.connect_oauth_step2_desc_gitflic")
                                  : t("onboarding.connect_oauth_step2_desc_gitlab")}
                            </div>
                          </div>
                        </li>
                        <li className="flex gap-3">
                          <span className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full bg-brand-600 text-xs font-semibold text-white">
                            3
                          </span>
                          <div>
                            <div className="font-medium text-slate-800 dark:text-slate-100">
                              {t("onboarding.connect_oauth_step3_title")}
                            </div>
                            <div className="mt-0.5 text-slate-500 dark:text-slate-400">
                              {t("onboarding.connect_oauth_step3_desc")}
                            </div>
                          </div>
                        </li>
                      </ol>
                      <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-3 text-xs text-slate-600 dark:border-slate-700 dark:bg-slate-900/40 dark:text-slate-300">
                        <span className="font-semibold">
                          {t("onboarding.connect_oauth_revoke_title")}:
                        </span>{" "}
                        {ciTarget === "github"
                          ? t("onboarding.connect_oauth_revoke_github")
                          : ciTarget === "gitflic"
                            ? t("onboarding.connect_oauth_revoke_gitflic")
                            : t("onboarding.connect_oauth_revoke_gitlab")}
                      </div>
                    </div>
                  )}
                  <div className="flex items-center justify-between pt-4">
                    <button className="btn-secondary" onClick={() => goTo("mode")}>
                      {t("common.back")}
                    </button>
                    <button
                      className="btn-primary inline-flex items-center gap-2"
                      disabled={
                        loading ||
                        (ciTarget !== "bitbucket" &&
                          flavor === "self_hosted" &&
                          (!oauthClientId || !oauthClientSecret))
                      }
                      onClick={startOAuth}
                    >
                      {loading && <Loader2 className="h-4 w-4 animate-spin" />}
                      {loading
                        ? t("onboarding.redirecting")
                        : ciTarget === "github"
                          ? t("onboarding.authorize_github")
                          : ciTarget === "bitbucket"
                            ? t("onboarding.authorize_bitbucket")
                            : ciTarget === "gitflic"
                              ? t("onboarding.authorize_gitflic")
                              : t("onboarding.authorize_gitlab")}
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <h2 className="text-lg font-semibold">{t("onboarding.connect_webhook_title")}</h2>
                  <p className="text-sm text-slate-500">
                    {ciTarget === "bitbucket"
                      ? t("onboarding.bitbucket_dc_webhook_only_hint")
                      : t("onboarding.connect_webhook_intro")}
                  </p>
                  {ciTarget === "bitbucket" ? (
                    <>
                      <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs dark:border-amber-900/50 dark:bg-amber-900/20">
                        {t("onboarding.dc_legacy_warning_bitbucket")}
                      </div>
                      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                        <div>
                          <label className="label">
                            {t("onboarding.bitbucket_dc_project_key")}
                          </label>
                          <input
                            className="input"
                            value={bbProjectKey}
                            onChange={(e) => setBbProjectKey(e.target.value)}
                            placeholder="PROJ"
                          />
                        </div>
                        <div>
                          <label className="label">
                            {t("onboarding.bitbucket_dc_repo_slug")}
                          </label>
                          <input
                            className="input"
                            value={bbRepoSlug}
                            onChange={(e) => setBbRepoSlug(e.target.value)}
                            placeholder="my-repo"
                          />
                        </div>
                      </div>
                    </>
                  ) : (
                    <div>
                      <label className="label">{t("onboarding.project_url")}</label>
                      <input
                        className="input"
                        value={projectInput}
                        onChange={(e) => setProjectInput(e.target.value)}
                        placeholder="https://gitlab.com/my-group/my-project"
                      />
                    </div>
                  )}
                  <div>
                    <label className="label">{t("onboarding.pat_optional")}</label>
                    <input
                      className="input"
                      type="password"
                      value={pat}
                      onChange={(e) => setPat(e.target.value)}
                      placeholder={
                        ciTarget === "bitbucket" ? "BBDC-..." : "glpat-..."
                      }
                    />
                  </div>
                  <div className="flex items-center justify-between pt-4">
                    <button className="btn-secondary" onClick={() => goTo("mode")}>
                      {t("common.back")}
                    </button>
                    <button
                      className="btn-primary inline-flex items-center gap-2"
                      disabled={
                        loading ||
                        (ciTarget === "bitbucket"
                          ? !bbProjectKey || !bbRepoSlug
                          : !projectInput)
                      }
                      onClick={startWebhookInit}
                    >
                      {loading && <Loader2 className="h-4 w-4 animate-spin" />}
                      {loading
                        ? t("common.connecting")
                        : pat
                          ? t("onboarding.connect_register")
                          : t("common.connect")}
                    </button>
                  </div>
                </>
              )}
            </div>
          )}

          {stepKey === "projects" && (
            <div className="space-y-4">
              <h2 className="text-lg font-semibold">{t("onboarding.projects_title")}</h2>
              {projectsLoading ? (
                <p className="text-sm text-slate-500">{t("onboarding.projects_loading")}</p>
              ) : projects.length === 0 ? (
                <p className="text-sm text-slate-500">
                  {ciTarget === "github"
                    ? t("onboarding.projects_empty_github")
                    : ciTarget === "bitbucket"
                      ? t("onboarding.projects_empty_bitbucket")
                      : t("onboarding.projects_empty")}
                </p>
              ) : (
                <div className="max-h-96 overflow-y-auto divide-y divide-slate-200 rounded-lg border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
                  <SelectAllRow
                    total={projects.length}
                    selected={selectedProjects.size}
                    onToggle={(next) =>
                      setSelectedProjects(
                        next ? new Set(projects.map((p) => p.id)) : new Set(),
                      )
                    }
                  />
                  {projects.map((p) => (
                    <label
                      key={p.id}
                      className="flex items-start gap-3 p-3 hover:bg-slate-50 dark:hover:bg-slate-800"
                    >
                      <input
                        type="checkbox"
                        className="mt-1"
                        checked={selectedProjects.has(p.id)}
                        onChange={() => {
                          const next = new Set(selectedProjects);
                          if (next.has(p.id)) next.delete(p.id);
                          else next.add(p.id);
                          setSelectedProjects(next);
                        }}
                      />
                      <div className="flex-1">
                        <div className="text-sm font-medium">{p.path_with_namespace}</div>
                        <div className="text-xs text-slate-500">{p.web_url}</div>
                      </div>
                    </label>
                  ))}
                </div>
              )}
              <div className="flex items-center justify-between pt-2">
                <button className="btn-secondary" onClick={() => goTo("connect")}>
                  {t("common.back")}
                </button>
                <button
                  className="btn-primary inline-flex items-center gap-2"
                  disabled={
                    watching ||
                    selectedProjects.size === 0 ||
                    caps.isPending ||
                    resolveWatchMode(watchModes) === null
                  }
                  onClick={watchSelected}
                >
                  {watching && <Loader2 className="h-4 w-4 animate-spin" />}
                  {watching
                    ? t("onboarding.watch_registering")
                    : t("onboarding.watch_n_projects", { count: selectedProjects.size })}
                </button>
              </div>
            </div>
          )}

          {stepKey === "ci_pick" && (
            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <Terminal className="h-5 w-5 text-brand-600" />
                <h2 className="text-lg font-semibold">{t("onboarding.ci_pick_title")}</h2>
              </div>
              <p className="text-sm text-slate-500">{t("onboarding.ci_pick_subtitle")}</p>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-3">
                {CI_OPTIONS.map((opt) => {
                  const active = selectedCi === opt.id;
                  return (
                    <button
                      key={opt.id}
                      type="button"
                      onClick={() => {
                        setSelectedCi(opt.id);
                        if (!tokenName) setTokenName(opt.defaultName);
                      }}
                      className={clsx(
                        "flex items-start gap-3 rounded-xl border p-3 text-left transition-colors",
                        active
                          ? "border-brand-600 bg-brand-50 dark:bg-brand-900/30"
                          : "border-slate-200 bg-white hover:border-brand-300 dark:border-slate-800 dark:bg-slate-900",
                      )}
                    >
                      <div
                        className={clsx(
                          "mt-0.5 flex h-9 w-9 items-center justify-center rounded-lg text-white",
                          opt.iconBg,
                        )}
                      >
                        <Terminal className="h-5 w-5" />
                      </div>
                      <div className="flex-1">
                        <div className="font-medium text-sm">
                          {t(`onboarding.ci_label.${opt.id}`)}
                        </div>
                        <div className="mt-1 text-xs text-slate-500">
                          {t(`onboarding.ci_desc.${opt.id}`)}
                        </div>
                      </div>
                      {active && (
                        <Check className="h-4 w-4 flex-shrink-0 text-brand-600" />
                      )}
                    </button>
                  );
                })}
              </div>
              <div className="flex items-center justify-between pt-4">
                <button className="btn-secondary" onClick={() => goTo("track")}>
                  {t("common.back")}
                </button>
                <button
                  className="btn-primary"
                  disabled={!selectedCi}
                  onClick={() => goTo("token")}
                >
                  {t("common.continue")}
                </button>
              </div>
            </div>
          )}

          {stepKey === "token" && (
            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <KeyRound className="h-5 w-5 text-brand-600" />
                <h2 className="text-lg font-semibold">{t("onboarding.token_title")}</h2>
              </div>
              <p className="text-sm text-slate-500">{t("onboarding.token_subtitle")}</p>
              {maxApiKeys !== null && (
                <div className="text-xs text-slate-500">
                  {t("settings.api_tokens_count", {
                    current: currentApiKeys,
                    max: maxApiKeys,
                  })}
                </div>
              )}
              {tokenLimitReached && !createdToken && (
                <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs dark:border-amber-900/50 dark:bg-amber-900/20">
                  {t("settings.api_tokens_limit_reached")}
                </div>
              )}
              {tokenError && (
                <div className="rounded-lg border border-rose-300 bg-rose-50 p-3 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-900/30 dark:text-rose-200">
                  {tokenError}
                </div>
              )}
              {!createdToken ? (
                <>
                  <div>
                    <label className="label">{t("common.name")}</label>
                    <input
                      className="input"
                      value={tokenName}
                      onChange={(e) => setTokenName(e.target.value)}
                      placeholder={t("settings.token_name_ph")}
                    />
                  </div>
                  <div>
                    <label className="label">{t("settings.expires_optional")}</label>
                    <input
                      className="input"
                      type="datetime-local"
                      value={tokenExpiresAt}
                      onChange={(e) => setTokenExpiresAt(e.target.value)}
                    />
                  </div>
                  <div className="flex items-center justify-between pt-4">
                    <button className="btn-secondary" onClick={() => goTo("ci_pick")}>
                      {t("common.back")}
                    </button>
                    <button
                      className="btn-primary inline-flex items-center gap-2"
                      disabled={
                        creatingToken ||
                        !tokenName.trim() ||
                        tokenLimitReached
                      }
                      onClick={async () => {
                        setTokenError(null);
                        setCreatingToken(true);
                        try {
                          const body: {
                            name: string;
                            scopes: string[];
                            expires_at?: string;
                          } = {
                            name: tokenName.trim(),
                            scopes: ["ingest"],
                          };
                          if (tokenExpiresAt) {
                            body.expires_at = new Date(
                              tokenExpiresAt,
                            ).toISOString();
                          }
                          const res = await api<ApiTokenCreated>(
                            "/api/tokens",
                            { method: "POST", body },
                          );
                          setCreatedToken(res);
                          await caps.refetch();
                          toast.success(t("settings.token_created"));
                        } catch (err) {
                          setTokenError(
                            err instanceof Error
                              ? err.message
                              : t("toast.unknown_error"),
                          );
                        } finally {
                          setCreatingToken(false);
                        }
                      }}
                    >
                      {creatingToken && <Loader2 className="h-4 w-4 animate-spin" />}
                      {t("onboarding.token_create_cta")}
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm dark:border-emerald-900 dark:bg-emerald-900/20">
                    <div className="font-semibold">
                      {t("settings.token_created")}
                    </div>
                    <p className="mt-1 text-xs text-slate-600 dark:text-slate-300">
                      {t("settings.token_created_warn")}
                    </p>
                    <code className="mt-2 block break-all rounded bg-white p-2 font-mono text-xs dark:bg-slate-900">
                      {createdToken.token}
                    </code>
                    <button
                      className="btn-secondary mt-2"
                      type="button"
                      onClick={async () => {
                        try {
                          await navigator.clipboard.writeText(
                            createdToken.token,
                          );
                          toast.success(t("common.copied"));
                        } catch {
                          // best-effort copy; user can still select manually
                        }
                      }}
                    >
                      {t("common.copy")}
                    </button>
                  </div>
                  <div className="flex items-center justify-end pt-4">
                    <button
                      className="btn-primary"
                      onClick={() => goTo("quickstart")}
                    >
                      {t("common.continue")}
                    </button>
                  </div>
                </>
              )}
            </div>
          )}

          {stepKey === "quickstart" && (
            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <Sparkles className="h-5 w-5 text-brand-600" />
                <h2 className="text-lg font-semibold">
                  {t("onboarding.quickstart_title", {
                    ci: selectedCi
                      ? t(`onboarding.ci_label.${selectedCi}`)
                      : "",
                  })}
                </h2>
              </div>
              <p className="text-sm text-slate-500">
                {t("onboarding.quickstart_subtitle")}
              </p>
              {createdToken && selectedCi ? (
                <IngestQuickstart
                  token={createdToken.token}
                  lockedProvider={selectedCi}
                />
              ) : (
                <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs dark:border-amber-900/50 dark:bg-amber-900/20">
                  {t("onboarding.quickstart_missing_token")}
                </div>
              )}
              <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-3 text-xs dark:border-slate-700 dark:bg-slate-900/40">
                <div className="font-medium text-slate-700 dark:text-slate-200">
                  {t("onboarding.quickstart_git_upsell_title")}
                </div>
                <p className="mt-1 text-slate-600 dark:text-slate-300">
                  {t("onboarding.quickstart_git_upsell_desc")}
                </p>
                <button
                  type="button"
                  className="mt-2 text-xs font-semibold text-brand-600 hover:underline"
                  onClick={() => {
                    setTrack("git");
                    setStepKey("provider");
                  }}
                >
                  {t("onboarding.quickstart_git_upsell_cta")} →
                </button>
              </div>
              <div className="flex items-center justify-between pt-4">
                <button className="btn-secondary" onClick={() => goTo("token")}>
                  {t("common.back")}
                </button>
                <button className="btn-primary" onClick={() => goTo("messenger")}>
                  {t("common.continue")}
                </button>
              </div>
            </div>
          )}

          {stepKey === "messenger" && (() => {
            const messengerBack: StepKey =
              track === "api"
                ? "quickstart"
                : mode === "oauth"
                  ? "projects"
                  : "connect";
            return (
              <MessengerStep
                bitbucketDcLegacyWarning={
                  ciTarget === "bitbucket" && bbDcLegacyWarning
                }
                onBack={() => goTo(messengerBack)}
                onNext={() => goTo("demo")}
              />
            );
          })()}

          {stepKey === "demo" && (
            <DemoStep
              onDone={async () => {
                await refresh();
                navigate("/dashboard");
              }}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function SelectAllRow({
  total,
  selected,
  onToggle,
}: {
  total: number;
  selected: number;
  onToggle: (checked: boolean) => void;
}) {
  const { t } = useTranslation();
  const all = total > 0 && selected === total;
  const some = selected > 0 && selected < total;
  return (
    <label className="flex items-center gap-3 bg-slate-50 p-3 dark:bg-slate-800/50">
      <input
        type="checkbox"
        checked={all}
        ref={(el) => {
          if (el) el.indeterminate = some;
        }}
        onChange={(e) => onToggle(e.target.checked)}
      />
      <span className="text-sm font-medium">
        {t("onboarding.select_all", { count: total })}
      </span>
    </label>
  );
}

function FlavorCard({
  label,
  description,
  active,
  onClick,
}: {
  label: string;
  description: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        "rounded-xl border p-4 text-left transition-colors",
        active
          ? "border-brand-600 bg-brand-50 dark:bg-brand-900/30"
          : "border-slate-200 bg-white hover:border-brand-300 dark:border-slate-800 dark:bg-slate-900",
      )}
    >
      <div className="flex items-center justify-between">
        <div className="font-medium">{label}</div>
        {active && <Check className="h-4 w-4 text-brand-600" />}
      </div>
      <div className="mt-1 text-sm text-slate-500">{description}</div>
    </button>
  );
}

function ModeCard({
  title,
  bullets,
  active,
  onClick,
  badge,
}: {
  title: string;
  bullets: string[];
  active: boolean;
  onClick: () => void;
  badge?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        "rounded-xl border p-4 text-left transition-colors",
        active
          ? "border-brand-600 bg-brand-50 dark:bg-brand-900/30"
          : "border-slate-200 bg-white hover:border-brand-300 dark:border-slate-800 dark:bg-slate-900",
      )}
    >
      <div className="flex items-center justify-between">
        <div className="font-medium">{title}</div>
        {badge && <span className="badge-green">{badge}</span>}
      </div>
      <ul className="mt-3 space-y-1 text-sm text-slate-500">
        {bullets.map((b) => (
          <li key={b} className="flex items-start gap-2">
            <Circle className="mt-1 h-1.5 w-1.5 flex-shrink-0 fill-current" />
            {b}
          </li>
        ))}
      </ul>
    </button>
  );
}

function WebhookSetupPanel({
  webhookInfo,
  ciTarget,
}: {
  webhookInfo: {
    url: string;
    secret: string;
    hookRegistered: boolean;
    registerError?: string | null;
  };
  ciTarget: CiTarget;
}) {
  const { t } = useTranslation();
  const host = (() => {
    try {
      return new URL(webhookInfo.url).hostname;
    } catch {
      return "";
    }
  })();
  const isLocalHost =
    host === "localhost" || host === "127.0.0.1" || host.endsWith(".local");

  const steps =
    ciTarget === "bitbucket"
      ? [
          t("onboarding.webhook_setup_bitbucket_step1"),
          t("onboarding.webhook_setup_step_url", { url: webhookInfo.url }),
          t("onboarding.webhook_setup_step_secret", { secret: webhookInfo.secret }),
          t("onboarding.webhook_setup_bitbucket_step4"),
          t("onboarding.webhook_setup_bitbucket_step5"),
        ]
      : [
          t("onboarding.webhook_setup_gitlab_step1"),
          t("onboarding.webhook_setup_step_url", { url: webhookInfo.url }),
          t("onboarding.webhook_setup_step_secret", { secret: webhookInfo.secret }),
          t("onboarding.webhook_setup_gitlab_step4"),
          t("onboarding.webhook_setup_gitlab_step5"),
          t("onboarding.webhook_setup_gitlab_step6"),
        ];

  return (
    <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm dark:border-amber-900 dark:bg-amber-900/30">
      <div className="mb-2 font-semibold">
        {ciTarget === "bitbucket"
          ? t("onboarding.finish_webhook_setup_bitbucket")
          : t("onboarding.finish_webhook_setup")}
      </div>
      {webhookInfo.registerError && (
        <p className="mb-2 text-xs text-amber-900/90 dark:text-amber-100/90">
          {t("onboarding.webhook_auto_register_failed", {
            error: webhookInfo.registerError,
          })}
        </p>
      )}
      {isLocalHost && (
        <p className="mb-2 text-xs text-amber-900/90 dark:text-amber-100/90">
          {t("onboarding.webhook_localhost_hint")}
        </p>
      )}
      <ol className="list-decimal space-y-1 pl-5 text-slate-700 dark:text-slate-200">
        {steps.map((step, i) => (
          <li key={i}>{step}</li>
        ))}
      </ol>
      <div className="mt-3 grid grid-cols-1 gap-2 text-xs md:grid-cols-2">
        <Copyable label={t("onboarding.webhook_url")} value={webhookInfo.url} />
        <Copyable label={t("onboarding.secret_token")} value={webhookInfo.secret} />
      </div>
    </div>
  );
}

function MessengerStep({
  bitbucketDcLegacyWarning,
  onBack,
  onNext,
}: {
  bitbucketDcLegacyWarning?: boolean;
  onBack: () => void;
  onNext: () => void;
}) {
  const { t } = useTranslation();
  const [botToken, setBotToken] = useState("");
  const [slackUrl, setSlackUrl] = useState("");
  const [matrixHome, setMatrixHome] = useState("");
  const [matrixToken, setMatrixToken] = useState("");
  const [matrixRoom, setMatrixRoom] = useState("");

  const [connectingTelegram, setConnectingTelegram] = useState(false);
  const [connectingSlack, setConnectingSlack] = useState(false);
  const [connectingMatrix, setConnectingMatrix] = useState(false);

  const [linkInfo, setLinkInfo] = useState<{
    bot_username: string;
    link_code: string;
  } | null>(null);
  const [slackConnected, setSlackConnected] = useState(false);
  const [matrixConnected, setMatrixConnected] = useState(false);

  async function connectTelegram() {
    setConnectingTelegram(true);
    try {
      const res = await api<{
        connection_id: string;
        bot_username: string;
        link_code: string;
        webhook_registered: boolean;
      }>("/api/integrations/telegram/init", {
        method: "POST",
        body: { bot_token: botToken },
      });
      setLinkInfo({ bot_username: res.bot_username, link_code: res.link_code });
      if (res.webhook_registered) {
        toast.success(t("onboarding.telegram_connected"));
      } else {
        toast.warning(t("integrations.telegram_webhook_failed"));
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    } finally {
      setConnectingTelegram(false);
    }
  }

  async function connectSlack() {
    setConnectingSlack(true);
    try {
      await api("/api/integrations/slack/webhook-init", {
        method: "POST",
        body: { webhook_url: slackUrl },
      });
      setSlackConnected(true);
      toast.success(t("onboarding.slack_connected"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    } finally {
      setConnectingSlack(false);
    }
  }

  async function connectMatrix() {
    setConnectingMatrix(true);
    try {
      await api("/api/integrations/matrix/init", {
        method: "POST",
        body: {
          homeserver_url: matrixHome,
          access_token: matrixToken,
          room_id: matrixRoom,
        },
      });
      setMatrixConnected(true);
      toast.success(t("onboarding.matrix_connected"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    } finally {
      setConnectingMatrix(false);
    }
  }

  return (
    <div className="space-y-4">
      {bitbucketDcLegacyWarning && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs dark:border-amber-900/50 dark:bg-amber-900/20">
          {t("onboarding.dc_legacy_warning_bitbucket")}
        </div>
      )}

      <h2 className="text-lg font-semibold">{t("onboarding.messenger_title")}</h2>
      <p className="text-sm text-slate-500">{t("onboarding.messenger_subtitle")}</p>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <div className="rounded-xl border border-slate-200 p-4 dark:border-slate-800">
          <div className="mb-2 font-medium">{t("onboarding.telegram")}</div>
          {linkInfo ? (
            <div className="space-y-2 text-sm">
              <p className="text-slate-500">
                {t("onboarding.telegram_paste_cmd", { bot: linkInfo.bot_username })}
              </p>
              <code className="block rounded bg-slate-100 p-2 text-center text-base font-semibold dark:bg-slate-800">
                /link {linkInfo.link_code}
              </code>
              <p className="text-xs text-slate-400">{t("onboarding.telegram_code_hint")}</p>
            </div>
          ) : (
            <div className="space-y-2">
              <input
                className="input"
                placeholder={t("integrations.telegram_token_label")}
                value={botToken}
                onChange={(e) => setBotToken(e.target.value)}
              />
              <button
                className="btn-primary inline-flex w-full items-center justify-center gap-2"
                disabled={!botToken || connectingTelegram}
                onClick={connectTelegram}
              >
                {connectingTelegram && <Loader2 className="h-4 w-4 animate-spin" />}
                {t("onboarding.connect_telegram")}
              </button>
            </div>
          )}
        </div>

        <div className="rounded-xl border border-slate-200 p-4 dark:border-slate-800">
          <div className="mb-2 font-medium">{t("onboarding.slack")}</div>
          {slackConnected ? (
            <p className="text-sm text-emerald-600">{t("onboarding.slack_connected")}</p>
          ) : (
            <div className="space-y-2">
              <input
                className="input"
                placeholder="https://hooks.slack.com/..."
                value={slackUrl}
                onChange={(e) => setSlackUrl(e.target.value)}
              />
              <button
                className="btn-primary inline-flex w-full items-center justify-center gap-2"
                disabled={!slackUrl || connectingSlack}
                onClick={connectSlack}
              >
                {connectingSlack && <Loader2 className="h-4 w-4 animate-spin" />}
                {t("onboarding.connect_slack")}
              </button>
            </div>
          )}
        </div>

        <div className="rounded-xl border border-slate-200 p-4 dark:border-slate-800">
          <div className="mb-2 font-medium">{t("onboarding.matrix")}</div>
          {matrixConnected ? (
            <p className="text-sm text-emerald-600">{t("onboarding.matrix_connected")}</p>
          ) : (
            <div className="space-y-2">
              <input
                className="input"
                placeholder="https://matrix.example.org"
                value={matrixHome}
                onChange={(e) => setMatrixHome(e.target.value)}
              />
              <input
                className="input"
                type="password"
                placeholder={t("integrations.matrix_token_label")}
                value={matrixToken}
                onChange={(e) => setMatrixToken(e.target.value)}
              />
              <input
                className="input"
                placeholder="!roomid:example.org"
                value={matrixRoom}
                onChange={(e) => setMatrixRoom(e.target.value)}
              />
              <button
                className="btn-primary inline-flex w-full items-center justify-center gap-2"
                disabled={
                  !matrixHome || !matrixToken || !matrixRoom || connectingMatrix
                }
                onClick={connectMatrix}
              >
                {connectingMatrix && <Loader2 className="h-4 w-4 animate-spin" />}
                {t("onboarding.connect_matrix")}
              </button>
            </div>
          )}
        </div>
      </div>

      <div className="flex items-center justify-between pt-4">
        <button className="btn-secondary" onClick={onBack}>
          {t("common.back")}
        </button>
        <button className="btn-primary" onClick={onNext}>
          {t("common.continue")}
        </button>
      </div>
    </div>
  );
}

type DemoNotification = { connection_id: string; channel: string; ok: boolean };

function DemoStep({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const [running, setRunning] = useState<string | null>(null);
  const [result, setResult] = useState<{
    analysis: Record<string, unknown>;
    notifications: DemoNotification[];
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function runDemo(scenario: string) {
    setRunning(scenario);
    setError(null);
    try {
      const res = await api<{
        analysis: Record<string, unknown>;
        notifications: DemoNotification[];
      }>("/api/demo/rca", { method: "POST", body: { scenario } });
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("toast.unknown_error"));
    } finally {
      setRunning(null);
    }
  }

  const scenarios: { id: string; labelKey: string }[] = [
    { id: "npm_install_eacces", labelKey: "onboarding.demo_scenario_npm" },
    { id: "test_assertion", labelKey: "onboarding.demo_scenario_tests" },
    { id: "pip_timeout", labelKey: "onboarding.demo_scenario_pip" },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Sparkles className="h-5 w-5 text-brand-600" />
        <h2 className="text-lg font-semibold">{t("onboarding.demo_title")}</h2>
      </div>
      <p className="text-sm text-slate-500">{t("onboarding.demo_intro")}</p>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
        {scenarios.map((s) => (
          <button
            key={s.id}
            className="btn-secondary inline-flex items-center justify-center gap-2"
            disabled={running !== null}
            onClick={() => runDemo(s.id)}
          >
            {running === s.id && <Loader2 className="h-4 w-4 animate-spin" />}
            {t(s.labelKey)}
          </button>
        ))}
      </div>

      {error && (
        <div className="rounded-lg border border-rose-300 bg-rose-50 p-3 text-sm text-rose-800">
          {error}
        </div>
      )}

      {result && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm dark:border-emerald-900 dark:bg-emerald-900/20">
          <div className="mb-1 font-semibold">{t("onboarding.demo_root_cause")}</div>
          <div className="mb-3">{String(result.analysis.root_cause)}</div>
          <div className="mb-1 font-semibold">{t("onboarding.demo_fix")}</div>
          <div>{String(result.analysis.fix_suggestion)}</div>
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            {result.notifications.length === 0 ? (
              <span className="text-slate-500">{t("onboarding.demo_no_channels")}</span>
            ) : (
              result.notifications.map((n) => (
                <span
                  key={n.connection_id}
                  className={n.ok ? "badge-green" : "badge-red"}
                >
                  {n.channel}:{" "}
                  {n.ok ? t("onboarding.demo_delivered") : t("onboarding.demo_not_sent")}
                </span>
              ))
            )}
          </div>
        </div>
      )}

      <div className="flex items-center justify-end pt-4">
        <button className="btn-primary" onClick={onDone}>
          {t("onboarding.finish")} <ExternalLink className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

function Copyable({ label, value }: { label: string; value: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-2 dark:border-slate-800 dark:bg-slate-900">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
        {label}
      </div>
      <div className="mt-1 flex items-center gap-2">
        <code className="flex-1 truncate text-xs">{value}</code>
        <button
          className="text-xs text-brand-600 hover:underline"
          onClick={async () => {
            await navigator.clipboard.writeText(value);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          }}
        >
          {copied ? t("common.copied") : t("common.copy")}
        </button>
      </div>
    </div>
  );
}
