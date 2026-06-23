import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  BarChart3,
  Gauge,
  Layers,
  LifeBuoy,
  Link2,
  LogOut,
  Menu,
  Settings,
  Sparkles,
  X,
  Zap,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../lib/auth";
import clsx from "clsx";
import LangSwitcher from "../components/LangSwitcher";

type NavItem = {
  to: string;
  icon: typeof Gauge;
  labelKey: string;
  end?: boolean;
};

const NAV: readonly NavItem[] = [
  { to: "/dashboard", icon: Gauge, labelKey: "nav.overview", end: true },
  { to: "/dashboard/integrations", icon: Link2, labelKey: "nav.integrations" },
  { to: "/dashboard/analyses", icon: Sparkles, labelKey: "nav.analyses" },
  { to: "/dashboard/clusters", icon: Layers, labelKey: "nav.clusters" },
  { to: "/dashboard/stats", icon: BarChart3, labelKey: "nav.stats" },
  { to: "/dashboard/settings", icon: Settings, labelKey: "nav.settings" },
];

export default function DashboardLayout() {
  const { me, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const { t } = useTranslation();
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => {
    setNavOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!navOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setNavOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [navOpen]);

  const sidebarBody = (
    <>
      <div className="mb-6 shrink-0 flex items-center gap-2">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-600 text-white">
          <Zap className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <div className="text-sm font-semibold">{t("common.app_name")}</div>
          <div className="text-[10px] font-medium uppercase tracking-wide text-brand-600 dark:text-brand-400">
            {t("common.app_edition")}
          </div>
          <div className="truncate text-xs text-slate-500">{me?.tenant.name}</div>
        </div>
      </div>
      <nav className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto">
        {NAV.map(({ to, icon: Icon, labelKey, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              clsx(
                "flex shrink-0 items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-brand-50 text-brand-700 dark:bg-brand-900/40 dark:text-brand-200"
                  : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800",
              )
            }
          >
            <Icon className="h-4 w-4" />
            {t(labelKey)}
          </NavLink>
        ))}
      </nav>
      <div className="shrink-0 space-y-3 border-t border-slate-200 pt-4 dark:border-slate-800">
        <LangSwitcher className="w-full justify-center" />
        <div className="truncate text-xs text-slate-500">{me?.user.email}</div>
        <a
          href="mailto:admin@localhost"
          className="inline-flex w-full items-center justify-center gap-2 rounded-lg px-3 py-2 text-xs font-medium text-slate-600 transition hover:bg-slate-100 hover:text-brand-700 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-brand-300"
        >
          <LifeBuoy className="h-3.5 w-3.5" />
          {t("nav.support")}
        </a>
        <button
          className="btn-secondary w-full"
          onClick={async () => {
            await logout();
            navigate("/login");
          }}
        >
          <LogOut className="h-4 w-4" /> {t("auth.sign_out")}
        </button>
      </div>
    </>
  );

  return (
    <div className="flex h-screen min-h-0 flex-col overflow-hidden md:flex-row">
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-3 md:hidden dark:border-slate-800 dark:bg-slate-900">
        <button
          type="button"
          className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-slate-200 text-slate-700 dark:border-slate-700 dark:text-slate-200"
          aria-label={t("nav.open_menu")}
          aria-expanded={navOpen}
          onClick={() => setNavOpen((v) => !v)}
        >
          {navOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-white">
            <Zap className="h-4 w-4" />
          </div>
        <div className="flex flex-col items-start">
          <span className="text-sm font-semibold">{t("common.app_name")}</span>
          <span className="text-[10px] font-medium uppercase tracking-wide text-brand-600 dark:text-brand-400">
            {t("common.app_edition")}
          </span>
        </div>
        </div>
        <div className="w-10" />
      </div>

      <aside className="hidden h-full min-h-0 w-64 shrink-0 flex-col border-r border-slate-200 bg-white p-4 md:flex dark:border-slate-800 dark:bg-slate-900">
        {sidebarBody}
      </aside>

      {navOpen && (
        <button
          type="button"
          aria-label={t("nav.close_menu")}
          className="fixed inset-0 top-14 z-40 bg-black/40 backdrop-blur-sm md:hidden"
          onClick={() => setNavOpen(false)}
        />
      )}
      <aside
        className={clsx(
          "fixed left-0 right-0 top-14 bottom-0 z-50 flex flex-col border-r border-slate-200 bg-white p-4 transition-transform duration-200 md:hidden dark:border-slate-800 dark:bg-slate-900",
          navOpen ? "translate-x-0" : "-translate-x-full",
        )}
        aria-hidden={!navOpen}
      >
        {sidebarBody}
      </aside>

      <main className="min-h-0 flex-1 overflow-y-auto bg-slate-50 p-4 sm:p-6 md:p-8 dark:bg-slate-950">
        <Outlet />
      </main>
    </div>
  );
}
