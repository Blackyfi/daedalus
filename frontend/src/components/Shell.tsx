import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useApp } from "../store";
import RunnerBar from "./RunnerBar";
import SubscriptionChip from "./SubscriptionChip";

export default function Shell() {
  const navigate = useNavigate();
  const location = useLocation();
  const { banner, setAuthed, clearFlash } = useApp();
  const [menuOpen, setMenuOpen] = useState(false);

  // Auto-close the mobile drawer whenever the user navigates away. Without
  // this, tapping a link leaves the drawer open over the new page until the
  // next tap on the brand / hamburger.
  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  async function logout() {
    try {
      await api("/api/v1/auth/logout", { method: "POST" });
    } catch {}
    setAuthed(false);
    navigate("/login", { replace: true });
  }

  const navClass = ({ isActive }: { isActive: boolean }) =>
    `block rounded px-3 py-2 text-xs uppercase tracking-wide md:py-1 ${
      isActive ? "bg-accent/10 text-accent" : "text-muted hover:text-text"
    }`;

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-border bg-panel">
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-3 px-3 py-2 sm:gap-4 sm:px-6 sm:py-3">
          <Link
            to="/"
            className="text-lg font-semibold text-accent"
            onClick={() => setMenuOpen(false)}
          >
            DAEDALUS
          </Link>
          <button
            type="button"
            aria-label={menuOpen ? "Close navigation" : "Open navigation"}
            aria-expanded={menuOpen}
            aria-controls="primary-nav"
            onClick={() => setMenuOpen((v) => !v)}
            className="btn ml-auto md:hidden"
          >
            <span aria-hidden="true">{menuOpen ? "✕" : "☰"}</span>
            <span className="sr-only">Menu</span>
          </button>
          <nav
            id="primary-nav"
            className={
              "order-last w-full flex-col gap-1 md:order-none md:flex md:w-auto md:flex-row md:gap-2 " +
              (menuOpen ? "flex" : "hidden")
            }
          >
            <NavLink to="/" end className={navClass}>
              Projects
            </NavLink>
            <NavLink to="/kpis" className={navClass}>
              KPIs
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
          </nav>
          <div className="hidden md:block md:flex-1" />
          <div className="hidden items-center gap-3 md:flex">
            <RunnerBar />
            <SubscriptionChip />
          </div>
          <button onClick={logout} className="btn hidden md:inline-flex">
            Log out
          </button>
          {menuOpen && (
            <div className="order-last flex w-full flex-col gap-2 border-t border-border pt-2 md:hidden">
              <RunnerBar />
              <SubscriptionChip />
              <button onClick={logout} className="btn w-full">
                Log out
              </button>
            </div>
          )}
        </div>
      </header>
      {banner && (
        <div
          role="status"
          className={`flex items-start gap-2 px-3 py-2 text-sm sm:px-6 ${
            banner.tone === "error"
              ? "bg-danger/10 text-danger"
              : banner.tone === "success"
                ? "bg-accent/10 text-accent"
                : "bg-panel2 text-muted"
          }`}
        >
          <span className="flex-1">{banner.message}</span>
          <button
            type="button"
            aria-label="Dismiss banner"
            onClick={clearFlash}
            className="-my-1 inline-flex min-h-[32px] min-w-[32px] items-center justify-center rounded text-base hover:bg-black/10"
          >
            <span aria-hidden="true">×</span>
          </button>
        </div>
      )}
      <main className="mx-auto w-full max-w-[1600px] flex-1 p-3 sm:p-4 lg:p-6">
        <Outlet />
      </main>
    </div>
  );
}
