import { useEffect, useSyncExternalStore } from "react";
import { CheckCircle2, AlertTriangle, Info, XCircle, X } from "lucide-react";
import clsx from "clsx";
import { toastStore, Toast } from "../../lib/toast";

function Icon({ kind }: { kind: Toast["kind"] }) {
  switch (kind) {
    case "success":
      return <CheckCircle2 className="h-5 w-5 text-emerald-500" />;
    case "error":
      return <XCircle className="h-5 w-5 text-rose-500" />;
    case "info":
      return <Info className="h-5 w-5 text-sky-500" />;
    case "warning":
      return <AlertTriangle className="h-5 w-5 text-amber-500" />;
  }
}

function ToastItem({ toast }: { toast: Toast }) {
  useEffect(() => {
    if (!toast.duration) return;
    const t = window.setTimeout(() => toastStore.dismiss(toast.id), toast.duration);
    return () => window.clearTimeout(t);
  }, [toast.id, toast.duration]);

  return (
    <div
      role="status"
      className={clsx(
        "pointer-events-auto flex w-80 max-w-full items-start gap-3 rounded-lg border p-3 shadow-lg backdrop-blur",
        "bg-white/95 dark:bg-slate-900/95",
        "border-slate-200 dark:border-slate-700",
        "animate-in fade-in slide-in-from-right-2",
      )}
    >
      <Icon kind={toast.kind} />
      <div className="min-w-0 flex-1 text-sm">
        {toast.title && (
          <div className="font-semibold text-slate-900 dark:text-slate-100">
            {toast.title}
          </div>
        )}
        {toast.message && (
          <div className="text-slate-600 dark:text-slate-300">
            {toast.message}
          </div>
        )}
      </div>
      <button
        aria-label="Close"
        onClick={() => toastStore.dismiss(toast.id)}
        className="text-slate-400 hover:text-slate-700 dark:hover:text-slate-200"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}

export function Toaster() {
  const toasts = useSyncExternalStore(
    toastStore.subscribe,
    toastStore.getSnapshot,
    toastStore.getSnapshot,
  );

  return (
    <div
      className="pointer-events-none fixed inset-0 z-[100] flex flex-col items-end justify-start gap-2 p-4 sm:p-6"
      aria-live="polite"
      aria-atomic="true"
    >
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} />
      ))}
    </div>
  );
}

export default Toaster;
