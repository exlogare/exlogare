import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowLeft, Download, ChevronLeft, ChevronRight } from "lucide-react";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { toast } from "../lib/toast";

type AuditEntry = {
  id: string;
  action: string;
  actor: string | null;
  target: string | null;
  meta: Record<string, unknown>;
  created_at: string;
};

type AuditPage = {
  items: AuditEntry[];
  next_cursor: string | null;
  limit: number;
};

const PAGE_SIZE = 50;

/** Audit log viewer (admin-only). Surfaces the same rows that the API */
export default function AuditLogPage() {
  const { t } = useTranslation();
  const { me } = useAuth();
  const isAdmin = me?.role === "owner" || me?.role === "admin";

  const [actionFilter, setActionFilter] = useState<string>("");
  const [actorFilter, setActorFilter] = useState<string>("");
  const [sinceFilter, setSinceFilter] = useState<string>(""); // YYYY-MM-DD
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);

  const currentCursor = cursorStack[cursorStack.length - 1] ?? null;

  const queryString = useMemo(() => {
    const sp = new URLSearchParams();
    sp.set("limit", String(PAGE_SIZE));
    if (actionFilter) sp.set("action", actionFilter);
    if (actorFilter) sp.set("actor", actorFilter);
    if (sinceFilter) sp.set("since", `${sinceFilter}T00:00:00Z`);
    if (currentCursor) sp.set("cursor", currentCursor);
    return sp.toString();
  }, [actionFilter, actorFilter, sinceFilter, currentCursor]);

  const page = useQuery({
    queryKey: ["audit", queryString],
    queryFn: () => api<AuditPage>(`/api/audit?${queryString}`),
    enabled: isAdmin,
  });

  const actions = useQuery({
    queryKey: ["audit", "actions"],
    queryFn: () => api<string[]>("/api/audit/actions"),
    enabled: isAdmin,
  });

  function resetPaging() {
    setCursorStack([null]);
  }

  function nextPage() {
    if (page.data?.next_cursor) {
      setCursorStack((s) => [...s, page.data!.next_cursor]);
    }
  }

  function prevPage() {
    setCursorStack((s) => (s.length > 1 ? s.slice(0, -1) : s));
  }

  async function downloadCsv() {
    const sp = new URLSearchParams();
    if (actionFilter) sp.set("action", actionFilter);
    if (actorFilter) sp.set("actor", actorFilter);
    if (sinceFilter) sp.set("since", `${sinceFilter}T00:00:00Z`);
    try {
      const res = await fetch(`/api/audit/export.csv?${sp.toString()}`, {
        method: "GET",
        credentials: "same-origin",
        headers: { Accept: "text/csv" },
      });
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download =
        res.headers.get("content-disposition")?.match(/filename="([^"]+)"/)?.[1] ??
        `exlogare-audit-${new Date().toISOString().replace(/[:.]/g, "-")}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : t("toast.unknown_error"),
      );
    }
  }

  if (!isAdmin) {
    return (
      <div className="space-y-4">
        <header>
          <h1 className="text-2xl font-semibold">{t("audit.title")}</h1>
        </header>
        <div className="card">
          <p className="text-sm text-slate-500">{t("audit.admin_only")}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <div className="space-y-1">
          <Link
            to="/dashboard/settings"
            className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-brand-600 dark:hover:text-brand-400"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> {t("audit.back_to_settings")}
          </Link>
          <h1 className="text-2xl font-semibold">{t("audit.title")}</h1>
          <p className="text-sm text-slate-500">{t("audit.subtitle")}</p>
        </div>
        <button
          type="button"
          className="btn-secondary inline-flex w-fit items-center gap-2"
          onClick={downloadCsv}
        >
          <Download className="h-4 w-4" /> {t("audit.export_csv")}
        </button>
      </header>

      <section className="card space-y-3">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <div>
            <label className="label">{t("audit.filter_action")}</label>
            <select
              className="input"
              value={actionFilter}
              onChange={(e) => {
                setActionFilter(e.target.value);
                resetPaging();
              }}
            >
              <option value="">{t("audit.filter_action_all")}</option>
              {actions.data?.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">{t("audit.filter_actor")}</label>
            <input
              className="input"
              type="text"
              value={actorFilter}
              placeholder="user@example.com"
              onChange={(e) => setActorFilter(e.target.value)}
              onBlur={resetPaging}
            />
          </div>
          <div>
            <label className="label">{t("audit.filter_since")}</label>
            <input
              className="input"
              type="date"
              value={sinceFilter}
              onChange={(e) => {
                setSinceFilter(e.target.value);
                resetPaging();
              }}
            />
          </div>
        </div>
      </section>

      <section className="card">
        {page.isLoading ? (
          <p className="text-sm text-slate-500">{t("common.loading")}</p>
        ) : page.isError ? (
          <p className="text-sm text-rose-500">
            {page.error instanceof Error
              ? page.error.message
              : t("toast.unknown_error")}
          </p>
        ) : page.data && page.data.items.length === 0 ? (
          <p className="text-sm text-slate-500">{t("audit.empty")}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="py-2 pr-4">{t("audit.col_when")}</th>
                  <th className="py-2 pr-4">{t("audit.col_action")}</th>
                  <th className="py-2 pr-4">{t("audit.col_actor")}</th>
                  <th className="py-2 pr-4">{t("audit.col_target")}</th>
                  <th className="py-2">{t("audit.col_meta")}</th>
                </tr>
              </thead>
              <tbody>
                {page.data?.items.map((row) => (
                  <tr
                    key={row.id}
                    className="border-t border-slate-100 align-top dark:border-slate-800"
                  >
                    <td className="whitespace-nowrap py-2 pr-4 text-xs text-slate-500">
                      {new Date(row.created_at).toLocaleString()}
                    </td>
                    <td className="py-2 pr-4 font-mono text-xs">
                      {row.action}
                    </td>
                    <td className="py-2 pr-4 text-xs">{row.actor ?? "—"}</td>
                    <td className="py-2 pr-4 break-all text-xs text-slate-500">
                      {row.target ?? "—"}
                    </td>
                    <td className="py-2 text-xs">
                      {Object.keys(row.meta || {}).length > 0 ? (
                        <details className="group">
                          <summary className="cursor-pointer text-slate-500 group-open:text-slate-700 dark:group-open:text-slate-300">
                            {t("audit.show_meta")}
                          </summary>
                          <pre className="mt-1 overflow-x-auto rounded bg-slate-100 p-2 text-[11px] dark:bg-slate-800">
                            {JSON.stringify(row.meta, null, 2)}
                          </pre>
                        </details>
                      ) : (
                        <span className="text-slate-400">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="mt-4 flex items-center justify-between">
          <button
            type="button"
            className="btn-secondary inline-flex items-center gap-1 disabled:opacity-50"
            onClick={prevPage}
            disabled={cursorStack.length <= 1}
          >
            <ChevronLeft className="h-4 w-4" /> {t("common.previous")}
          </button>
          <p className="text-xs text-slate-500">
            {t("audit.page_size", { count: PAGE_SIZE })}
          </p>
          <button
            type="button"
            className="btn-secondary inline-flex items-center gap-1 disabled:opacity-50"
            onClick={nextPage}
            disabled={!page.data?.next_cursor}
          >
            {t("common.next")} <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </section>
    </div>
  );
}
