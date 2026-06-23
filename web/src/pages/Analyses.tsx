import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api } from "../lib/api";
import type {
  AnalysesResponse,
  ClusterBadge,
  ClustersBadgesResponse,
} from "../lib/types";

export default function AnalysesPage() {
  const { t, i18n } = useTranslation();
  const [severity, setSeverity] = useState<string>("");
  const [offset, setOffset] = useState(0);
  const limit = 25;
  const q = useQuery({
    queryKey: ["analyses", { severity, offset }],
    queryFn: () =>
      api<AnalysesResponse>(
        `/api/analyses?limit=${limit}&offset=${offset}${severity ? `&severity=${severity}` : ""}`,
      ),
  });

  const analysisIds = (q.data?.items ?? []).map((a) => a.id);
  const badges = useQuery({
    queryKey: ["analyses", "badges", analysisIds],
    enabled: analysisIds.length > 0,
    queryFn: () => {
      const params = analysisIds
        .map((id) => `analysis_id=${encodeURIComponent(id)}`)
        .join("&");
      return api<ClustersBadgesResponse>(`/api/clusters/badges?${params}`);
    },
  });

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">{t("analyses.title")}</h1>
          <p className="text-sm text-slate-500">{t("analyses.subtitle")}</p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-sm text-slate-500">{t("analyses.severity")}</label>
          <select
            className="input w-32"
            value={severity}
            onChange={(e) => {
              setOffset(0);
              setSeverity(e.target.value);
            }}
          >
            <option value="">{t("analyses.all")}</option>
            <option value="high">{t("analyses.high")}</option>
            <option value="medium">{t("analyses.medium")}</option>
            <option value="low">{t("analyses.low")}</option>
          </select>
        </div>
      </header>

      <div className="card">
        {q.isLoading ? (
          <p className="text-sm text-slate-500">{t("common.loading")}</p>
        ) : (q.data?.items ?? []).length === 0 ? (
          <p className="text-sm text-slate-500">{t("analyses.empty_filter")}</p>
        ) : (
          <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
          <table className="w-full min-w-[640px] table-fixed text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="w-24 py-2">{t("analyses.severity")}</th>
                <th className="py-2">{t("analyses.root_cause")}</th>
                <th className="w-24 py-2">{t("analyses.provider")}</th>
                <th className="w-40 py-2">{t("analyses.created")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
              {q.data!.items.map((a) => (
                <tr key={a.id} className="hover:bg-slate-50 dark:hover:bg-slate-800">
                  <td className="py-3 pr-2">
                    <span className={severityBadge(a.severity)}>{a.severity}</span>
                  </td>
                  <td className="py-3 pr-2">
                    <div className="flex items-center gap-2">
                      <Link
                        to={`/dashboard/analyses/${a.id}`}
                        className="truncate font-medium text-brand-700 hover:underline"
                      >
                        {a.root_cause}
                      </Link>
                      <RecurringBadge badge={badges.data?.badges[a.id]} />
                    </div>
                    <div className="truncate text-xs text-slate-500">
                      {(a.project_path || a.project_id) && (
                        <>
                          <span>{a.project_path ?? a.project_id}</span>
                          <span aria-hidden> · </span>
                        </>
                      )}
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
                      <span>
                        {t("analyses.confidence")} {(a.confidence * 100).toFixed(0)}%
                      </span>
                    </div>
                  </td>
                  <td className="py-3 pr-2 text-slate-500">
                    <div className="flex flex-col gap-0.5">
                      <span>{a.provider}</span>
                      <span className="text-[10px] uppercase tracking-wide text-slate-400">
                        {sourceBadgeLabel(a.source, t)}
                      </span>
                    </div>
                  </td>
                  <td className="py-3 pr-2 text-slate-500">
                    {new Date(a.created_at).toLocaleString(i18n.language)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-slate-500">
        <div>
          {t("analyses.total_showing", {
            total: q.data?.total ?? 0,
            from: offset + 1,
            to: Math.min(q.data?.total ?? 0, offset + limit),
          })}
        </div>
        <div className="flex gap-2">
          <button
            className="btn-secondary"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - limit))}
          >
            {t("common.previous")}
          </button>
          <button
            className="btn-secondary"
            disabled={!q.data || offset + limit >= (q.data.total ?? 0)}
            onClick={() => setOffset(offset + limit)}
          >
            {t("common.next")}
          </button>
        </div>
      </div>
    </div>
  );
}

function severityBadge(sev: string) {
  if (sev === "high") return "badge-red";
  if (sev === "medium") return "badge-yellow";
  if (sev === "low") return "badge-green";
  return "badge-slate";
}

function sourceBadgeLabel(
  source: string | null | undefined,
  t: (key: string) => string,
): string {
  if (!source) return t("analyses.via_webhook");
  if (source.endsWith("_ingest")) return t("analyses.via_api_ingest");
  if (source.endsWith("_poll")) return t("analyses.via_polling");
  return t("analyses.via_webhook");
}

function RecurringBadge({ badge }: { badge: ClusterBadge | undefined }) {
  const { t } = useTranslation();
  if (!badge) return null;
  if (badge.status === "acknowledged") return null;
  const tone =
    badge.status === "resolved"
      ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-200"
      : "bg-amber-50 text-amber-700 dark:bg-amber-900/40 dark:text-amber-200";
  return (
    <Link
      to="/dashboard/clusters"
      className={
        "shrink-0 rounded-full border border-current/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide " +
        tone
      }
      title={t("clusters.badge_title", { count: badge.count })}
    >
      ×{badge.count}
    </Link>
  );
}
