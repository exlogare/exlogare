import clsx from "clsx";

type Accent = "brand" | "emerald" | "rose" | "sky";

const ACCENT_CLASSES: Record<Accent, { icon: string }> = {
  brand: { icon: "bg-brand-100 text-brand-700 dark:bg-brand-900/50 dark:text-brand-300" },
  emerald: { icon: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/50 dark:text-emerald-300" },
  rose: { icon: "bg-rose-100 text-rose-700 dark:bg-rose-900/50 dark:text-rose-300" },
  sky: { icon: "bg-sky-100 text-sky-700 dark:bg-sky-900/50 dark:text-sky-300" },
};

export function KpiCard({
  icon,
  label,
  value,
  accent = "brand",
  hint,
}: {
  icon: React.ReactNode;
  label: React.ReactNode;
  value: React.ReactNode;
  accent?: Accent;
  hint?: React.ReactNode;
}) {
  const tones = ACCENT_CLASSES[accent];
  return (
    <div className="card-hover group relative">
      <div className="relative z-10 flex items-center justify-between text-xs uppercase tracking-wide text-slate-500">
        <span>{label}</span>
        <span
          className={clsx(
            "inline-flex h-7 w-7 items-center justify-center rounded-lg",
            tones.icon,
          )}
        >
          {icon}
        </span>
      </div>
      <div className="relative z-10 mt-2 text-2xl font-semibold">{value}</div>
      {hint && (
        <div className="relative z-10 mt-1 text-xs text-slate-500">{hint}</div>
      )}
    </div>
  );
}
