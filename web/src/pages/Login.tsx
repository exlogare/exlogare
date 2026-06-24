import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Zap } from "lucide-react";
import LangSwitcher from "../components/LangSwitcher";

type SessionResponse = {
  access_token: string;
  token_type: string;
};

export default function LoginPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await api<SessionResponse>("/api/auth/login", {
        method: "POST",
        body: { email, password },
      });
      await login();
      navigate("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : t("toast.unknown_error"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-brand-50 to-slate-50 p-6 dark:from-slate-900 dark:to-slate-950">
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-brand-600 text-white">
            <Zap className="h-6 w-6" />
          </div>
          <div className="flex-1">
            <h1 className="text-2xl font-semibold">{t("common.app_name")}</h1>
            <p className="text-xs font-medium uppercase tracking-wide text-brand-600 dark:text-brand-400">
              {t("common.app_edition")}
            </p>
            <p className="text-sm text-slate-500">{t("auth.tagline")}</p>
          </div>
          <LangSwitcher />
        </div>

        <div className="card">
          <h2 className="mb-2 text-lg font-semibold">{t("auth.signIn")}</h2>
          <p className="mb-6 text-sm text-slate-500">{t("auth.password_intro")}</p>
          <form onSubmit={onSubmit} className="space-y-4">
            <div>
              <label className="mb-1 block text-sm font-medium">{t("auth.email")}</label>
              <input
                type="email"
                className="input w-full"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="username"
              />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium">{t("auth.password")}</label>
              <input
                type="password"
                className="input w-full"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="current-password"
              />
            </div>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <button type="submit" className="btn-primary w-full" disabled={loading}>
              {loading ? t("auth.signing_in") : t("auth.signIn")}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
