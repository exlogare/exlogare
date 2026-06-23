export type ToastKind = "success" | "error" | "info" | "warning";

export interface Toast {
  id: string;
  kind: ToastKind;
  title?: string;
  message?: string;
  duration: number | null;
}

type Listener = (toasts: Toast[]) => void;

class ToastStore {
  private toasts: Toast[] = [];
  private listeners = new Set<Listener>();

  subscribe = (listener: Listener) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = () => this.toasts;

  private emit() {
    this.listeners.forEach((l) => l(this.toasts));
  }

  push(t: Omit<Toast, "id">): string {
    const id =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : Math.random().toString(36).slice(2);
    this.toasts = [...this.toasts, { id, ...t }];
    this.emit();
    return id;
  }

  dismiss(id: string) {
    this.toasts = this.toasts.filter((t) => t.id !== id);
    this.emit();
  }

  clear() {
    this.toasts = [];
    this.emit();
  }
}

export const toastStore = new ToastStore();

type ToastInput = string | { title?: string; message?: string; duration?: number | null };

function expand(input: ToastInput): { title?: string; message?: string; duration: number | null } {
  if (typeof input === "string") {
    return { message: input, duration: 4000 };
  }
  return {
    title: input.title,
    message: input.message,
    duration: input.duration === undefined ? 4000 : input.duration,
  };
}

export const toast = {
  success(input: ToastInput) {
    return toastStore.push({ kind: "success", ...expand(input) });
  },
  error(input: ToastInput) {
    const { duration, ...rest } = expand(input);
    return toastStore.push({
      kind: "error",
      duration: duration === 4000 ? 6000 : duration,
      ...rest,
    });
  },
  info(input: ToastInput) {
    return toastStore.push({ kind: "info", ...expand(input) });
  },
  warning(input: ToastInput) {
    return toastStore.push({ kind: "warning", ...expand(input) });
  },
  dismiss(id: string) {
    toastStore.dismiss(id);
  },
};

export default toast;
