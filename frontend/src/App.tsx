import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type Project, type Result } from "./api";
import { DataView } from "./views/DataView";
import { OptimizeView } from "./views/OptimizeView";
import { PlanView } from "./views/PlanView";
import { ResultsView } from "./views/ResultsView";
import { RoutinesView } from "./views/RoutinesView";
import { SensitivityView } from "./views/SensitivityView";

const TABS = [
  ["data", "Data"],
  ["plan", "Floor plan"],
  ["optimize", "Optimize"],
  ["results", "Results"],
  ["sensitivity", "Sensitivity"],
  ["routines", "Routines"],
] as const;
type Tab = (typeof TABS)[number][0];

export function App() {
  const [project, setProject] = useState<Project | null>(null);
  const [result, setResult] = useState<Result>({ present: false });
  const [tab, setTabState] = useState<Tab>(() => {
    const fromHash = window.location.hash.slice(1);
    return TABS.some(([id]) => id === fromHash) ? (fromHash as Tab) : "data";
  });
  // The tab lives in the URL hash, so a reload or a shared link opens the same view.
  const setTab = (next: Tab) => {
    window.history.replaceState(null, "", `#${next}`);
    setTabState(next);
  };
  const [error, setError] = useState<string | null>(null);
  const [planRoutine, setPlanRoutine] = useState<string | null>(null);

  const fail = useCallback((e: unknown) => {
    const err = e as ApiError;
    setError([err.message, ...(err.issues ?? [])].join(" · "));
  }, []);

  const refreshResult = useCallback(async () => {
    try {
      setResult(await api.result());
    } catch {
      setResult({ present: false });
    }
  }, []);

  /** Every change to the project goes through here so the result stays in step. */
  const onProject = useCallback(
    (p: Project) => {
      setProject(p);
      setError(null);
      if (p.loaded && p.ok && p.feasible) void refreshResult();
      else setResult({ present: false });
    },
    [refreshResult],
  );

  useEffect(() => {
    api.project().then(onProject).catch(fail);
  }, [onProject, fail]);

  const usable = Boolean(project?.loaded && project.ok && project.feasible);
  const showRoutine = (id: string) => {
    setPlanRoutine(id);
    setTab("plan");
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          Reach<small>optimize where things live</small>
        </div>
        <nav className="tabs">
          {TABS.map(([id, label]) => (
            <button
              key={id}
              className={`tab${tab === id ? " active" : ""}`}
              disabled={id !== "data" && !usable}
              onClick={() => setTab(id)}
            >
              {label}
            </button>
          ))}
        </nav>
        {project?.loaded && <span className="project-name">{project.layout?.name}</span>}
      </header>
      <main>
        {error && (
          <div className="banner error row">
            <span className="grow">{error}</span>
            <button className="mini" onClick={() => setError(null)}>Dismiss</button>
          </div>
        )}
        {!project ? (
          <div className="empty">Loading…</div>
        ) : tab === "data" || !usable ? (
          <DataView project={project} onProject={onProject} onError={fail} goTo={() => setTab("plan")} />
        ) : tab === "plan" ? (
          <PlanView project={project} result={result} onProject={onProject} onError={fail} routine={planRoutine} setRoutine={setPlanRoutine} />
        ) : tab === "optimize" ? (
          <OptimizeView project={project} result={result} onDone={refreshResult} onError={fail} goTo={() => setTab("results")} />
        ) : tab === "results" ? (
          <ResultsView project={project} result={result} goOptimize={() => setTab("optimize")} showRoutine={showRoutine} />
        ) : tab === "sensitivity" ? (
          <SensitivityView project={project} result={result} onError={fail} />
        ) : (
          <RoutinesView project={project} onProject={onProject} onError={fail} showRoutine={showRoutine} />
        )}
      </main>
    </div>
  );
}
