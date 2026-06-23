import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  ReactNode,
} from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { AlertTriangle } from "lucide-react";
import clsx from "clsx";
import { useTranslation } from "react-i18next";

export type ConfirmTone = "default" | "danger" | "warning";

export interface ConfirmOptions {
  title?: string;
  message?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: ConfirmTone;
}

type Resolver = (ok: boolean) => void;

interface ConfirmContextValue {
  confirm: (opts: ConfirmOptions) => Promise<boolean>;
}

const ConfirmContext = createContext<ConfirmContextValue | null>(null);

export function useConfirm() {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error("useConfirm must be used inside <ConfirmProvider>");
  return ctx.confirm;
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [opts, setOpts] = useState<ConfirmOptions>({});
  const resolverRef = useRef<Resolver | null>(null);
  const { t } = useTranslation();

  const confirm = useCallback((next: ConfirmOptions) => {
    setOpts(next);
    setOpen(true);
    return new Promise<boolean>((resolve) => {
      resolverRef.current = resolve;
    });
  }, []);

  const settle = useCallback((ok: boolean) => {
    if (resolverRef.current) {
      resolverRef.current(ok);
      resolverRef.current = null;
    }
    setOpen(false);
  }, []);

  const tone = opts.tone ?? "default";
  const confirmBtnClass =
    tone === "danger"
      ? "bg-rose-600 text-white hover:bg-rose-700 focus:ring-rose-500"
      : tone === "warning"
        ? "bg-amber-500 text-white hover:bg-amber-600 focus:ring-amber-400"
        : "bg-brand-600 text-white hover:bg-brand-700 focus:ring-brand-500";

  const value = useMemo(() => ({ confirm }), [confirm]);

  return (
    <ConfirmContext.Provider value={value}>
      {children}
      <Dialog.Root
        open={open}
        onOpenChange={(o) => {
          if (!o) settle(false);
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[90] bg-black/50 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in" />
          <Dialog.Content
            className={clsx(
              "fixed left-1/2 top-1/2 z-[95] w-full max-w-md -translate-x-1/2 -translate-y-1/2",
              "rounded-xl border border-slate-200 bg-white p-6 shadow-2xl",
              "dark:border-slate-700 dark:bg-slate-900",
              "data-[state=open]:animate-in data-[state=open]:fade-in data-[state=open]:zoom-in-95",
            )}
          >
            <div className="flex items-start gap-3">
              {tone !== "default" && (
                <div
                  className={clsx(
                    "flex h-10 w-10 flex-none items-center justify-center rounded-full",
                    tone === "danger"
                      ? "bg-rose-100 text-rose-600 dark:bg-rose-900/40 dark:text-rose-300"
                      : "bg-amber-100 text-amber-600 dark:bg-amber-900/40 dark:text-amber-300",
                  )}
                >
                  <AlertTriangle className="h-5 w-5" />
                </div>
              )}
              <div className="flex-1">
                <Dialog.Title className="text-lg font-semibold text-slate-900 dark:text-slate-100">
                  {opts.title ?? t("confirm.default_title")}
                </Dialog.Title>
                {opts.message && (
                  <Dialog.Description asChild>
                    <div className="mt-2 text-sm text-slate-600 dark:text-slate-300">
                      {opts.message}
                    </div>
                  </Dialog.Description>
                )}
              </div>
            </div>
            <div className="mt-6 flex items-center justify-end gap-2">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => settle(false)}
              >
                {opts.cancelLabel ?? t("common.cancel")}
              </button>
              <button
                type="button"
                className={clsx(
                  "inline-flex items-center justify-center rounded-lg px-4 py-2 text-sm font-medium shadow-sm transition-colors",
                  "focus:outline-none focus:ring-2 focus:ring-offset-2 dark:focus:ring-offset-slate-900",
                  confirmBtnClass,
                )}
                onClick={() => settle(true)}
                autoFocus
              >
                {opts.confirmLabel ?? t("common.confirm")}
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </ConfirmContext.Provider>
  );
}
