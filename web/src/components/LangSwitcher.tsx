import { useTranslation } from "react-i18next";
import { Globe } from "lucide-react";
import clsx from "clsx";

const LANGS = [
  { code: "en", label: "EN" },
  { code: "ru", label: "RU" },
] as const;

export function LangSwitcher({ className }: { className?: string }) {
  const { i18n, t } = useTranslation();
  const current = (i18n.resolvedLanguage || i18n.language || "en").slice(0, 2);

  return (
    <div
      className={clsx(
        "inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white p-0.5 text-xs dark:border-slate-700 dark:bg-slate-900",
        className,
      )}
      role="group"
      aria-label={t("common.language")}
    >
      <Globe className="ml-1 h-3.5 w-3.5 text-slate-400" />
      {LANGS.map((lng) => (
        <button
          key={lng.code}
          type="button"
          onClick={() => void i18n.changeLanguage(lng.code)}
          className={clsx(
            "rounded-md px-2 py-1 font-medium transition-colors",
            current === lng.code
              ? "bg-brand-600 text-white"
              : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800",
          )}
          aria-pressed={current === lng.code}
        >
          {lng.label}
        </button>
      ))}
    </div>
  );
}

export default LangSwitcher;
