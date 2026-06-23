export type Me = {
  user: { id: string; email: string; display_name: string | null };
  tenant: { id: string; name: string; slug: string };
  role: "owner" | "admin" | "member" | "viewer";
  onboarded: boolean;
};

export type PlanCapabilities = {
  plan: string;
  effective_plan: "free" | "startup" | "pro" | "enterprise" | "payg";
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
  support_level: string;
  quota: {
    can_run_analysis: boolean;
    block_reason: string | null;
    prepaid_analyses_remaining: number;
    lifetime_analyses_used: number;
    monthly_analyses_used: number;
    lifetime_cap?: number;
    monthly_cap?: number;
    unlimited?: boolean;
  };
};

export type PackItem = { size: number; price_rub: number; currency: string };

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

export type RecurringAlert = {
  kind: "retry" | "terminal";
  error_code: string | null;
  active_until: string | null;
  subscription_id: string;
};

export type AutoRenewInfo = {
  subscription_id: string;
  plan: string;
  auto_renew: boolean;
  active_until: string | null;
  recurring_state: "pending" | "activated" | "cancelled" | string;
};

/** Open YooKassa renewal invoice (set by the celery sweep task 5 days */
export type YkPendingInvoice = {
  invoice_id: string;
  invoice_url: string;
  expires_at: string | null;
};

export type PaymentProvider = "intellectmoney" | "yookassa";

export type BillingSummary = {
  plan: string;
  plan_limit: number | null;
  price_per_run_rub: number;
  runs_this_month: number;
  free_tier_remaining: number;
  month_cost_rub: number;
  balance_rub: number;
  last_payment: Record<string, unknown> | null;
  prepaid_analyses_remaining: number;
  lifetime_analyses_used: number;
  lifetime_cap: number | null;
  monthly_cap: number | null;
  quota_exhausted: boolean;
  quota_block_reason: string | null;
  recurring_alert: RecurringAlert | null;
  auto_renew: AutoRenewInfo | null;
  billing_enabled?: boolean;
  payment_provider?: PaymentProvider;
  yk_pending_invoice?: YkPendingInvoice | null;
};

export type PaymentIntentWidgetParams = {
  EshopId: string;
  OrderId: string;
  ServiceName: string;
  RecipientCurrency: string;
  RecipientAmount: string;
  Email: string;
  SuccessUrl: string;
  FailUrl: string;
  UserField_1: string;
  UserField_2: string;
  Preference: string | null;
  RecurringType: string | null;
};

/** Discriminated union over ``mode`` so the SPA can branch cheaply: */
export type PaymentIntentResponse = {
  order_id: string;
  amount_rub: number;
  currency: string;
  service_name: string;
  kind: "pack" | "plan_upgrade" | "plan_renew" | string;
  provider: PaymentProvider;
  mode: "widget" | "redirect";
  widget: PaymentIntentWidgetParams | null;
  redirect_url: string | null;
};

export type PaymentStatusResponse = {
  order_id: string;
  status: "pending" | "succeeded" | "failed" | "cancelled" | string;
  kind: "pack" | "plan_upgrade" | "plan_renew" | string | null;
  amount_rub: number;
  created_at: string;
  paid_at: string | null;
};

export type PaymentIntentRequest =
  | { kind: "pack"; pack_size: number; email?: string | null }
  | {
      kind: "plan";
      plan: "startup" | "pro";
      auto_renew?: boolean;
      email?: string | null;
    };

export type ChangePlanResponse = {
  plan: string;
  effective_at: string | null;
  pending: boolean;
  note: string | null;
  requires_payment: boolean;
  target_plan: string | null;
};

export type Payment = {
  id: string;
  amount: number;
  currency: string;
  status: string;
  description: string | null;
  paid_at: string | null;
  created_at: string;
};

export type Invoice = { period: string; runs: number; cost_rub: number };

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

export type UsageSummary = {
  tenant_id: string;
  window_days: number;
  runs_in_window: number;
  runs_this_month: number;
  lifetime_used: number;
  lifetime_cap: number | null;
  monthly_cap: number | null;
  remaining: number;
  prepaid_remaining: number;
  plan: string;
  unlimited: boolean;
};

export type UsageTimeseriesPoint = { date: string; runs: number };

export type SupportTicketPriority = "low" | "normal" | "high";

export type SupportTicketCreateRequest =
  | {
      kind: "support";
      priority: SupportTicketPriority;
      subject: string;
      category: "billing" | "bug" | "integration" | "other";
      message: string;
      analysis_id?: string | null;
    }
  | {
      kind: "sales";
      priority: SupportTicketPriority;
      company: string;
      team_size: "1-10" | "11-50" | "51-200" | "200+";
      expected_volume?: number | null;
      phone?: string | null;
      message: string;
    };

export type SupportTicketCreateResponse = { id: string; status: string };

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

