import { useEffect } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { api } from "./api";
import { useApp } from "./store";
import LoginPage from "./pages/LoginPage";
import ProjectListPage from "./pages/ProjectListPage";
import ProjectPage from "./pages/ProjectPage";
import ConnectorsPage from "./pages/ConnectorsPage";
import AuditPage from "./pages/AuditPage";
import SecurityPage from "./pages/SecurityPage";
import AlgorithmsPage from "./pages/AlgorithmsPage";
import AccountPage from "./pages/AccountPage";
import Shell from "./components/Shell";

function PrivateOutlet({ children }: { children: React.ReactNode }) {
  const authed = useApp((s) => s.authed);
  const bootChecked = useApp((s) => s.bootChecked);
  // While the boot probe hasn't returned yet, render nothing — flipping
  // straight to <Navigate to="/login"> on `authed=false` would race the
  // async session check and replace the URL before the probe completes.
  if (!bootChecked) return null;
  return authed ? <>{children}</> : <Navigate to="/login" replace />;
}

function LoginRouteGuard({ children }: { children: React.ReactNode }) {
  // Inverse: if the boot probe says we're already authed, don't render
  // the login form on top of an active session — kick to the project list.
  const authed = useApp((s) => s.authed);
  const bootChecked = useApp((s) => s.bootChecked);
  if (!bootChecked) return null;
  return authed ? <Navigate to="/" replace /> : <>{children}</>;
}

export default function App() {
  const setAuthed = useApp((s) => s.setAuthed);
  const setBootChecked = useApp((s) => s.setBootChecked);

  // On boot, probe whether the session cookie is still valid.
  useEffect(() => {
    (async () => {
      try {
        await api("/api/v1/projects");
        setAuthed(true);
      } catch {
        setAuthed(false);
      } finally {
        setBootChecked(true);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="min-h-screen w-full overflow-x-hidden">
      <Routes>
        <Route
          path="/login"
          element={
            <LoginRouteGuard>
              <LoginPage />
            </LoginRouteGuard>
          }
        />
        <Route
          element={
            <PrivateOutlet>
              <Shell />
            </PrivateOutlet>
          }
        >
          <Route path="/" element={<ProjectListPage />} />
          <Route path="/projects/:projectId" element={<ProjectPage />} />
          <Route path="/projects/:projectId/runs/:runId" element={<ProjectPage />} />
          <Route path="/connectors" element={<ConnectorsPage />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="/security" element={<SecurityPage />} />
          <Route path="/algorithms" element={<AlgorithmsPage />} />
          <Route path="/account" element={<AccountPage />} />
        </Route>
      </Routes>
    </div>
  );
}
