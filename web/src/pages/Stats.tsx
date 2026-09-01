import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import clsx from "clsx";
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Info,
} from "lucide-react";
import {
  Cell,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../lib/api";
import type {
  OverviewStats,
  TimeseriesPoint,
  TopProject,
  TopRootCause,
} from "../lib/types";
import { KpiCard } from "../components/KpiCard";

function formatDuration(seconds: number | null | undefined) {
  if (seconds == null) return "-";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds - m * 60);
  return `${m}m ${s}s`;
}

export default function StatsPage() {
  const { t, i18n } = useTranslation();
  const overview = useQuery({
    queryKey: ["stats", "overview"],
    queryFn: () => api<OverviewStats>("/api/stats/overview"),
  });
  const ts = useQuery({
    queryKey: ["stats", "ts"],
    queryFn: () => api<TimeseriesPoint[]>("/api/stats/timeseries?days=30"),
  });
  const tp = useQuery({
    queryKey: ["stats", "top-projects"],
    queryFn: () => api<TopProject[]>("/api/stats/top-projects"),
  });
  const tr = useQuery({
    queryKey: ["stats", "top-root"],
    queryFn: () => api<TopRootCause[]>("/api/stats/top-root-causes"),
  });

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">{t("stats.title")}</h1>
        <p className="text-sm text-slate-500">{t("stats.subtitle")}</p>
      </header>

      <section className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <KpiCard
          icon={<AlertTriangle className="h-4 w-4" />}
          label={t("stats.failures_detected")}
          value={overview.data?.failures_detected ?? "-"}
          accent="rose"
        />
        <KpiCard
          icon={<CheckCircle2 className="h-4 w-4" />}
          label={t("stats.analyses_completed")}
          value={overview.data?.analyses_completed ?? "-"}
          accent="emerald"
        />
        <P90Card
          label={t("stats.p90_time_to_rca")}
          value={formatDuration(overview.data?.p90_time_to_rca_seconds ?? null)}
          tooltip={t("stats.p90_tooltip")}
        />
      </section>

      <section className="card-hover">
        <h2 className="mb-3 text-lg font-semibold">
          {t("stats.failures_per_day")}
        </h2>
        {ts.isLoading ? (
          <p className="text-sm text-slate-500">{t("common.loading")}</p>
        ) : (ts.data ?? []).length === 0 ? (
          <p className="text-sm text-slate-500">{t("stats.no_events")}</p>
        ) : (
          <FailuresPerDayChart points={ts.data!} locale={i18n.language} />
        )}
      </section>

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card-hover">
          <h2 className="mb-3 text-lg font-semibold">
            {t("stats.top_projects")}
          </h2>
          {(tp.data ?? []).length === 0 ? (
            <p className="text-sm text-slate-500">{t("stats.no_data")}</p>
          ) : (
            <TopProjectsPie projects={tp.data!} />
          )}
        </div>

        <div className="card-hover">
          <h2 className="mb-3 text-lg font-semibold">
            {t("stats.top_root_causes")}
          </h2>
          {(tr.data ?? []).length === 0 ? (
            <p className="text-sm text-slate-500">{t("stats.no_data")}</p>
          ) : (
            <RootCauseHeatmap rows={tr.data!} />
          )}
        </div>
      </section>
    </div>
  );
}

function P90Card({
  label,
  value,
  tooltip,
}: {
  label: string;
  value: React.ReactNode;
  tooltip: string;
}) {
  return (
    <KpiCard
      icon={<Clock3 className="h-4 w-4" />}
      accent="brand"
      label={
        <span className="inline-flex items-center gap-1">
          {label}
          <InfoTooltip text={tooltip} />
        </span>
      }
      value={value}
    />
  );
}

function InfoTooltip({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      <button
        type="button"
        aria-label={text}
        className="inline-flex h-4 w-4 items-center justify-center rounded-full text-slate-400 hover:text-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-400"
      >
        <Info className="h-3.5 w-3.5" />
      </button>
      {open && (
        <span
          role="tooltip"
          className="pointer-events-none absolute left-1/2 top-full z-20 mt-2 w-64 -translate-x-1/2 rounded-lg border border-slate-200 bg-white p-2.5 text-[11px] leading-snug text-slate-600 shadow-lg dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
        >
          {text}
        </span>
      )}
    </span>
  );
}

