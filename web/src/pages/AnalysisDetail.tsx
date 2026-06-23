import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Check, Copy, ExternalLink } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api } from "../lib/api";
import type { Analysis } from "../lib/types";
import RcaMarkdown from "../components/ui/RcaMarkdown";

export default function AnalysisDetailPage() {
  const { t, i18n } = useTranslation();
  const { id } = useParams();
  const q = useQuery({
    queryKey: ["analysis", id],
    queryFn: () => api<Analysis>(`/api/analyses/${id}`),
    enabled: Boolean(id),
  });

  if (q.isLoading) return <p className="text-sm text-slate-500">{t("common.loading")}</p>;
  if (q.isError || !q.data)
    return (
      <div className="card">
        <Link to="/dashboard/analyses" className="text-sm text-brand-600 hover:underline">
          <ArrowLeft className="mr-1 inline h-3 w-3" /> {t("common.back")}
        </Link>
        <p className="mt-2 text-sm text-rose-600">{t("analyses.not_found")}</p>
      </div>
    );

  const a = q.data;
  return (
    <div className="space-y-4">
      <Link to="/dashboard/analyses" className="text-sm text-brand-600 hover:underline">
        <ArrowLeft className="mr-1 inline h-3 w-3" /> {t("analyses.all_analyses")}
      </Link>
      <div className="card space-y-4">
        <header className="flex items-start justify-between gap-4">
          <div>
            <div className="mb-2 flex items-center gap-2">
              <span className={severityBadge(a.severity)}>{a.severity.toUpperCase()}</span>
              <span className="text-xs text-slate-500">
                {t("analyses.confidence_pct", { pct: (a.confidence * 100).toFixed(0) })}
              </span>
              <span className="text-xs text-slate-500">
                {new Date(a.created_at).toLocaleString(i18n.language)}
              </span>
            </div>
            <h1 className="text-xl font-semibold">{a.root_cause}</h1>
            <div className="mt-1 flex flex-wrap items-center gap-x-1 gap-y-1 text-xs text-slate-500">
              <span>{a.provider}</span>
              <span aria-hidden>·</span>
              <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                {sourceBadgeLabel(a.source, t)}
              </span>
              {(a.project_path || a.project_id) && (
                <>
                  <span aria-hidden>·</span>
                  {a.project_web_url ? (
                    <a
                      href={a.project_web_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-brand-600 hover:underline"
                    >
                      {a.project_path ?? a.project_id}
                    </a>
                  ) : (
                    <span>{a.project_path ?? a.project_id}</span>
                  )}
                </>
              )}
              <span aria-hidden>·</span>
              {a.pipeline_url ? (
                <a
                  href={a.pipeline_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-brand-600 hover:underline"
                >
                  {t("overview.run")} #{a.ci_run_id}
                </a>
              ) : (
                <span>
                  {t("overview.run")} #{a.ci_run_id}
                </span>
              )}
              {a.ci_job_id && (
                <>
                  <span aria-hidden>·</span>
                  {a.job_url ? (
                    <a
                      href={a.job_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-brand-600 hover:underline"
                    >
                      {t("analyses.job")} #{a.ci_job_id}
                    </a>
                  ) : (
                    <span>
                      {t("analyses.job")} #{a.ci_job_id}
                    </span>
                  )}
                </>
              )}
            </div>
            <div className="mt-2">
              <AnalysisIdPill id={a.id} />
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
            {a.job_url && (
              <a
                href={a.job_url}
                target="_blank"
                rel="noreferrer"
                className="btn-secondary"
              >
                {t("analyses.open_job")} <ExternalLink className="h-4 w-4" />
              </a>
            )}
            {a.pipeline_url && (
              <a
                href={a.pipeline_url}
                target="_blank"
                rel="noreferrer"
                className="btn-secondary"
              >
                {t("analyses.open_pipeline")} <ExternalLink className="h-4 w-4" />
              </a>
            )}
          </div>
        </header>

        <section>
          <h2 className="mb-1 text-sm font-semibold text-slate-500 uppercase tracking-wide">
            {t("analyses.explanation")}
          </h2>
          <RcaMarkdown>{a.explanation}</RcaMarkdown>
        </section>

        <section>
          <h2 className="mb-1 text-sm font-semibold text-slate-500 uppercase tracking-wide">
            {t("analyses.fix_suggestion")}
          </h2>
          <RcaMarkdown>{a.fix_suggestion}</RcaMarkdown>
        </section>
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

function AnalysisIdPill({ id }: { id: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(id);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
    }
  }

  return (
    <button
      type="button"
      onClick={copy}
      title={copied ? t("common.copied") : t("common.copy")}
      className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-slate-200 bg-slate-50 px-2 py-1 font-mono text-[11px] text-slate-600 transition hover:border-brand-300 hover:text-brand-700 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-400 dark:hover:border-brand-600 dark:hover:text-brand-300"
    >
      <span className="font-sans text-[10px] uppercase tracking-wide text-slate-400 dark:text-slate-500">
        {t("analyses.id_label")}
      </span>
      <span className="truncate">{id}</span>
      {copied ? (
        <Check className="h-3 w-3 shrink-0 text-emerald-500" />
      ) : (
        <Copy className="h-3 w-3 shrink-0" />
      )}
    </button>
  );
}
