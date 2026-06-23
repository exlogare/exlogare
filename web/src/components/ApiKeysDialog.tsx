/** API keys management dialog — list, create, revoke + post-create */
import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  Copy,
  KeyRound,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { toast } from "../lib/toast";
import { useConfirm } from "./ui/ConfirmDialog";
import { useCapabilities } from "../lib/capabilities";
import IngestQuickstart from "./IngestQuickstart";

type ApiTokenRow = {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
};

type ApiTokenCreated = ApiTokenRow & { token: string };

const ALL_SCOPES = ["ingest", "read"] as const;

type View = "list" | "create" | "created";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

export default function ApiKeysDialog({ open, onOpenChange }: Props) {
  const { t, i18n } = useTranslation();
  const confirm = useConfirm();
  const caps = useCapabilities();
  const apiKeysAllowed = caps.data?.api_keys_allowed ?? true;
  const notificationsEnabled = caps.data?.notifications_enabled ?? false;
  const maxApiKeys = caps.data?.max_api_keys ?? null;
  const currentApiKeys = caps.data?.current_api_keys ?? 0;
  const tokenLimitReached =
    maxApiKeys !== null && currentApiKeys >= maxApiKeys;

  const tokens = useQuery({
    queryKey: ["api-tokens"],
    queryFn: () => api<ApiTokenRow[]>("/api/tokens"),
    enabled: open && apiKeysAllowed,
  });

  const [view, setView] = useState<View>("list");
  const [tokenName, setTokenName] = useState("");
  const [tokenScopes, setTokenScopes] = useState<string[]>(["ingest"]);
  const [tokenExpiresAt, setTokenExpiresAt] = useState("");
  const [tokenError, setTokenError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newToken, setNewToken] = useState<ApiTokenCreated | null>(null);
  const [copied, setCopied] = useState(false);

  function resetForm() {
    setTokenName("");
    setTokenScopes(["ingest"]);
    setTokenExpiresAt("");
    setTokenError(null);
  }

  function handleOpenChange(next: boolean) {
    if (!next) {
      // Dialog closing — reset to default view so the next open is clean.
      setView("list");
      setNewToken(null);
      setCopied(false);
      resetForm();
    }
    onOpenChange(next);
  }

  function startCreate() {
    resetForm();
    setNewToken(null);
    setCopied(false);
    setView("create");
  }

  function backToList() {
    setView("list");
    setNewToken(null);
    setCopied(false);
    resetForm();
  }

  function toggleScope(scope: string) {
    setTokenScopes((current) =>
      current.includes(scope)
        ? current.filter((s) => s !== scope)
        : [...current, scope],
    );
  }

  async function createToken() {
    setCreating(true);
    setTokenError(null);
    try {
      const body: { name: string; scopes: string[]; expires_at?: string } = {
        name: tokenName.trim(),
        scopes: tokenScopes,
      };
      if (tokenExpiresAt) {
        body.expires_at = new Date(tokenExpiresAt).toISOString();
      }
      const res = await api<ApiTokenCreated>("/api/tokens", {
        method: "POST",
        body,
      });
      setNewToken(res);
      setView("created");
      await tokens.refetch();
      await caps.refetch();
    } catch (err) {
      setTokenError(err instanceof Error ? err.message : t("toast.unknown_error"));
    } finally {
      setCreating(false);
    }
  }

  async function revokeToken(id: string, name: string) {
    const ok = await confirm({
      title: t("settings.revoke_token_title", { name }),
      message: t("settings.revoke_token_msg"),
      confirmLabel: t("common.revoke"),
      tone: "danger",
    });
    if (!ok) return;
    try {
      await api(`/api/tokens/${id}/revoke`, { method: "POST" });
      toast.success(t("settings.token_revoked"));
      await tokens.refetch();
      await caps.refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("toast.unknown_error"));
    }
  }

  async function copyRawToken() {
    if (!newToken) return;
    try {
      await navigator.clipboard.writeText(newToken.token);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard may be unavailable; the secret is still visible onscreen.
    }
  }

  function renderTokenState(row: ApiTokenRow): string {
    if (row.revoked_at) return t("settings.token_state_revoked");
    if (row.expires_at && new Date(row.expires_at) <= new Date())
      return t("settings.token_state_expired");
    return t("settings.token_state_active");
  }

  const titleText = (() => {
    if (view === "created") return t("settings.token_created");
    if (view === "create") return t("settings.new_token");
    return t("settings.api_tokens");
  })();

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-slate-900/40 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content
          className={`fixed left-1/2 top-1/2 z-50 w-[min(96vw,${
            view === "created" ? "44rem" : "40rem"
          })] -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-xl focus:outline-none dark:border-slate-800 dark:bg-slate-900`}
        >
          <div className="flex items-start gap-3 border-b border-slate-200 p-5 dark:border-slate-800">
            {view !== "list" && (
              <button
                type="button"
                className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800"
                onClick={backToList}
                aria-label={t("common.back")}
              >
                <ArrowLeft className="h-4 w-4" />
              </button>
            )}
            <KeyRound className="mt-0.5 h-5 w-5 flex-shrink-0 text-brand-600" aria-hidden />
            <div className="flex-1">
              <Dialog.Title className="text-base font-semibold">
                {titleText}
              </Dialog.Title>
              <Dialog.Description className="mt-1 text-xs text-slate-500">
                {t("settings.api_tokens_desc")}
              </Dialog.Description>
            </div>
            <Dialog.Close
              className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800"
              aria-label={t("common.close")}
            >
              <X className="h-4 w-4" />
            </Dialog.Close>
          </div>

          <div className="max-h-[75vh] overflow-y-auto p-5">
            {!apiKeysAllowed ? (
              <div />
            ) : view === "list" ? (
              <ListView
                tokens={tokens.data ?? []}
                isLoading={tokens.isLoading}
                maxApiKeys={maxApiKeys}
                currentApiKeys={currentApiKeys}
                tokenLimitReached={tokenLimitReached}
                notificationsEnabled={notificationsEnabled}
                language={i18n.language}
                onCreate={startCreate}
                onRevoke={revokeToken}
                renderTokenState={renderTokenState}
              />
            ) : view === "create" ? (
              <div className="space-y-3">
                <div>
                  <label className="label">{t("common.name")}</label>
                  <input
                    className="input"
                    value={tokenName}
                    onChange={(e) => setTokenName(e.target.value)}
                    placeholder={t("settings.token_name_ph")}
                    autoFocus
                  />
                </div>
                <div>
                  <label className="label">{t("settings.scopes")}</label>
                  <div className="flex flex-col gap-1 text-sm">
                    {ALL_SCOPES.map((scope) => (
                      <label key={scope} className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={tokenScopes.includes(scope)}
                          onChange={() => toggleScope(scope)}
                        />
                        <span className="font-mono">{scope}</span>
                      </label>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="label">{t("settings.expires_optional")}</label>
                  <input
                    className="input"
                    type="date"
                    value={tokenExpiresAt}
                    onChange={(e) => setTokenExpiresAt(e.target.value)}
                  />
                </div>
                {tokenError && (
                  <p className="text-sm text-red-600">{tokenError}</p>
                )}
                <div className="flex items-center justify-end gap-2">
                  <button className="btn-secondary" onClick={backToList}>
                    {t("common.cancel")}
                  </button>
                  <button
                    className="btn-primary"
                    onClick={createToken}
                    disabled={
                      !tokenName.trim() ||
                      tokenScopes.length === 0 ||
                      creating
                    }
                  >
                    {creating ? t("common.loading") : t("settings.create")}
                  </button>
                </div>
              </div>
            ) : view === "created" && newToken ? (
              <div className="space-y-3">
                <div className="rounded bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-900/30 dark:text-amber-100">
                  {t("settings.token_created_warn")}
                </div>
                <div className="rounded border border-slate-200 bg-slate-50 p-3 font-mono text-xs break-all dark:border-slate-700 dark:bg-slate-800">
                  {newToken.token}
                </div>
                <div className="flex items-center justify-end gap-2">
                  <button className="btn-secondary" onClick={copyRawToken}>
                    <Copy className="mr-1 h-4 w-4" />
                    {copied ? t("common.copied") : t("common.copy")}
                  </button>
                </div>
                <IngestQuickstart
                  token={newToken.token}
                  scopes={newToken.scopes as Array<"ingest" | "read">}
                />
                <div className="flex items-center justify-end gap-2">
                  <button className="btn-primary" onClick={backToList}>
                    {t("settings.ive_saved_it")}
                  </button>
                </div>
              </div>
            ) : null}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function ListView({
  tokens,
  isLoading,
  maxApiKeys,
  currentApiKeys,
  tokenLimitReached,
  notificationsEnabled,
  language,
  onCreate,
  onRevoke,
  renderTokenState,
}: {
  tokens: ApiTokenRow[];
  isLoading: boolean;
  maxApiKeys: number | null;
  currentApiKeys: number;
  tokenLimitReached: boolean;
  notificationsEnabled: boolean;
  language: string;
  onCreate: () => void;
  onRevoke: (id: string, name: string) => void;
  renderTokenState: (row: ApiTokenRow) => string;
}) {
  const { t } = useTranslation();

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          {maxApiKeys !== null ? (
            <p className="text-xs text-slate-500">
              {t("settings.api_tokens_count", {
                current: currentApiKeys,
                max: maxApiKeys,
              })}
            </p>
          ) : (
            <p className="text-xs text-slate-500">
              {t("settings.api_tokens_count_unlimited", {
                current: currentApiKeys,
                defaultValue: `${currentApiKeys} active`,
              })}
            </p>
          )}
        </div>
        <button
          className="btn-primary"
          onClick={onCreate}
          disabled={tokenLimitReached}
          title={
            tokenLimitReached
              ? t("settings.api_tokens_limit_reached")
              : undefined
          }
        >
          <Plus className="mr-1 h-4 w-4" />
          {t("settings.new_token")}
        </button>
      </div>

      {!notificationsEnabled && (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-900/30 dark:text-amber-100">
          <span className="font-semibold">
            {t("settings.notif_upsell_title")}
          </span>
          <span> </span>
          <span>{t("settings.notif_upsell_desc")}</span>
          <span> </span>
        </div>
      )}

      {isLoading ? (
        <p className="text-sm text-slate-500">{t("common.loading")}</p>
      ) : tokens.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-slate-500">
              <tr>
                <th className="py-2">{t("common.name")}</th>
                <th className="py-2">{t("settings.prefix")}</th>
                <th className="py-2">{t("settings.scopes")}</th>
                <th className="py-2">{t("settings.state")}</th>
                <th className="py-2">{t("settings.last_used")}</th>
                <th className="py-2">{t("settings.expires")}</th>
                <th className="py-2" />
              </tr>
            </thead>
            <tbody>
              {tokens.map((row) => (
                <tr
                  key={row.id}
                  className="border-t border-slate-100 dark:border-slate-800"
                >
                  <td className="py-2">{row.name}</td>
                  <td className="py-2 font-mono text-xs">{row.prefix}…</td>
                  <td className="py-2 font-mono text-xs">
                    {row.scopes.join(", ")}
                  </td>
                  <td className="py-2">{renderTokenState(row)}</td>
                  <td className="py-2 text-xs text-slate-500">
                    {row.last_used_at
                      ? new Date(row.last_used_at).toLocaleString(language)
                      : t("common.dash")}
                  </td>
                  <td className="py-2 text-xs text-slate-500">
                    {row.expires_at
                      ? new Date(row.expires_at).toLocaleDateString(language)
                      : t("common.never")}
                  </td>
                  <td className="py-2 text-right">
                    {!row.revoked_at && (
                      <button
                        className="btn-secondary inline-flex items-center gap-1"
                        onClick={() => onRevoke(row.id, row.name)}
                        aria-label={t("common.revoke")}
                      >
                        <Trash2 className="h-4 w-4" />
                        <span>{t("common.revoke")}</span>
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-slate-500">{t("settings.no_tokens")}</p>
      )}
    </div>
  );
}
