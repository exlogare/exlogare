import { useMemo, useState } from "react";
import { Check, Copy } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  buildIngestSnippet,
  buildReadSnippet,
  type IngestProvider,
  type ReadRecipe,
} from "../lib/ingestSnippet";
import { docsUrl } from "../lib/requisites";

interface Props {
  /** The just-issued raw token. Shown only once at creation time. */
  token: string;
  /** Public API base URL. Defaults to ``/api`` so the snippet "just works" */
  apiBase?: string;
  /** When set, hide the dropdown and render the snippet for this CI only. */
  lockedProvider?: IngestProvider;
  /** Scopes the token was created with — controls which tabs are useful. */
  scopes?: ReadonlyArray<"ingest" | "read">;
}

type Tab = "send" | "read";

export default function IngestQuickstart({
  token,
  apiBase = "/api",
  lockedProvider,
  scopes,
}: Props) {
  const { t, i18n } = useTranslation();

  const showSend = !scopes || scopes.includes("ingest");
  const showRead = !scopes || scopes.includes("read");
  const initialTab: Tab = showSend ? "send" : "read";

  const [tab, setTab] = useState<Tab>(initialTab);
  const [provider, setProvider] = useState<IngestProvider>(
    lockedProvider ?? "jenkins",
  );
  const [recipe, setRecipe] = useState<ReadRecipe>("list");
  const [copied, setCopied] = useState(false);
  const effectiveProvider = lockedProvider ?? provider;

  const snippet = useMemo(() => {
    if (tab === "read") {
      return buildReadSnippet(recipe, token, apiBase);
    }
    return buildIngestSnippet(effectiveProvider, token, apiBase);
  }, [tab, recipe, effectiveProvider, token, apiBase]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(snippet);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard may be unavailable; the snippet is still selectable.
    }
  }

  const tabs: Array<{ id: Tab; label: string; visible: boolean }> = [
    { id: "send", label: t("settings.quickstart_tab_send"), visible: showSend },
    { id: "read", label: t("settings.quickstart_tab_read"), visible: showRead },
  ];

  return (
    <div className="rounded-md border border-slate-200 p-3 text-sm dark:border-slate-700">
      {showSend && showRead ? (
        <div className="mb-3 flex gap-1 rounded-md bg-slate-100 p-1 dark:bg-slate-800/60">
          {tabs
            .filter((tt) => tt.visible)
            .map((tt) => (
              <button
                key={tt.id}
                type="button"
                onClick={() => setTab(tt.id)}
                className={`flex-1 rounded px-3 py-1 text-xs font-medium transition ${
                  tab === tt.id
                    ? "bg-white text-slate-900 shadow-sm dark:bg-slate-700 dark:text-slate-100"
                    : "text-slate-600 dark:text-slate-300"
                }`}
              >
                {tt.label}
              </button>
            ))}
        </div>
      ) : null}

      <div className="mb-2 flex items-center justify-between gap-2">
        <label className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          {tab === "read"
            ? t("settings.quickstart_label_read")
            : t("settings.quickstart_label")}
        </label>
        {tab === "send" ? (
          lockedProvider ? (
            <span className="text-xs font-semibold text-slate-700 dark:text-slate-200">
              {t(`onboarding.ci_label.${lockedProvider}`)}
            </span>
          ) : (
            <select
              className="input w-44 text-xs"
              value={provider}
              onChange={(e) => setProvider(e.target.value as IngestProvider)}
            >
              <option value="jenkins">Jenkins</option>
              <option value="circleci">CircleCI</option>
              <option value="teamcity">TeamCity</option>
              <option value="drone">Drone / Woodpecker</option>
              <option value="github_actions">GitHub Actions</option>
              <option value="gitlab_ci">GitLab CI</option>
              <option value="generic">{t("settings.quickstart_other")}</option>
            </select>
          )
        ) : (
          <select
            className="input w-44 text-xs"
            value={recipe}
            onChange={(e) => setRecipe(e.target.value as ReadRecipe)}
          >
            <option value="list">{t("settings.quickstart_recipe_list")}</option>
            <option value="stats">{t("settings.quickstart_recipe_stats")}</option>
            <option value="morning_digest">
              {t("settings.quickstart_recipe_digest")}
            </option>
          </select>
        )}
      </div>
      <pre className="max-h-72 overflow-auto rounded bg-slate-50 p-3 font-mono text-[11px] leading-relaxed dark:bg-slate-800/60">
        {snippet}
      </pre>
      {tab === "read" ? (
        <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
          {t("settings.quickstart_read_hint")}{" "}
          <a
            href={docsUrl("api-read", i18n.language)}
            target="_blank"
            rel="noopener noreferrer"
            className="underline hover:no-underline"
          >
            {t("settings.quickstart_read_hint_link")}
          </a>
        </p>
      ) : null}
      <div className="mt-2 flex items-center justify-end">
        <button className="btn-secondary" type="button" onClick={copy}>
          {copied ? (
            <Check className="mr-1 h-4 w-4 text-emerald-500" />
          ) : (
            <Copy className="mr-1 h-4 w-4" />
          )}
          {copied ? t("common.copied") : t("settings.copy_snippet")}
        </button>
      </div>
    </div>
  );
}