function FailuresPerDayChart({
  points,
  locale,
}: {
  points: TimeseriesPoint[];
  locale: string;
}) {
  const { t } = useTranslation();
  const data = points.map((p) => {
    const d = new Date(`${p.date}T12:00:00`);
    return {
      date: p.date,
      label: d.toLocaleDateString(locale, { month: "short", day: "numeric" }),
      fullLabel: d.toLocaleDateString(locale, { dateStyle: "medium" }),
      failures: p.failures,
    };
  });

  return (
    <div className="h-[280px] w-full min-w-0">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart
          data={data}
          margin={{ top: 8, right: 12, left: 0, bottom: 4 }}
        >
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
            width={40}
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
              const row = payload?.[0]?.payload as
                | { fullLabel?: string }
                | undefined;
              return row?.fullLabel ?? String(label);
            }}
            formatter={(value: number | string) => [value, t("stats.failures")]}
          />
          <Line
            type="monotone"
            dataKey="failures"
            stroke="#e11d48"
            strokeWidth={2}
            dot={{ r: 2, fill: "#e11d48" }}
            activeDot={{ r: 4 }}
            name={t("stats.failures")}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

const PIE_COLORS = [
  "#6366f1", // brand/indigo
  "#10b981", // emerald
  "#f59e0b", // amber
  "#ec4899", // pink
  "#06b6d4", // cyan
  "#a855f7", // violet
  "#64748b", // slate — reserved for "Other"
];

type PieDatum = {
  name: string;
  value: number;
  isOther: boolean;
};

