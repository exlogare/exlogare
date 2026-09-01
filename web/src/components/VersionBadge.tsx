import clsx from "clsx";
import { useTranslation } from "react-i18next";
import { useAppUpdate } from "../lib/useAppUpdate";

type VersionBadgeProps = {
  className?: string;
};

export default function VersionBadge({ className }: VersionBadgeProps) {
  const { t } = useTranslation();
  const { currentVersion, updateAvailable, loading, releasesUrl } = useAppUpdate();

  if (loading && !currentVersion) return null;

  const label = updateAvailable
    ? t("common.update_available")
    : currentVersion
      ? `v${currentVersion}`
      : null;

  if (!label) return null;

  const styles = clsx(
    "inline-flex w-fit items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide transition",
    updateAvailable
      ? "bg-amber-100 text-amber-800 hover:bg-amber-200 dark:bg-amber-900/50 dark:text-amber-200 dark:hover:bg-amber-900/70"
      : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
    className,
  );

  if (updateAvailable) {
    return (
      <a
        href={releasesUrl}
        target="_blank"
        rel="noopener noreferrer"
        className={styles}
        title={t("common.update_available_hint", { version: currentVersion ?? "" })}
      >
        {label}
      </a>
    );
  }

  return <span className={styles}>{label}</span>;
}
