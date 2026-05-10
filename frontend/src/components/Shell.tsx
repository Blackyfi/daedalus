import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useApp } from "../store";
import RunnerBar from "./RunnerBar";
import SubscriptionChip from "./SubscriptionChip";

export default function Shell() {
  const navigate = useNavigate();
  const { banner, setAuthed, clearFlash } = useApp();

  async function logout() {
    try {
      await api("/api/v1/auth/logout", { method: "POST" });
    } catch {}
    setAuthed(false);
    navigate("/login", { replace: true });
  }

  const navClass = ({ isActive }: { isActive: boolean }) =>
    `px-3 py-1 rounded text-xs uppercase tracking-wide ${
      isActive ? "bg-accent/10 text-accent" : "text-muted hover:text-text"
    }`;

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-border bg-panel">
        <div className="mx-auto flex max-w-[1600px] items-center gap-4 px-6 py-3">
          <Link to="/" className="text-lg font-semibold text-accent">
            DAEDALUS
          </Link>
          <nav className="flex gap-2">
            <NavLink to="/" end className={navClass}>
              Projects
            </NavLink>
            <NavLink to="/connectors" className={navClass}>
              Connectors
            </NavLink>
            <NavLink to="/audit" className={navClass}>
              Audit
            </NavLink>
            <NavLink to="/security" className={navClass}>
              Security
            </NavLink>
            <NavLink to="/algorithms" className={navClass}>
              Algorithms
            </NavLink>
            <NavLink to="/account" className={navClass}>
              Account
            </NavLink>
          </nav>
          <div className="flex-1" />
          <RunnerBar />
          <SubscriptionChip />
          <button onClick={logout} className="btn">
            Log out
          </button>
        </div>
      </header>
      {banner && (
        <div
          className={`px-6 py-2 text-sm ${
            banner.tone === "error"
              ? "bg-danger/10 text-danger"
              : banner.tone === "success"
                ? "bg-accent/10 text-accent"
                : "bg-panel2 text-muted"
          }`}
          onClick={clearFlash}
        >
          {banner.message}
        </div>
      )}
      <main className="mx-auto w-full max-w-[1600px] flex-1 p-6">
        <Outlet />
      </main>
    </div>
  );
}