function TopProjectsPie({ projects }: { projects: TopProject[] }) {
  const { t, i18n } = useTranslation();

  const { slices, total } = useMemo(() => {
    const sorted = [...projects].sort((a, b) => b.analyses - a.analyses);
    const topN = 6;
    const head = sorted.slice(0, topN).map((p) => ({
      name: p.project_path ?? p.project_id ?? "(unknown)",
      value: p.analyses,
      isOther: false,
    }));
    const tail = sorted.slice(topN);
    const tailSum = tail.reduce((acc, p) => acc + p.analyses, 0);
    const combined: PieDatum[] =
      tailSum > 0
        ? [...head, { name: t("stats.other_projects"), value: tailSum, isOther: true }]
        : head;
    const total = combined.reduce((acc, s) => acc + s.value, 0);
    return { slices: combined, total };
  }, [projects, t]);

  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
      <div className="h-[260px] w-full min-w-0 sm:w-[55%]">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={slices}
              dataKey="value"
              nameKey="name"
              cx="50%"
              cy="50%"
              innerRadius={55}
              outerRadius={95}
              paddingAngle={2}
              stroke="none"
            >
              {slices.map((s, i) => (
                <Cell
                  key={s.name}
                  fill={
                    s.isOther
                      ? PIE_COLORS[PIE_COLORS.length - 1]
                      : PIE_COLORS[i % (PIE_COLORS.length - 1)]
                  }
                />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{
                borderRadius: "8px",
                border: "1px solid rgb(226 232 240)",
                fontSize: "12px",
              }}
              formatter={(value: number, _name, entry) => {
                const pct = total
                  ? ` · ${Math.round((value / total) * 100)}%`
                  : "";
                return [
                  `${value.toLocaleString(i18n.language)}${pct}`,
                  (entry as unknown as { payload: PieDatum }).payload.name,
                ];
              }}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <div className="flex-1 min-w-0 space-y-2 text-sm">
        <div className="mb-2 text-xs uppercase tracking-wide text-slate-500">
          {t("stats.projects_legend_total", {
            total: total.toLocaleString(i18n.language),
          })}
        </div>
        <ul className="space-y-1.5">
          {slices.map((s, i) => (
            <li
              key={s.name}
              className="flex items-center gap-2 text-xs"
              title={s.name}
            >
              <span
                className="inline-block h-2.5 w-2.5 flex-shrink-0 rounded-sm"
                style={{
                  background: s.isOther
                    ? PIE_COLORS[PIE_COLORS.length - 1]
                    : PIE_COLORS[i % (PIE_COLORS.length - 1)],
                }}
              />
              <span className="min-w-0 flex-1 truncate">{s.name}</span>
              <span className="tabular-nums text-slate-500">
                {s.value.toLocaleString(i18n.language)}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

const SEVERITIES = ["low", "medium", "high"] as const;
type Severity = (typeof SEVERITIES)[number];

const SEVERITY_BASE: Record<Severity, { rgb: string; ring: string; label: string }> = {
  low: { rgb: "16, 185, 129", ring: "ring-emerald-500", label: "heatmap_low" },
  medium: { rgb: "245, 158, 11", ring: "ring-amber-500", label: "heatmap_medium" },
  high: { rgb: "225, 29, 72", ring: "ring-rose-500", label: "heatmap_high" },
};

type HeatmapRow = {
  cause: string;
  totals: Record<Severity, number>;
  rowTotal: number;
};

function RootCauseHeatmap({ rows }: { rows: TopRootCause[] }) {
  const { t, i18n } = useTranslation();

  const { matrix, max, hottest } = useMemo(() => {
    const byCause = new Map<string, HeatmapRow>();
    for (const r of rows) {
      const existing = byCause.get(r.root_cause) ?? {
        cause: r.root_cause,
        totals: { low: 0, medium: 0, high: 0 },
        rowTotal: 0,
      };
      const sev = (SEVERITIES as readonly string[]).includes(r.severity)
        ? (r.severity as Severity)
        : "low";
      existing.totals[sev] += r.count;
      existing.rowTotal += r.count;
      byCause.set(r.root_cause, existing);
    }
    const matrix = [...byCause.values()].sort(
      (a, b) => b.rowTotal - a.rowTotal,
    );
    let max = 0;
    let hottest: { cause: string; sev: Severity } | null = null;
    for (const row of matrix) {
      for (const sev of SEVERITIES) {
        const c = row.totals[sev];
        if (c > max) {
          max = c;
          hottest = { cause: row.cause, sev };
        }
      }
    }
    return { matrix, max, hottest };
  }, [rows]);

  return (
    <div className="space-y-3">
      <div
        role="table"
        aria-label={t("stats.top_root_causes")}
        className="grid gap-1"
        style={{
          gridTemplateColumns:
            "minmax(0,1fr) repeat(3, minmax(36px, 56px)) minmax(40px, 56px)",
        }}
      >
        {/* header row */}
        <div />
        {SEVERITIES.map((sev) => (
          <div
            key={sev}
            className="pb-1 text-center text-[10px] font-semibold uppercase tracking-wide text-slate-500"
          >
            {t(`stats.${SEVERITY_BASE[sev].label}`)}
          </div>
        ))}
        <div className="pb-1 text-right text-[10px] font-semibold uppercase tracking-wide text-slate-500">
          Σ
        </div>

        {/* body rows */}
        {matrix.map((row) => (
          <HeatRow
            key={row.cause}
            row={row}
            max={max}
            hottestCause={hottest?.cause ?? null}
            hottestSeverity={hottest?.sev ?? null}
            locale={i18n.language}
          />
        ))}
      </div>

      <p className="text-[11px] leading-snug text-slate-500">
        {t("stats.heatmap_legend")}
      </p>
    </div>
  );
}

function HeatRow({
  row,
  max,
  hottestCause,
  hottestSeverity,
  locale,
}: {
  row: HeatmapRow;
  max: number;
  hottestCause: string | null;
  hottestSeverity: Severity | null;
  locale: string;
}) {
  const { t } = useTranslation();
  return (
    <>
      <div
        className="flex items-center truncate pr-2 text-xs text-slate-700 dark:text-slate-200"
        title={row.cause}
      >
        {row.cause}
      </div>
      {SEVERITIES.map((sev) => {
        const count = row.totals[sev];
        const isHottest =
          count > 0 &&
          hottestCause === row.cause &&
          hottestSeverity === sev &&
          count === max;
        const intensity =
          max === 0 || count === 0 ? 0 : 0.15 + 0.85 * (count / max);
        return (
          <div
            key={sev}
            className={clsx(
              "relative flex h-9 items-center justify-center rounded-md text-[11px] font-medium tabular-nums",
              count === 0
                ? "bg-slate-100 text-slate-400 dark:bg-slate-800/60"
                : "text-white",
              isHottest && `ring-2 ring-offset-1 ring-offset-white dark:ring-offset-slate-900 ${SEVERITY_BASE[sev].ring}`,
            )}
            style={
              count > 0
                ? {
                    backgroundColor: `rgba(${SEVERITY_BASE[sev].rgb}, ${intensity})`,
                  }
                : undefined
            }
            title={
              count > 0
                ? t("stats.heatmap_count_tooltip", { count })
                : undefined
            }
            aria-label={`${row.cause} — ${t(`stats.${SEVERITY_BASE[sev].label}`)}: ${count}`}
          >
            {count > 0 ? count : ""}
          </div>
        );
      })}
      <div className="flex items-center justify-end pr-1 text-xs font-semibold tabular-nums text-slate-600 dark:text-slate-300">
        {row.rowTotal.toLocaleString(locale)}
      </div>
    </>
  );
}
