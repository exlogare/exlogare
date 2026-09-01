import { useEffect, useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Check, Loader2, Search, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import clsx from "clsx";
import { api } from "../lib/api";
import { toast } from "../lib/toast";
import type {
  BitbucketConnection,
  GitFlicConnection,
  GitHubConnection,
  GitLabConnection,
  GitLabProject,
  WatchProjectsResponse,
} from "../lib/types";
import { useCapabilities } from "../lib/capabilities";
import { resolveWatchMode } from "../lib/watchMode";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** All CIConnections (GitLab/GitHub/Bitbucket) for the active provider. */
  connections:
    | GitLabConnection[]
    | GitHubConnection[]
    | BitbucketConnection[]
    | GitFlicConnection[];
  /** Called after successful watch, so the caller can refetch. */
  onSynced: () => void;
  /** API prefix ``gitlab`` / ``github`` / ``bitbucket`` / ``gitflic``. */
  ciProvider?: "gitlab" | "github" | "bitbucket" | "gitflic";
}

export function SyncProjectsDialog({
  open,
  onOpenChange,
  connections,
  onSynced,
  ciProvider = "gitlab",
}: Props) {
  const { t } = useTranslation();
  const caps = useCapabilities();
  const apiBase = `/api/integrations/${ciProvider}`;
  // Bitbucket Cloud uses ``/repos`` like GitHub; GitLab uses ``/projects``.
  const listEndpoint =
    ciProvider === "gitlab" || ciProvider === "gitflic" ? "projects" : "repos";

  const baseConnections = useMemo(
    () => connections.filter((c) => c.external_project_id === null),
    [connections],
  );
  /** Only actively enabled projects block "add again" via sync — disabled rows can be re-added / re-enabled here. */
  const watchedIds = useMemo(
    () =>
      new Set(
        connections
          .filter(
            (c) =>
              c.external_project_id !== null && c.enabled !== false,
          )
          .map((c) =>
            ciProvider === "bitbucket" || ciProvider === "gitflic"
              ? String(c.external_project_name ?? c.external_project_id)
              : String(c.external_project_id),
          ),
      ),
    [connections, ciProvider],
  );

  const [baseId, setBaseId] = useState<string | null>(null);
  const [projects, setProjects] = useState<GitLabProject[]>([]);
  const [loading, setLoading] = useState(false);
  const [watching, setWatching] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (!open) {
      setProjects([]);
      setSelected(new Set());
      setQuery("");
      setLoading(false);
      setWatching(false);
      return;
    }
    if (baseConnections.length === 0) {
      setBaseId(null);
      return;
    }
    setBaseId((prev) => prev ?? baseConnections[0].id);
  }, [open, baseConnections]);

  useEffect(() => {
    if (!open || !baseId) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const res = await api<GitLabProject[]>(
          `${apiBase}/${listEndpoint}?connection_id=${baseId}`,
        );
        if (!cancelled) {
          setProjects(res);
          setSelected(new Set());
        }
      } catch (err) {
        if (!cancelled) {
          toast.error(
            err instanceof Error ? err.message : t("toast.unknown_error"),
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, baseId, t]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return projects;
    return projects.filter(
      (p) =>
        p.path_with_namespace.toLowerCase().includes(q) ||
        p.name.toLowerCase().includes(q),
    );
  }, [projects, query]);

  const newInFiltered = useMemo(
    () => filtered.filter((p) => !watchedIds.has(String(p.id))),
    [filtered, watchedIds],
  );

  const allSelected =
    newInFiltered.length > 0 &&
    newInFiltered.every((p) => selected.has(String(p.id)));
  const someSelected =
    newInFiltered.some((p) => selected.has(String(p.id))) && !allSelected;

  function toggleOne(id: string, on: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  function toggleAll(on: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const p of newInFiltered) {
        if (on) next.add(String(p.id));
        else next.delete(String(p.id));
      }
      return next;
    });
  }

  async function watchSelected() {
    if (!baseId || selected.size === 0) return;
    const modes =
      ciProvider === "github"
        ? caps.data?.github_modes
        : ciProvider === "bitbucket"
          ? caps.data?.bitbucket_modes
          : ciProvider === "gitflic"
            ? caps.data?.gitflic_modes
            : caps.data?.gitlab_modes;
    const watchMode = resolveWatchMode(modes);
    if (!watchMode) {
      toast.error(t("onboarding.watch_mode_unavailable"));
      return;
    }
    setWatching(true);
    try {
      const body =
        ciProvider === "gitflic"
          ? { project_paths: Array.from(selected), mode: watchMode }
          : { project_ids: Array.from(selected), mode: watchMode };
      const res = await api<WatchProjectsResponse>(
        `${apiBase}/watch?connection_id=${baseId}`,
        { method: "POST", body },
      );
      toast.success(
        t("integrations.sync_synced_n", { count: selected.size }),
      );
      if (res.repo_limit_partial) {
        toast.warning(t("integrations.repo_limit_partial"));
      }
      onSynced();
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    } finally {
      setWatching(false);
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[90] bg-black/50 backdrop-blur-sm" />
        <Dialog.Content
          className={clsx(
            "fixed left-1/2 top-1/2 z-[95] flex max-h-[85vh] w-full max-w-2xl -translate-x-1/2 -translate-y-1/2 flex-col",
            "rounded-xl border border-slate-200 bg-white p-6 shadow-2xl",
            "dark:border-slate-700 dark:bg-slate-900",
          )}
        >
          <div className="mb-4 flex items-center justify-between">
            <Dialog.Title className="text-lg font-semibold">
              {t("integrations.sync_title")}
            </Dialog.Title>
            <Dialog.Close
              className="text-slate-400 hover:text-slate-700 dark:hover:text-slate-200"
              aria-label={t("common.close")}
            >
              <X className="h-5 w-5" />
            </Dialog.Close>
          </div>

          {baseConnections.length === 0 ? (
            <p className="py-6 text-sm text-slate-500">
              {t("integrations.sync_no_base")}
            </p>
          ) : (
            <>
              {baseConnections.length > 1 && (
                <div className="mb-3">
                  <label className="label">
                    {t("integrations.sync_select_base")}
                  </label>
                  <select
                    className="input"
                    value={baseId ?? ""}
                    onChange={(e) => setBaseId(e.target.value)}
                  >
                    {baseConnections.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.base_url}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              <div className="mb-3 flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-700">
                <Search className="h-4 w-4 text-slate-400" />
                <input
                  className="flex-1 bg-transparent text-sm outline-none"
                  placeholder={t("integrations.sync_search_placeholder")}
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </div>

              {loading ? (
                <div className="flex items-center justify-center gap-2 py-10 text-sm text-slate-500">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {t("common.loading")}
                </div>
              ) : filtered.length === 0 ? (
                <p className="py-10 text-center text-sm text-slate-500">
                  {t("integrations.sync_empty")}
                </p>
              ) : (
                <div className="flex-1 overflow-y-auto rounded-lg border border-slate-200 dark:border-slate-700">
                  <label className="flex items-center gap-3 border-b border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-800/50">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      ref={(el) => {
                        if (el) el.indeterminate = someSelected;
                      }}
                      onChange={(e) => toggleAll(e.target.checked)}
                      disabled={newInFiltered.length === 0}
                    />
                    <span className="text-sm font-medium">
                      {t("integrations.sync_select_all", {
                        count: newInFiltered.length,
                      })}
                    </span>
                  </label>
                  <ul className="divide-y divide-slate-200 dark:divide-slate-700">
                    {filtered.map((p) => {
                      const pid = String(p.id);
                      const already = watchedIds.has(pid);
                      const checked = selected.has(pid);
                      return (
                        <li
                          key={pid}
                          className={clsx(
                            "flex items-center gap-3 p-3",
                            already && "opacity-60",
                          )}
                        >
                          <input
                            type="checkbox"
                            checked={already || checked}
                            disabled={already}
                            onChange={(e) => toggleOne(pid, e.target.checked)}
                            title={
                              already
                                ? t("integrations.sync_already_watched")
                                : undefined
                            }
                          />
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-sm font-medium">
                              {p.path_with_namespace || p.name}
                            </div>
                            {p.default_branch && (
                              <div className="truncate text-xs text-slate-500">
                                {p.default_branch}
                              </div>
                            )}
                          </div>
                          {already && (
                            <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-200">
                              <Check className="h-3 w-3" />
                              {t("integrations.sync_already_watched")}
                            </span>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}

              <div className="mt-4 flex items-center justify-end gap-2">
                <button
                  className="btn-secondary"
                  onClick={() => onOpenChange(false)}
                  disabled={watching}
                >
                  {t("common.cancel")}
                </button>
                <button
                  className="btn-primary inline-flex items-center gap-2"
                  disabled={
                    selected.size === 0 ||
                    watching ||
                    caps.isPending ||
                    resolveWatchMode(
                      ciProvider === "github"
                        ? caps.data?.github_modes
                        : ciProvider === "bitbucket"
                          ? caps.data?.bitbucket_modes
                          : caps.data?.gitlab_modes,
                    ) === null
                  }
                  onClick={watchSelected}
                >
                  {watching && <Loader2 className="h-4 w-4 animate-spin" />}
                  {t("integrations.sync_watch_n", { count: selected.size })}
                </button>
              </div>
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export default SyncProjectsDialog;
