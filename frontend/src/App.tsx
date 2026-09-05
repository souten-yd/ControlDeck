import { lazy, Suspense, useEffect } from "react";
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
import WorkflowRunnerPage from "./pages/WorkflowRunner";
import RemotePage from "./pages/Remote";
import GitHubPage from "./pages/GitHub";
import KnowledgePage from "./pages/Knowledge";
import ModelsPage from "./pages/Models";
import AssistantPage from "./pages/Assistant";
import AddonHostPage from "./pages/AddonHost";

/** 再デプロイ後、開きっぱなしの旧画面は消えたchunkを読みに行き
 *  "Importing a module script failed" で落ちる。1度だけ自動再読み込みして復帰する。 */
function lazyPage<T extends { default: React.ComponentType<Record<string, never>> }>(load: () => Promise<T>) {
  return lazy(async () => {
    try {
      return await load();
    } catch (error) {
      const last = Number(sessionStorage.getItem("cd-chunk-reload") || 0);
      if (Date.now() - last > 10_000) {
        sessionStorage.setItem("cd-chunk-reload", String(Date.now()));
        location.reload();
        await new Promise(() => {});  // reload完了までエラー画面を出さない
      }
      throw error;
    }
  });
}

const OpenCodePage = lazyPage(() => import("./features/opencode/OpenCodePage"));
const ApplicationsPage = lazyPage(() => import("./pages/Applications"));
const ProjectLabPage = lazyPage(() => import("./pages/ProjectLab"));

function LazyPage({ children }: { children: React.ReactNode }) {
  return <Suspense fallback={<div className="p-6 text-sm text-zinc-400">読み込み中...</div>}>{children}</Suspense>;
}

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

  const resolvedUser = user ?? data;
  if (resolvedUser) {
    if (resolvedUser.totp_required && !resolvedUser.totp_enabled && location.pathname !== "/settings") {
      return <Navigate to="/settings" replace />;
    }
    return <>{children}</>;
  }
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

/** 機能で切り替わる画面は、route ごと出し入れせずに中で判定する。
 *
 * route 表を作り直すと RouterProvider に別の router を渡すことになる。React Router は
 * router の差し替えを想定していないので、差し替わった瞬間に画面全体が消える。 */
function FeatureRoute({ feature, children }: { feature: string; children: React.ReactNode }) {
  const { data: meta, isLoading } = useMeta();
  if (isLoading) return <div className="p-6 text-sm text-zinc-400">読み込み中...</div>;
  if (!(meta?.enabled_features ?? []).includes(feature)) return <Navigate to="/" replace />;
  return <>{children}</>;
}

function buildRouter() {
  const featureRoutes = [];
  {
    featureRoutes.push({
      path: "opencode",
      element: <FeatureRoute feature="opencode"><Suspense fallback={<div className="p-6 text-sm text-zinc-400">OpenCodeを読み込み中...</div>}><OpenCodePage /></Suspense></FeatureRoute>,
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
      { path: "runner", element: <WorkflowRunnerPage /> },
      { path: "workflows/:id", element: <WorkflowsPage /> },
      { path: "applications", element: <LazyPage><ApplicationsPage /></LazyPage> },
      { path: "project-lab", element: <LazyPage><ProjectLabPage /></LazyPage> },
      { path: "remote", element: <RemotePage /> },
      { path: "github", element: <GitHubPage /> },
      { path: "knowledge", element: <KnowledgePage /> },
      { path: "models", element: <ModelsPage /> },
      { path: "assistant", element: <AssistantPage /> },
      { path: "system", element: <SystemPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "x/:addonId/:viewId/*", element: <AddonHostPage /> },
      ...featureRoutes,
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
  ]);
}

// router は 1 度だけ作る。作り直すと画面が消えるため、module 直下に置いて固定する。
const router = buildRouter();

export default function App() {
  const { isLoading } = useMeta();
  if (isLoading) return <div className="grid h-dvh place-items-center text-sm text-zinc-400">読み込み中...</div>;
  return <RouterProvider router={router} />;
}
