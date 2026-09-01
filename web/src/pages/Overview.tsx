/** Overview — first page after sign-in. */
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  BarChart3,
  BookOpen,
  CheckCircle2,
  Clock3,
  ExternalLink,
  Flame,
  Folder,
  Layers,
  Link2,
  Plug,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import clsx from "clsx";
import { api } from "../lib/api";
import { KpiCard } from "../components/KpiCard";
import { useAuth } from "../lib/auth";
import { docsUrl } from "../lib/requisites";
import type {
  Analysis,
  AnalysesResponse,
  ClustersStats,
  OverviewStats,
  TimeseriesPoint,
  TopProject,
} from "../lib/types";

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds - m * 60);
  return `${m}m ${s}s`;
}

function fillMissingDays(
  points: TimeseriesPoint[],
  days: number,
): TimeseriesPoint[] {
  const map = new Map(points.map((p) => [p.date, p.failures]));
  const out: TimeseriesPoint[] = [];
  const today = new Date();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    const iso = d.toISOString().slice(0, 10);
    out.push({ date: iso, failures: map.get(iso) ?? 0 });
  }
  return out;
}

export default function OverviewPage() {
  const { t, i18n } = useTranslation();
  const { me } = useAuth();

  const stats = useQuery({
    queryKey: ["stats", "overview"],
    queryFn: () => api<OverviewStats>("/api/stats/overview?days=30"),
  });
  const ts = useQuery({
    queryKey: ["stats", "timeseries", 30],
    queryFn: () => api<TimeseriesPoint[]>("/api/stats/timeseries?days=30"),
  });
  const topProjects = useQuery({
    queryKey: ["stats", "top-projects", 5],
    queryFn: () => api<TopProject[]>("/api/stats/top-projects?days=30&limit=5"),
  });
  const recent = useQuery({
    queryKey: ["analyses", "recent"],
    queryFn: () => api<AnalysesResponse>("/api/analyses?limit=5"),
  });
  const clusters = useQuery({
    queryKey: ["clusters", "stats"],
    queryFn: () => api<ClustersStats>("/api/clusters/stats"),
  });

  const totalAnalyses = recent.data?.total ?? 0;
  const isFreshTenant =
    !stats.isLoading &&
    !recent.isLoading &&
    totalAnalyses === 0 &&
    (stats.data?.failures_detected ?? 0) === 0;

  const greeting = me?.user.display_name?.trim() || me?.user.email?.split("@")[0];

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="text-2xl font-semibold">
            {greeting
              ? t("overview.greeting", { name: greeting })
              : t("overview.title")}
          </h1>
          <p className="text-sm text-slate-500">{t("overview.subtitle")}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300">
            {t("overview.window_30d")}
          </span>
        </div>
      </header>


      {isFreshTenant ? (
        <EmptyState />
      ) : (
        <>
          <KpiGrid
            stats={stats.data}
            language={i18n.language}
            isLoading={stats.isLoading}
          />

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <section className="card-hover lg:col-span-2">
              <div className="mb-2 flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <TrendingUp className="h-5 w-5 text-brand-600" />
                  <h2 className="text-lg font-semibold">
                    {t("overview.failures_per_day")}
                  </h2>
                </div>
                <Link
                  to="/dashboard/stats"
                  className="text-xs font-medium text-brand-600 hover:underline"
                >
                  {t("overview.open_stats")} →
                </Link>
              </div>
              <FailuresTrend
                points={ts.data ?? []}
                isLoading={ts.isLoading}
                locale={i18n.language}
              />
            </section>

            <div className="space-y-4">
              <RecurringCard
                stats={clusters.data}
                isLoading={clusters.isLoading}
              />
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <TopProjectsCard
              projects={topProjects.data ?? []}
              isLoading={topProjects.isLoading}
              language={i18n.language}
            />
            <SeverityCard
              stats={stats.data}
              isLoading={stats.isLoading}
              language={i18n.language}
            />
          </div>

          <RecentAnalyses
            items={recent.data?.items ?? []}
            isLoading={recent.isLoading}
            language={i18n.language}
          />
        </>
      )}

      <QuickActions />
    </div>
  );
}

