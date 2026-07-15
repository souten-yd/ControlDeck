import { lazy, Suspense, useEffect, useMemo } from "react";
import {
  createBrowserRouter,
  Navigate,
  RouterProvider,
  useLocation,
} from "react-router-dom";
import { setUnauthorizedHandler } from "./api/client";
import { useMe, useMeta } from "./api/hooks";
import { useAuth } from "./stores";
import AppLayout from "./layouts/AppLayout";
import LoginPage from "./pages/Login";
import DashboardPage from "./pages/Dashboard";
import AppsPage from "./pages/Apps";
import LogsPage from "./pages/Logs";
import SystemPage from "./pages/System";
import SettingsPage from "./pages/Settings";
import FilesPage from "./pages/Files";
import TerminalPage from "./pages/Terminal";
import WorkflowsPage from "./pages/Workflows";
import RemotePage from "./pages/Remote";
import GitHubPage from "./pages/GitHub";
import KnowledgePage from "./pages/Knowledge";
import ModelsPage from "./pages/Models";
import AssistantPage from "./pages/Assistant";

const OpenCodePage = lazy(() => import("./features/opencode/OpenCodePage"));

function RequireAuth({ children }: { children: React.ReactNode }) {
  const user = useAuth((s) => s.user);
  const setUser = useAuth((s) => s.setUser);
  const location = useLocation();
  const { data, isLoading, isError } = useMe(user === null);

  useEffect(() => {
    if (data) setUser(data);
  }, [data, setUser]);

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
  }, [setUser]);

  if (user) return <>{children}</>;
  if (isLoading)
    return (
      <div className="grid h-dvh place-items-center text-sm text-zinc-400">
        読み込み中...
      </div>
    );
  if (isError || !data)
    return <Navigate to="/login" state={{ from: location }} replace />;
  return <>{children}</>;
}

function buildRouter(enabledFeatures: string[]) {
  const featureRoutes = [];
  if (enabledFeatures.includes("opencode")) {
    featureRoutes.push({
      path: "opencode",
      element: <Suspense fallback={<div className="p-6 text-sm text-zinc-400">OpenCodeを読み込み中...</div>}><OpenCodePage /></Suspense>,
    });
  }
  return createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    path: "/",
    element: (
      <RequireAuth>
        <AppLayout />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "apps", element: <AppsPage /> },
      { path: "logs", element: <LogsPage /> },
      { path: "files", element: <FilesPage /> },
      { path: "terminal", element: <TerminalPage /> },
      { path: "workflows", element: <WorkflowsPage /> },
      { path: "workflows/:id", element: <WorkflowsPage /> },
      { path: "remote", element: <RemotePage /> },
      { path: "github", element: <GitHubPage /> },
      { path: "knowledge", element: <KnowledgePage /> },
      { path: "models", element: <ModelsPage /> },
      { path: "assistant", element: <AssistantPage /> },
      { path: "system", element: <SystemPage /> },
      { path: "settings", element: <SettingsPage /> },
      ...featureRoutes,
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
  ]);
}

export default function App() {
  const { data: meta, isLoading } = useMeta();
  const featureKey = (meta?.enabled_features ?? []).slice().sort().join(",");
  const router = useMemo(() => buildRouter(featureKey ? featureKey.split(",") : []), [featureKey]);
  if (isLoading) return <div className="grid h-dvh place-items-center text-sm text-zinc-400">読み込み中...</div>;
  return <RouterProvider router={router} />;
}
