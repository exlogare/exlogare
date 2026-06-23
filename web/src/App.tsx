import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./lib/auth";
import LoginPage from "./pages/Login";
import OnboardingPage from "./pages/Onboarding";
import DashboardLayout from "./layouts/DashboardLayout";
import OverviewPage from "./pages/Overview";
import IntegrationsPage from "./pages/Integrations";
import AnalysesPage from "./pages/Analyses";
import AnalysisDetailPage from "./pages/AnalysisDetail";
import ClustersPage from "./pages/Clusters";
import StatsPage from "./pages/Stats";
import SettingsPage from "./pages/Settings";
import AuditLogPage from "./pages/AuditLog";

function Gate({ children }: { children: React.ReactNode }) {
  const { me, loading } = useAuth();
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-slate-500">Loading...</div>
    );
  }
  if (!me) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/onboarding"
          element={
            <Gate>
              <OnboardingPage />
            </Gate>
          }
        />
        <Route
          path="/dashboard"
          element={
            <Gate>
              <DashboardLayout />
            </Gate>
          }
        >
          <Route index element={<OverviewPage />} />
          <Route path="integrations" element={<IntegrationsPage />} />
          <Route path="analyses" element={<AnalysesPage />} />
          <Route path="analyses/:id" element={<AnalysisDetailPage />} />
          <Route path="clusters" element={<ClustersPage />} />
          <Route path="stats" element={<StatsPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="audit" element={<AuditLogPage />} />
        </Route>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </AuthProvider>
  );
}

export default App;
