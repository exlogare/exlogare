export type Me = {
  user: { id: string; email: string; display_name: string | null };
  tenant: { id: string; name: string; slug: string };
  role: "owner" | "admin" | "member" | "viewer";
  onboarded: boolean;
};

export type Capabilities = {
  gitlab_modes: Array<"webhook" | "oauth_polling" | "hybrid">;
  gitlab_oauth_redirect_uri: string;
  max_gitlab_repos: number | null;
  current_gitlab_repos: number;
  github_modes: Array<"webhook" | "oauth_polling" | "hybrid">;
  max_github_repos: number | null;
  current_github_repos: number;
  github_oauth_redirect_uri: string;
  bitbucket_modes: Array<"webhook" | "oauth_polling" | "hybrid">;
  max_bitbucket_repos: number | null;
  current_bitbucket_repos: number;
  bitbucket_oauth_redirect_uri: string;
  gitflic_modes: Array<"webhook" | "oauth_polling" | "hybrid">;
  max_gitflic_repos: number | null;
  current_gitflic_repos: number;
  gitflic_oauth_redirect_uri: string;
  hybrid_allowed: boolean;
  api_keys_allowed: boolean;
  max_api_keys: number | null;
  current_api_keys: number;
  notifications_enabled: boolean;
  outbound_webhooks_enabled: boolean;
  history_retention_days: number;
};

export type SessionResponse = {
  access_token: string;
  token_type: string;
  user: Me["user"];
  tenant: Me["tenant"];
  role: Me["role"];
};

export type WatchProjectsResponse = {
  results: Array<{
    connection_id: string;
    project_id: string;
    project_path: string | null;
    mode: string;
    status: string;
    hook_registered: boolean;
    enabled: boolean;
    error: string | null;
  }>;
  repo_limit_partial: boolean;
};

export type GitLabProject = {
  id: string;
  name: string;
  path_with_namespace: string;
  web_url: string;
  default_branch: string | null;
  last_activity_at: string | null;
};

export type FeedbackChannelKey =
  | "mr_comment"
  | "commit_comment"
  | "issue"
  | "status_check";

export type FeedbackPolicy = Record<FeedbackChannelKey, boolean>;

export type GitLabConnection = {
  id: string;
  base_url: string;
  mode: "webhook" | "oauth_polling" | "hybrid";
  status: "pending_manual" | "active" | "error" | "disabled";
  /** When false, ingestion is off; use PATCH { enabled: true } to reconnect. */
  enabled?: boolean;
  /** Self-hosted GitLab placeholder row — Application ID may be edited via PATCH /oauth-app/:id */
  oauth_app_editable?: boolean;
  oauth_client_id?: string | null;
  external_project_id: string | null;
  external_project_name: string | null;
  external_project_url: string | null;
  last_delivery_at: string | null;
  gitlab_user: Record<string, unknown> | null;
  feedback_override?: Partial<FeedbackPolicy> | null;
  feedback_effective?: FeedbackPolicy | null;
};

/** Same shape as GitLab; API is under `/api/integrations/github/*` */
export type GitHubConnection = {
  id: string;
  base_url: string;
  mode: "webhook" | "oauth_polling" | "hybrid";
  status: "pending_manual" | "active" | "error" | "disabled";
  enabled?: boolean;
  oauth_app_editable?: boolean;
  oauth_client_id?: string | null;
  external_project_id: string | null;
  external_project_name: string | null;
  external_project_url: string | null;
  last_delivery_at: string | null;
  github_user: Record<string, unknown> | null;
  feedback_override?: Partial<FeedbackPolicy> | null;
  feedback_effective?: FeedbackPolicy | null;
};

/** Same shape as GitLab; API is under `/api/integrations/bitbucket/*`. */
export type BitbucketConnection = {
  id: string;
  base_url: string;
  mode: "webhook" | "oauth_polling" | "hybrid";
  status: "pending_manual" | "active" | "error" | "disabled";
  enabled?: boolean;
  oauth_app_editable?: boolean;
  oauth_client_id?: string | null;
  external_project_id: string | null;
  external_project_name: string | null;
  external_project_url: string | null;
  last_delivery_at: string | null;
  bitbucket_user: Record<string, unknown> | null;
  feedback_override?: Partial<FeedbackPolicy> | null;
  feedback_effective?: FeedbackPolicy | null;
};

/** GitFlic CI connection. Same envelope as the other Git providers; */
export type GitFlicConnection = {
  id: string;
  base_url: string;
  mode: "webhook" | "oauth_polling" | "hybrid";
  status: "pending_manual" | "active" | "error" | "disabled";
  enabled?: boolean;
  external_project_id: string | null;
  external_project_name: string | null;
  external_project_url: string | null;
  last_delivery_at: string | null;
  gitflic_user: Record<string, unknown> | null;
  flavor?: "cloud" | "selfhosted";
};

export type NotificationConnection = {
  id: string;
  channel: "telegram" | "slack" | "matrix";
  enabled: boolean;
  target: string | null;
  endpoint: string | null;
  status: string;
  webhook_registered?: boolean | null;
  link_code?: string | null;
  bot_username?: string | null;
};

/** A tenant-managed outbound HTTP push subscription. */
export type OutboundWebhookSubscription = {
  id: string;
  name: string;
  url: string;
  events: string[];
  enabled: boolean;
  consecutive_failures: number;
  last_delivery_at: string | null;
  last_status: number | null;
  last_error: string | null;
  disabled_at: string | null;
};

/** Shape returned by ``POST /…/outbound-webhooks`` and ``…/rotate-secret``. */
export type OutboundWebhookCreated = OutboundWebhookSubscription & {
  secret: string;
};

export type OverviewStats = {
  failures_detected: number;
  analyses_completed: number;
  rca_count: number;
  severity_counts: Record<string, number>;
  avg_time_to_rca_seconds: number | null;
  p50_time_to_rca_seconds: number | null;
  p90_time_to_rca_seconds: number | null;
  window_days: number;
};

export type TimeseriesPoint = { date: string; failures: number };
export type TopProject = {
  project_id: string | null;
  project_path: string | null;
  failures: number;
  analyses: number;
};
export type TopRootCause = { root_cause: string; severity: string; count: number };

export type Analysis = {
  id: string;
  provider: string;
  source: string | null;
  ci_run_id: string;
  ci_job_id: string | null;
  project_id: string | null;
  project_path: string | null;
  project_web_url: string | null;
  pipeline_url: string | null;
  job_url: string | null;
  mr_iid: string | null;
  root_cause: string;
  explanation: string;
  fix_suggestion: string;
  severity: "low" | "medium" | "high";
  confidence: number;
  created_at: string;
};

export type AnalysesResponse = {
  items: Analysis[];
  total: number;
  limit: number;
  offset: number;
};

export type ClusterStatus = "active" | "acknowledged" | "resolved";

export type Cluster = {
  id: string;
  fingerprint_hash: string;
  last_root_cause: string;
  last_severity: "low" | "medium" | "high";
  count: number;
  first_seen_at: string;
  last_seen_at: string;
  status: ClusterStatus;
  last_analysis_id: string | null;
  acknowledged_at: string | null;
  resolved_at: string | null;
};

export type ClustersResponse = {
  items: Cluster[];
  total: number;
  limit: number;
  offset: number;
};

export type ClustersStats = {
  active: number;
  acknowledged: number;
  resolved: number;
};

export type ClusterBadge = {
  cluster_id: string;
  count: number;
  status: ClusterStatus;
};

export type ClustersBadgesResponse = {
  badges: Record<string, ClusterBadge>;
};

