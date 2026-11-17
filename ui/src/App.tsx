import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth";
import { isAuthError } from "./api/client";
import Jobs from "./screens/Jobs";
import Login from "./screens/Login";
import SolveDetail from "./screens/SolveDetail";
import SpotSetup from "./screens/SpotSetup";
import Spots from "./screens/Spots";
import Compare from "./screens/Compare";
import { useEffect } from "react";

function Shell({ children }: { children: React.ReactNode }) {
  const { logout } = useAuth();
  return (
    <div className="shell">
      <header className="topnav">
        <span className="brand">holdem-solver</span>
        <nav aria-label="main">
          <NavLink to="/spots">Spots</NavLink>
          <NavLink to="/spots/new">New spot</NavLink>
          <NavLink to="/jobs">Jobs</NavLink>
          <NavLink to="/compare">Compare</NavLink>
        </nav>
        <button className="ghost" onClick={() => void logout()}>
          Log out
        </button>
      </header>
      <main>{children}</main>
    </div>
  );
}

function Guard({ children }: { children: React.ReactNode }) {
  const { authed } = useAuth();
  const loc = useLocation();
  if (authed === null) return <p className="status">Loading…</p>;
  if (!authed) return <Navigate to="/login" state={{ from: loc.pathname }} replace />;
  return <Shell>{children}</Shell>;
}

export default function App() {
  const { expire } = useAuth();
  useEffect(() => {
    const onUnhandled = (e: PromiseRejectionEvent) => {
      if (isAuthError(e.reason)) expire();
    };
    window.addEventListener("unhandledrejection", onUnhandled);
    return () => window.removeEventListener("unhandledrejection", onUnhandled);
  }, [expire]);
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/"
        element={
          <Guard>
            <Navigate to="/jobs" replace />
          </Guard>
        }
      />
      <Route path="/jobs" element={<Guard><Jobs /></Guard>} />
      <Route path="/jobs/:id" element={<Guard><SolveDetail /></Guard>} />
      <Route path="/spots" element={<Guard><Spots /></Guard>} />
      <Route path="/spots/new" element={<Guard><SpotSetup /></Guard>} />
      <Route path="/spots/:id/edit" element={<Guard><SpotSetup /></Guard>} />
      <Route path="/compare" element={<Guard><Compare /></Guard>} />
      <Route path="*" element={<Navigate to="/jobs" replace />} />
    </Routes>
  );
}