function EmptyState() {
  const { t, i18n } = useTranslation();
  return (
    <section className="relative overflow-hidden rounded-2xl border border-brand-200 bg-gradient-to-br from-brand-50 via-white to-sky-50 p-8 dark:border-brand-800 dark:from-brand-950/40 dark:via-slate-900 dark:to-sky-950/30">
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3 lg:items-center">
        <div className="lg:col-span-2">
          <div className="mb-2 inline-flex items-center gap-2 rounded-full bg-brand-600/10 px-3 py-1 text-xs font-semibold text-brand-700 dark:text-brand-300">
            <Sparkles className="h-3.5 w-3.5" />
            {t("overview.empty_kicker")}
          </div>
          <h2 className="text-2xl font-semibold tracking-tight">
            {t("overview.empty_title")}
          </h2>
          <p className="mt-2 max-w-xl text-sm text-slate-600 dark:text-slate-300">
            {t("overview.empty_body")}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link
              to="/dashboard/integrations"
              className="btn-primary inline-flex items-center gap-2"
            >
              <Plug className="h-4 w-4" />
              {t("overview.empty_cta_integrations")}
            </Link>
            <a
              href={docsUrl("api", i18n.language)}
              target="_blank"
              rel="noopener noreferrer"
              className="btn-secondary inline-flex items-center gap-2"
            >
              <BookOpen className="h-4 w-4" />
              {t("overview.empty_cta_docs")}
            </a>
          </div>
        </div>
        <ul className="space-y-2 text-sm text-slate-700 dark:text-slate-200">
          {[t("overview.empty_b1"), t("overview.empty_b2"), t("overview.empty_b3")].map(
            (b) => (
              <li
                key={b}
                className="flex items-start gap-2 rounded-xl border border-white/60 bg-white/60 p-3 backdrop-blur-sm dark:border-slate-800/60 dark:bg-slate-900/60"
              >
                <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0 text-emerald-500" />
                <span>{b}</span>
              </li>
            ),
          )}
        </ul>
      </div>
    </section>
  );
}

function KpiGrid({
  stats,
  language,
  isLoading,
}: {
  stats: OverviewStats | undefined;
  language: string;
  isLoading: boolean;
}) {
  const { t } = useTranslation();
  const fmt = (n: number | undefined): string =>
    typeof n === "number" ? n.toLocaleString(language) : isLoading ? "…" : "0";

  return (
    <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <KpiCard
        icon={<AlertTriangle className="h-4 w-4" />}
        label={t("overview.failures_detected")}
        value={fmt(stats?.failures_detected)}
        accent="rose"
      />
      <KpiCard
        icon={<CheckCircle2 className="h-4 w-4" />}
        label={t("overview.analyses_completed")}
        value={fmt(stats?.analyses_completed)}
        accent="emerald"
      />
      <KpiCard
        icon={<Flame className="h-4 w-4" />}
        label={t("overview.high_severity")}
        value={fmt(stats?.severity_counts?.high)}
        accent="rose"
      />
      <KpiCard
        icon={<Clock3 className="h-4 w-4" />}
        label={t("overview.p90_time_to_rca")}
        value={formatDuration(stats?.p90_time_to_rca_seconds ?? null)}
        accent="brand"
      />
    </section>
  );
}

