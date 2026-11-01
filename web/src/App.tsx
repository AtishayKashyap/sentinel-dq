import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { Card, Loading } from "./components/ui";
import { STATIC, staticApi } from "./lib/api";
import { Overview } from "./pages/Overview";

// Overview ships in the main bundle (first paint); chart-heavy pages load on demand
// so Recharts never blocks the landing page.
const TablePage = lazy(() => import("./pages/TablePage").then((m) => ({ default: m.TablePage })));
const CheckPage = lazy(() => import("./pages/CheckPage").then((m) => ({ default: m.CheckPage })));
const RunsPage = lazy(() => import("./pages/RunsPage").then((m) => ({ default: m.RunsPage })));

function DemoBanner() {
  const meta = useQuery({ queryKey: ["meta"], queryFn: staticApi.meta, enabled: STATIC });
  if (!STATIC) return null;
  const when = meta.data ? new Date(meta.data.generated_at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "…";
  return (
    <div className="demo-banner" role="note">
      Live demo · a simulated veterinary-clinic database with deliberately injected defects, re-run weekly (last: {when}).{" "}
      <a href="https://github.com/AtishayKashyap/sentinel-dq">Source on GitHub</a>
    </div>
  );
}

export function App() {
  return (
    <>
      <DemoBanner />
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round">
              <path d="M5 13l4 4L19 7" />
            </svg>
          </span>
          sentinel-dq
        </div>
        <nav>
          <NavLink to="/" end>Overview</NavLink>
          <NavLink to="/runs">Runs</NavLink>
        </nav>
      </header>
      <main>
        <Suspense fallback={<Card><Loading rows={4} /></Card>}>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/tables/:datasource/:table" element={<TablePage />} />
          <Route path="/checks/:checkId" element={<CheckPage />} />
          <Route path="*" element={<div className="empty-state">Page not found.</div>} />
        </Routes>
        </Suspense>
      </main>
    </>
  );
}
