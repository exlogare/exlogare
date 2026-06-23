import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { Me } from "./types";
import { ApiError, api, clearCsrfCache } from "./api";

type AuthContextValue = {
  me: Me | null;
  loading: boolean;
  /** Hydrate the ``me`` state after a successful ``POST /api/auth/verify``. */
  login: () => Promise<Me>;
  /** Invalidate the server-side session and clear local context. */
  logout: () => Promise<void>;
  /** Re-fetch ``/api/auth/me``; sets ``me`` to null on 401. */
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api<Me>("/api/auth/me");
      setMe(data);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setMe(null);
      } else {
        setMe(null);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(async () => {
    const data = await api<Me>("/api/auth/me");
    setMe(data);
    return data;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api<{ status: string }>("/api/auth/logout", { method: "POST" });
    } catch {
      // Best-effort; clear local state regardless.
    }
    clearCsrfCache();
    setMe(null);
  }, []);

  const value = useMemo(
    () => ({ me, loading, login, logout, refresh }),
    [me, loading, login, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