function FailuresTrend({
  points,
  isLoading,
  locale,
}: {
  points: TimeseriesPoint[];
  isLoading: boolean;
  locale: string;
}) {
  const { t } = useTranslation();
  const filled = useMemo(() => fillMissingDays(points, 30), [points]);
  const total = filled.reduce((acc, p) => acc + p.failures, 0);
  const peak = filled.reduce((m, p) => Math.max(m, p.failures), 0);
  const avg = total > 0 ? total / 30 : 0;

  if (isLoading) {
    return (
      <div className="h-[220px] w-full animate-pulse rounded-md bg-slate-100 dark:bg-slate-800/50" />
    );
  }
  if (total === 0) {
    return (
      <div className="flex h-[220px] flex-col items-center justify-center gap-2 rounded-md border border-dashed border-slate-200 text-sm text-slate-500 dark:border-slate-800">
        <Activity className="h-5 w-5" />
        <span>{t("overview.no_failures_window")}</span>
      </div>
    );
  }

  const data = filled.map((p) => {
    const d = new Date(`${p.date}T12:00:00`);
    return {
      date: p.date,
      label: d.toLocaleDateString(locale, { month: "short", day: "numeric" }),
      fullLabel: d.toLocaleDateString(locale, { dateStyle: "medium" }),
      failures: p.failures,
    };
  });

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500">
        <span>
          {t("overview.trend_total", { value: total.toLocaleString(locale) })}
        </span>
        <span>{t("overview.trend_avg", { value: avg.toFixed(1) })}</span>
        <span>{t("overview.trend_peak", { value: peak.toLocaleString(locale) })}</span>
      </div>
      <div className="h-[220px] w-full min-w-0">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 4 }}>
            <defs>
              <linearGradient id="overview-failures-grad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#e11d48" stopOpacity={0.35} />
                <stop offset="95%" stopColor="#e11d48" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid
              strokeDasharray="3 3"
              vertical={false}
              className="stroke-slate-200 dark:stroke-slate-700"
            />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 11, fill: "currentColor" }}
              className="text-slate-500"
              interval="preserveStartEnd"
              minTickGap={28}
            />
            <YAxis
              allowDecimals={false}
              width={32}
              tick={{ fontSize: 11, fill: "currentColor" }}
              className="text-slate-500"
            />
            <Tooltip
              contentStyle={{
                borderRadius: "8px",
                border: "1px solid rgb(226 232 240)",
                fontSize: "12px",
              }}
              labelFormatter={(label, payload) => {
                const row = payload?.[0]?.payload as { fullLabel?: string } | undefined;
                return row?.fullLabel ?? String(label);
              }}
              formatter={(value: number | string) => [
                value,
                t("overview.failures_detected"),
              ]}
            />
            <Area
              type="monotone"
              dataKey="failures"
              stroke="#e11d48"
              strokeWidth={2}
              fill="url(#overview-failures-grad)"
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function RecurringCard({
  stats,
  isLoading,
}: {
  stats: ClustersStats | undefined;
  isLoading: boolean;
}) {
  const { t } = useTranslation();
  if (isLoading) {
    return <div className="card h-32 animate-pulse" />;
  }
  const active = stats?.active ?? 0;
  const acknowledged = stats?.acknowledged ?? 0;
  const resolved = stats?.resolved ?? 0;

  return (
    <Link
      to="/dashboard/clusters"
      className="card-hover block transition-colors hover:border-brand-400"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Layers className="h-5 w-5 text-brand-600" />
          <h3 className="text-base font-semibold">
            {t("overview.recurring_title")}
          </h3>
        </div>
        <ArrowUpRight className="h-4 w-4 text-slate-400" />
      </div>

      <div className="mt-3 grid grid-cols-3 gap-2 text-center">
        <Stat label={t("clusters.tab_active")} value={active} tone="rose" />
        <Stat
          label={t("clusters.tab_acknowledged")}
          value={acknowledged}
          tone="amber"
        />
        <Stat
          label={t("clusters.tab_resolved")}
          value={resolved}
          tone="emerald"
        />
      </div>
    </Link>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "rose" | "amber" | "emerald";
}) {
  const tones = {
    rose: "text-rose-600 dark:text-rose-400",
    amber: "text-amber-600 dark:text-amber-400",
    emerald: "text-emerald-600 dark:text-emerald-400",
  } as const;
  return (
    <div className="rounded-lg border border-slate-100 bg-slate-50 p-2 dark:border-slate-800 dark:bg-slate-900/40">
      <div className={clsx("text-xl font-semibold", tones[tone])}>{value}</div>
      <div className="text-[10px] uppercase tracking-wide text-slate-500">
        {label}
      </div>
    </div>
  );
}

