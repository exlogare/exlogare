import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Layers, RotateCcw, Check, X } from "lucide-react";
import { api } from "../lib/api";
import type {
  Cluster,
  ClustersResponse,
  ClustersStats,
  ClusterStatus,
} from "../lib/types";

const TABS: ClusterStatus[] = ["active", "acknowledged", "resolved"];

const PAGE_SIZE = 25;

/** Recurring-issues (failure clusters) page. */
export default function ClustersPage() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<ClusterStatus>("active");
  const [offset, setOffset] = useState(0);

  const stats = useQuery({
    queryKey: ["clusters", "stats"],
    queryFn: () => api<ClustersStats>("/api/clusters/stats"),
  });

  const list = useQuery({
    queryKey: ["clusters", "list", { tab, offset }],
    queryFn: () =>
      api<ClustersResponse>(
        `/api/clusters?status=${tab}&limit=${PAGE_SIZE}&offset=${offset}`,
      ),
  });

  const transition = useMutation({
    mutationFn: async (input: { id: string; action: "acknowledge" | "resolve" | "reopen" }) =>
      api<Cluster>(`/api/clusters/${input.id}/${input.action}`, { method: "POST" }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["clusters", "list"] }),
        queryClient.invalidateQueries({ queryKey: ["clusters", "stats"] }),
      ]);
    },
  });

  const showEmptyHint = !list.isLoading && (list.data?.items.length ?? 0) === 0;

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">{t("clusters.title")}</h1>
          <p className="text-sm text-slate-500">{t("clusters.subtitle")}</p>
        </div>
      </header>

      <nav className="flex gap-2 border-b border-slate-200 pb-2 text-sm dark:border-slate-800">
        {TABS.map((key) => {
          const isActive = tab === key;
          const count = stats.data ? stats.data[key] : null;
          return (
            <button
              key={key}
              type="button"
              onClick={() => {
                setTab(key);
                setOffset(0);
              }}
              className={
                isActive
                  ? "rounded-t-lg border-b-2 border-brand-600 px-3 py-1.5 font-medium text-brand-700 dark:text-brand-200"
                  : "px-3 py-1.5 text-slate-500 hover:text-slate-700 dark:hover:text-slate-200"
              }
            >
              {t(`clusters.tab_${key}`)}
              {count !== null && (
                <span className="ml-1 rounded bg-slate-100 px-1.5 text-[11px] text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      <div className="card">
        {list.isLoading ? (
          <p className="text-sm text-slate-500">{t("common.loading")}</p>
        ) : showEmptyHint ? (
          <EmptyState tab={tab} />
        ) : (
          <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
          <table className="w-full min-w-[720px] table-fixed text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="w-24 py-2">{t("clusters.severity")}</th>
                <th className="py-2">{t("clusters.last_root_cause")}</th>
                <th className="w-20 py-2 text-right">{t("clusters.count")}</th>
                <th className="w-40 py-2">{t("clusters.last_seen")}</th>
                <th className="w-44 py-2">{t("clusters.actions")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
              {list.data!.items.map((c) => (
                <ClusterRow
                  key={c.id}
                  cluster={c}
                  locale={i18n.language}
                  onAction={(action) => transition.mutate({ id: c.id, action })}
                  busy={transition.isPending}
                />
              ))}
            </tbody>
          </table>
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-slate-500">
        <div>
          {t("clusters.total_showing", {
            total: list.data?.total ?? 0,
            from: list.data?.items.length ? offset + 1 : 0,
            to: Math.min(list.data?.total ?? 0, offset + PAGE_SIZE),
          })}
        </div>
        <div className="flex gap-2">
          <button
            className="btn-secondary"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          >
            {t("common.previous")}
          </button>
          <button
            className="btn-secondary"
            disabled={!list.data || offset + PAGE_SIZE >= (list.data.total ?? 0)}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            {t("common.next")}
          </button>
        </div>
      </div>
    </div>
  );
}

function ClusterRow({
  cluster,
  locale,
  onAction,
  busy,
}: {
  cluster: Cluster;
  locale: string;
  onAction: (action: "acknowledge" | "resolve" | "reopen") => void;
  busy: boolean;
}) {
  const { t } = useTranslation();
  const lastSeenLabel = useMemo(
    () => new Date(cluster.last_seen_at).toLocaleString(locale),
    [cluster.last_seen_at, locale],
  );
  const detailHref = cluster.last_analysis_id
    ? `/dashboard/analyses/${cluster.last_analysis_id}`
    : null;

  return (
    <tr className="hover:bg-slate-50 dark:hover:bg-slate-800">
      <td className="py-3 pr-2">
        <span className={severityBadge(cluster.last_severity)}>
          {cluster.last_severity}
        </span>
      </td>
      <td className="py-3 pr-2">
        {detailHref ? (
          <Link
            to={detailHref}
            className="block truncate font-medium text-brand-700 hover:underline"
          >
            {cluster.last_root_cause}
          </Link>
        ) : (
          <span className="block truncate font-medium text-slate-700 dark:text-slate-200">
            {cluster.last_root_cause}
          </span>
        )}
        <div className="truncate text-xs text-slate-500">
          {t("clusters.fingerprint")}: {cluster.fingerprint_hash.slice(0, 12)}…
        </div>
      </td>
      <td className="py-3 pr-2 text-right tabular-nums">
        <span className="rounded bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-700 dark:bg-slate-800 dark:text-slate-200">
          ×{cluster.count}
        </span>
      </td>
      <td className="py-3 pr-2 text-slate-500">{lastSeenLabel}</td>
      <td className="py-3 pr-2">
        <div className="flex flex-wrap gap-1">
          {cluster.status === "active" && (
            <>
              <button
                type="button"
                className="btn-secondary !py-1 !text-xs"
                onClick={() => onAction("acknowledge")}
                disabled={busy}
                title={t("clusters.help_acknowledge")}
              >
                <Check className="h-3 w-3" />
                {t("clusters.action_acknowledge")}
              </button>
              <button
                type="button"
                className="btn-secondary !py-1 !text-xs"
                onClick={() => onAction("resolve")}
                disabled={busy}
                title={t("clusters.help_resolve")}
              >
                <X className="h-3 w-3" />
                {t("clusters.action_resolve")}
              </button>
            </>
          )}
          {cluster.status !== "active" && (
            <button
              type="button"
              className="btn-secondary !py-1 !text-xs"
              onClick={() => onAction("reopen")}
              disabled={busy}
              title={t("clusters.help_reopen")}
            >
              <RotateCcw className="h-3 w-3" />
              {t("clusters.action_reopen")}
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}

function EmptyState({ tab }: { tab: ClusterStatus }) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col items-center gap-2 py-10 text-center">
      <Layers className="h-8 w-8 text-slate-400" />
      <p className="text-sm text-slate-500">{t(`clusters.empty_${tab}`)}</p>
    </div>
  );
}

function severityBadge(sev: string) {
  if (sev === "high") return "badge-red";
  if (sev === "medium") return "badge-yellow";
  if (sev === "low") return "badge-green";
  return "badge-slate";
}
