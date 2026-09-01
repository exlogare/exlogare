/** Outbound webhooks card — list + create/edit/rotate dialog. */
import { Fragment, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  Copy,
  Plus,
  RefreshCw,
  RotateCcw,
  Send,
  Trash2,
  Webhook,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import clsx from "clsx";
import { api } from "../lib/api";
import { useConfirm } from "./ui/ConfirmDialog";
import { toast } from "../lib/toast";
import type {
  OutboundWebhookCreated,
  OutboundWebhookSubscription,
} from "../lib/types";

const ROOT = "/api/integrations/outbound-webhooks";
const ALL_EVENTS = ["analysis.completed"] as const;

export default function OutboundWebhooks() {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const confirm = useConfirm();

  const subs = useQuery({
    queryKey: ["outbound-webhooks"],
    queryFn: () => api<OutboundWebhookSubscription[]>(ROOT),
    enabled: true,
  });

  const [createOpen, setCreateOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState<string[]>([...ALL_EVENTS]);
  const [createdSecret, setCreatedSecret] =
    useState<OutboundWebhookCreated | null>(null);
  const [error, setError] = useState<string | null>(null);

  function openCreate() {
    setEditingId(null);
    setName("");
    setUrl("");
    setEvents([...ALL_EVENTS]);
    setCreatedSecret(null);
    setError(null);
    setCreateOpen(true);
  }

  function openEdit(row: OutboundWebhookSubscription) {
    setEditingId(row.id);
    setName(row.name);
    setUrl(row.url);
    setEvents(row.events.length ? row.events : [...ALL_EVENTS]);
    setCreatedSecret(null);
    setError(null);
    setCreateOpen(true);
  }

  function close() {
    setCreateOpen(false);
    setCreatedSecret(null);
  }

  const createMut = useMutation({
    mutationFn: () =>
      api<OutboundWebhookCreated>(ROOT, {
        method: "POST",
        body: { name: name.trim(), url: url.trim(), events },
      }),
    onSuccess: async (res) => {
      setCreatedSecret(res);
      await qc.invalidateQueries({ queryKey: ["outbound-webhooks"] });
    },
    onError: (err) => setError(err instanceof Error ? err.message : String(err)),
  });

  const updateMut = useMutation({
    mutationFn: () =>
      api<OutboundWebhookSubscription>(`${ROOT}/${editingId}`, {
        method: "PATCH",
        body: { name: name.trim(), url: url.trim(), events },
      }),
    onSuccess: async () => {
      toast.success(t("outbound.saved"));
      close();
      await qc.invalidateQueries({ queryKey: ["outbound-webhooks"] });
    },
    onError: (err) => setError(err instanceof Error ? err.message : String(err)),
  });

  async function toggleEnabled(row: OutboundWebhookSubscription) {
    try {
      await api(`${ROOT}/${row.id}`, {
        method: "PATCH",
        body: { enabled: !row.enabled },
      });
      await qc.invalidateQueries({ queryKey: ["outbound-webhooks"] });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  async function rotate(row: OutboundWebhookSubscription) {
    const ok = await confirm({
      title: t("outbound.rotate_title"),
      message: t("outbound.rotate_msg"),
      confirmLabel: t("outbound.rotate"),
    });
    if (!ok) return;
    try {
      const res = await api<OutboundWebhookCreated>(
        `${ROOT}/${row.id}/rotate-secret`,
        { method: "POST" },
      );
      setEditingId(row.id);
      setName(row.name);
      setUrl(row.url);
      setEvents(row.events);
      setCreatedSecret(res);
      setCreateOpen(true);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  async function test(row: OutboundWebhookSubscription) {
    try {
      const res = await api<{ ok: boolean; status: number; detail: string | null }>(
        `${ROOT}/${row.id}/test`,
        { method: "POST" },
      );
      if (res.ok) toast.success(t("outbound.test_ok", { status: res.status }));
      else
        toast.error(
          t("outbound.test_failed", {
            status: res.status,
            detail: res.detail ?? "",
          }),
        );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  async function remove(row: OutboundWebhookSubscription) {
    const ok = await confirm({
      title: t("outbound.delete_title"),
      message: t("outbound.delete_msg", { name: row.name }),
      confirmLabel: t("common.delete"),
      tone: "danger",
    });
    if (!ok) return;
    try {
      await api(`${ROOT}/${row.id}`, { method: "DELETE" });
      toast.success(t("outbound.deleted"));
      await qc.invalidateQueries({ queryKey: ["outbound-webhooks"] });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  function toggleEvent(value: string) {
    setEvents((current) =>
      current.includes(value)
        ? current.filter((v) => v !== value)
        : [...current, value],
    );
  }

  async function copySecret() {
    if (!createdSecret) return;
    try {
      await navigator.clipboard.writeText(createdSecret.secret);
      toast.success(t("common.copied"));
    } catch {
      // Clipboard may be unavailable; the secret is still visible onscreen.
    }
  }

  const rows = subs.data ?? [];

  return (
    <section className="card">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Webhook className="h-5 w-5 text-brand-600" />
            <h2 className="text-lg font-semibold">{t("outbound.title")}</h2>
          </div>
          <p className="mt-1 text-sm text-slate-500">{t("outbound.desc")}</p>
        </div>
        <button className="btn-primary" onClick={openCreate}>
          <Plus className="mr-1 h-4 w-4" />
          {t("outbound.new")}
        </button>
      </div>

      {subs.isLoading ? (
        <p className="text-sm text-slate-500">{t("common.loading")}</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-slate-500">{t("outbound.empty")}</p>
      ) : (
        <div className="space-y-3">
          {rows.map((row) => (
            <OutboundWebhookCard
              key={row.id}
              row={row}
              language={i18n.language}
              onTest={() => test(row)}
              onRotate={() => rotate(row)}
              onToggleEnabled={() => toggleEnabled(row)}
              onEdit={() => openEdit(row)}
              onRemove={() => remove(row)}
            />
          ))}
        </div>
      )}

      {createOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-lg bg-white p-6 shadow-xl dark:bg-slate-900">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-lg font-semibold">
                {createdSecret
                  ? t("outbound.created")
                  : editingId
                    ? t("outbound.edit_title")
                    : t("outbound.new")}
              </h3>
              <button
                className="text-slate-500 hover:text-slate-800"
                onClick={close}
                aria-label={t("common.close")}
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            {createdSecret ? (
              <Fragment>
                <div className="mb-3 rounded bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-900/30 dark:text-amber-100">
                  {t("outbound.secret_warn")}
                </div>
                <div className="rounded border border-slate-200 bg-slate-50 p-3 font-mono text-xs break-all dark:border-slate-700 dark:bg-slate-800">
                  {createdSecret.secret}
                </div>
                <div className="mt-3 flex items-center justify-end gap-2">
                  <button className="btn-secondary" onClick={copySecret}>
                    <Copy className="mr-1 h-4 w-4" />
                    {t("common.copy")}
                  </button>
                  <button className="btn-primary" onClick={close}>
                    {t("settings.ive_saved_it")}
                  </button>
                </div>
              </Fragment>
            ) : (
              <div className="space-y-3">
                <div>
                  <label className="label">{t("common.name")}</label>
                  <input
                    className="input"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder={t("outbound.name_ph")}
                  />
                </div>
                <div>
                  <label className="label">{t("outbound.url_label")}</label>
                  <input
                    className="input"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    placeholder="https://example.com/exlogare-webhook"
                  />
                  <p className="mt-1 text-xs text-slate-500">
                    {t("outbound.url_hint")}
                  </p>
                </div>
                <div>
                  <label className="label">{t("outbound.events")}</label>
                  <div className="flex flex-col gap-1 text-sm">
                    {ALL_EVENTS.map((evt) => (
                      <label key={evt} className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={events.includes(evt)}
                          onChange={() => toggleEvent(evt)}
                        />
                        <span className="font-mono">{evt}</span>
                      </label>
                    ))}
                  </div>
                </div>
                {error && <p className="text-sm text-red-600">{error}</p>}
                <div className="flex items-center justify-end gap-2">
                  <button className="btn-secondary" onClick={close}>
                    {t("common.cancel")}
                  </button>
                  <button
                    className="btn-primary"
                    disabled={
                      !name.trim() ||
                      !url.trim() ||
                      events.length === 0 ||
                      createMut.isPending ||
                      updateMut.isPending
                    }
                    onClick={() => {
                      setError(null);
                      if (editingId) updateMut.mutate();
                      else createMut.mutate();
                    }}
                  >
                    {editingId ? t("common.save") : t("outbound.create")}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function OutboundWebhookCard({
  row,
  language,
  onTest,
  onRotate,
  onToggleEnabled,
  onEdit,
  onRemove,
}: {
  row: OutboundWebhookSubscription;
  language: string;
  onTest: () => void;
  onRotate: () => void;
  onToggleEnabled: () => void;
  onEdit: () => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      className={clsx(
        "rounded-xl border border-slate-200 bg-white",
        "dark:border-slate-800 dark:bg-slate-900",
        "transition-all duration-200",
        "hover:-translate-y-0.5 hover:border-brand-400 hover:shadow-md",
        "dark:hover:border-brand-500",
        !row.enabled && "opacity-80",
      )}
    >
      <button
        type="button"
        onClick={() => setExpanded((x) => !x)}
        className="flex w-full items-center gap-3 p-4 text-left"
        aria-expanded={expanded}
      >
        <Webhook className="h-5 w-5 flex-shrink-0 text-brand-600" aria-hidden />
        <div className="flex-1 min-w-0">
          <div className="truncate text-sm font-medium text-slate-900 dark:text-slate-100">
            {row.name}
          </div>
          <div className="truncate font-mono text-xs text-slate-500">
            {row.url}
          </div>
        </div>
        <StateBadge row={row} t={t} />
        <ChevronDown
          className={clsx(
            "h-4 w-4 flex-shrink-0 text-slate-400 transition-transform duration-200",
            expanded && "rotate-180",
          )}
        />
      </button>

      <div
        className={clsx(
          "grid transition-[grid-template-rows] duration-300 ease-out",
          expanded ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
        )}
      >
        <div className="overflow-hidden">
          <div className="space-y-4 border-t border-slate-200 p-4 dark:border-slate-800">
            <OutboundDetails row={row} language={language} />

            {row.last_error && (
              <p
                className="rounded-md bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-900/20 dark:text-amber-200"
                title={row.last_error}
              >
                {row.last_error}
              </p>
            )}

            <div className="flex flex-wrap items-center gap-2">
              <button
                className="btn-secondary inline-flex items-center gap-1"
                onClick={onTest}
                title={t("outbound.test")}
              >
                <Send className="h-4 w-4" />
                {t("common.test")}
              </button>
              <button
                className="btn-secondary inline-flex items-center gap-1"
                onClick={onRotate}
                title={t("outbound.rotate")}
              >
                <RotateCcw className="h-4 w-4" />
                {t("outbound.rotate")}
              </button>
              <button
                className="btn-secondary inline-flex items-center gap-1"
                onClick={onToggleEnabled}
              >
                <RefreshCw className="h-4 w-4" />
                {row.enabled ? t("common.disable") : t("common.enable")}
              </button>
              <button className="btn-secondary" onClick={onEdit}>
                {t("common.edit")}
              </button>
              <button
                className="btn-danger ml-auto inline-flex items-center gap-1"
                onClick={onRemove}
                aria-label={t("common.delete")}
              >
                <Trash2 className="h-4 w-4" />
                <span>{t("common.delete")}</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function OutboundDetails({
  row,
  language,
}: {
  row: OutboundWebhookSubscription;
  language: string;
}) {
  const { t } = useTranslation();
  const rows: { label: string; value: string | null }[] = [
    {
      label: "URL",
      value: row.url,
    },
    {
      label: t("outbound.events"),
      value: row.events.join(", ") || null,
    },
    {
      label: t("outbound.last_delivery"),
      value: row.last_delivery_at
        ? new Date(row.last_delivery_at).toLocaleString(language)
        : null,
    },
    {
      label: t("settings.state"),
      value:
        row.last_status !== null
          ? `HTTP ${row.last_status}`
          : null,
    },
  ];

  const visible = rows.filter((r) => r.value);
  if (visible.length === 0) return null;

  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-xs sm:grid-cols-2">
      {visible.map((r) => (
        <div key={r.label} className="flex items-start gap-2">
          <dt className="w-24 flex-shrink-0 text-slate-500">{r.label}</dt>
          <dd className="min-w-0 truncate font-mono text-slate-800 dark:text-slate-200">
            {r.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function StateBadge({
  row,
  t,
}: {
  row: OutboundWebhookSubscription;
  t: (key: string, opts?: Record<string, unknown>) => string;
}) {
  if (!row.enabled) {
    const reason = row.disabled_at
      ? t("outbound.state_auto_disabled")
      : t("outbound.state_disabled");
    return (
      <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-700 dark:bg-slate-800 dark:text-slate-300">
        {reason}
      </span>
    );
  }
  if (row.consecutive_failures > 0) {
    return (
      <span
        className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-900 dark:bg-amber-900/40 dark:text-amber-200"
        title={row.last_error ?? undefined}
      >
        {t("outbound.state_failing", { n: row.consecutive_failures })}
      </span>
    );
  }
  return (
    <span className="rounded bg-emerald-100 px-2 py-0.5 text-xs text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200">
      {t("outbound.state_active")}
    </span>
  );
}