function TopProjectsCard({
  projects,
  isLoading,
  language,
}: {
  projects: TopProject[];
  isLoading: boolean;
  language: string;
}) {
  const { t } = useTranslation();
  const max = projects.reduce((m, p) => Math.max(m, p.analyses), 0);

  return (
    <section className="card-hover">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Folder className="h-5 w-5 text-brand-600" />
          <h2 className="text-lg font-semibold">{t("overview.top_projects")}</h2>
        </div>
        <Link
          to="/dashboard/stats"
          className="text-xs font-medium text-brand-600 hover:underline"
        >
          {t("overview.view_all")} →
        </Link>
      </div>

      {isLoading ? (
        <p className="text-sm text-slate-500">{t("common.loading")}</p>
      ) : projects.length === 0 ? (
        <p className="text-sm text-slate-500">{t("overview.no_projects")}</p>
      ) : (
        <ul className="space-y-2">
          {projects.map((p) => {
            const label = p.project_path ?? p.project_id ?? t("overview.unknown_project");
            const pct = max > 0 ? Math.round((p.analyses / max) * 100) : 0;
            return (
              <li key={p.project_id ?? label} className="space-y-1">
                <div className="flex items-center justify-between gap-2 text-sm">
                  <span className="min-w-0 truncate font-medium">{label}</span>
                  <span className="text-xs text-slate-500">
                    {p.analyses.toLocaleString(language)}
                  </span>
                </div>
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                  <div
                    className="h-full rounded-full bg-brand-500"
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function SeverityCard({
  stats,
  isLoading,
  language,
}: {
  stats: OverviewStats | undefined;
  isLoading: boolean;
  language: string;
}) {
  const { t } = useTranslation();
  const counts = stats?.severity_counts ?? {};
  const high = counts.high ?? 0;
  const medium = counts.medium ?? 0;
  const low = counts.low ?? 0;
  const total = high + medium + low;

  return (
    <section className="card-hover">
      <div className="mb-3 flex items-center gap-2">
        <BarChart3 className="h-5 w-5 text-brand-600" />
        <h2 className="text-lg font-semibold">
          {t("overview.severity_breakdown")}
        </h2>
      </div>

      {isLoading ? (
        <p className="text-sm text-slate-500">{t("common.loading")}</p>
      ) : total === 0 ? (
        <p className="text-sm text-slate-500">{t("overview.no_data")}</p>
      ) : (
        <div className="space-y-3">
          <SeverityBar
            label={t("analyses.high")}
            value={high}
            total={total}
            color="bg-rose-500"
            language={language}
          />
          <SeverityBar
            label={t("analyses.medium")}
            value={medium}
            total={total}
            color="bg-amber-500"
            language={language}
          />
          <SeverityBar
            label={t("analyses.low")}
            value={low}
            total={total}
            color="bg-emerald-500"
            language={language}
          />
        </div>
      )}
    </section>
  );
}

function SeverityBar({
  label,
  value,
  total,
  color,
  language,
}: {
  label: string;
  value: number;
  total: number;
  color: string;
  language: string;
}) {
  const pct = total > 0 ? Math.round((value / total) * 100) : 0;
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-sm">
        <span className="capitalize">{label}</span>
        <span className="text-xs text-slate-500">
          {value.toLocaleString(language)} · {pct}%
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div className={clsx("h-full", color)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function RecentAnalyses({
  items,
  isLoading,
  language,
}: {
  items: Analysis[];
  isLoading: boolean;
  language: string;
}) {
  const { t } = useTranslation();
  return (
    <section className="card">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Sparkles className="h-5 w-5 text-brand-600" />
          <h2 className="text-lg font-semibold">{t("overview.recent_rcas")}</h2>
        </div>
        <Link
          className="text-sm text-brand-600 hover:underline"
          to="/dashboard/analyses"
        >
          {t("overview.view_all")}
        </Link>
      </div>

      {isLoading ? (
        <p className="text-sm text-slate-500">{t("common.loading")}</p>
      ) : items.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500 dark:border-slate-700">
          {t("overview.empty")}
        </div>
      ) : (
        <ul className="divide-y divide-slate-200 dark:divide-slate-800">
          {items.map((a) => (
            <li
              key={a.id}
              className="flex items-start gap-3 py-3 transition-colors hover:bg-slate-50/60 dark:hover:bg-slate-800/40"
            >
              <span className={severityBadge(a.severity)}>
                {a.severity.toUpperCase()}
              </span>
              <div className="flex-1 min-w-0">
                <div className="truncate text-sm font-medium">
                  {a.root_cause}
                </div>
                <div className="truncate text-xs text-slate-500">
                  {a.provider}
                  {(a.project_path || a.project_id) && (
                    <>
                      <span aria-hidden> · </span>
                      <span>{a.project_path ?? a.project_id}</span>
                    </>
                  )}
                  <span aria-hidden> · </span>
                  <span>
                    {t("overview.run")} #{a.ci_run_id}
                  </span>
                  {a.ci_job_id && (
                    <>
                      <span aria-hidden> · </span>
                      <span>
                        {t("analyses.job")} #{a.ci_job_id}
                      </span>
                    </>
                  )}
                  <span aria-hidden> · </span>
                  {new Date(a.created_at).toLocaleString(language)}
                </div>
              </div>
              <div className="flex items-center gap-3 text-xs">
                {a.pipeline_url && (
                  <a
                    href={a.pipeline_url}
                    target="_blank"
                    className="text-brand-600 hover:underline"
                    rel="noreferrer"
                  >
                    {t("overview.pipeline")}{" "}
                    <ExternalLink className="inline h-3 w-3" />
                  </a>
                )}
                <Link
                  to={`/dashboard/analyses/${a.id}`}
                  className="text-brand-600 hover:underline"
                >
                  {t("overview.details")}
                </Link>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function QuickActions() {
  const { t, i18n } = useTranslation();
  type Action = {
    to: string;
    icon: typeof Link2;
    title: string;
    desc: string;
    external?: boolean;
  };
  const actions: Action[] = [
    {
      to: "/dashboard/integrations",
      icon: Link2,
      title: t("overview.qa_integrations_title"),
      desc: t("overview.qa_integrations_desc"),
    },
    {
      to: "/dashboard/stats",
      icon: BarChart3,
      title: t("overview.qa_stats_title"),
      desc: t("overview.qa_stats_desc"),
    },
    {
      to: "/dashboard/clusters",
      icon: Layers,
      title: t("overview.qa_clusters_title"),
      desc: t("overview.qa_clusters_desc"),
    },
    {
      to: docsUrl("", i18n.language),
      icon: BookOpen,
      title: t("overview.qa_docs_title"),
      desc: t("overview.qa_docs_desc"),
      external: true,
    },
  ];
  const cardClass =
    "group flex items-start gap-3 rounded-2xl border border-slate-200 bg-white p-4 transition-all hover:-translate-y-0.5 hover:border-brand-400 hover:shadow-md dark:border-slate-800 dark:bg-slate-900 dark:hover:border-brand-500";
  return (
    <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {actions.map(({ to, icon: Icon, title, desc, external }) => {
        const inner = (
          <>
            <span className="inline-flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-brand-100 text-brand-700 dark:bg-brand-900/40 dark:text-brand-300">
              <Icon className="h-5 w-5" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1 text-sm font-semibold">
                <span className="truncate">{title}</span>
                <ArrowUpRight className="h-3.5 w-3.5 text-slate-400 transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5" />
              </div>
              <div className="mt-0.5 text-xs text-slate-500">{desc}</div>
            </div>
          </>
        );
        return external ? (
          <a
            key={to}
            href={to}
            target="_blank"
            rel="noopener noreferrer"
            className={cardClass}
          >
            {inner}
          </a>
        ) : (
          <Link key={to} to={to} className={cardClass}>
            {inner}
          </Link>
        );
      })}
    </section>
  );
}

function severityBadge(sev: string): string {
  if (sev === "high") return "badge-red";
  if (sev === "medium") return "badge-yellow";
  if (sev === "low") return "badge-green";
  return "badge-slate";
}

