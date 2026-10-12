import { useEffect, useState } from "react";
import { api, fmtPct, watchJob, type JobSnapshot, type Project, type Result } from "../api";
import { LineChart } from "../components/charts";

interface Props {
  project: Project;
  result: Result;
  onDone: () => Promise<void>;
  onError: (e: unknown) => void;
  goTo: () => void;
}

// Survives switching tabs, so a running job is picked up again when the view comes back.
let lastJob: JobSnapshot | null = null;

export function OptimizeView({ project, result, onDone, onError, goTo }: Props) {
  const [seed, setSeed] = useState(result.seed ?? 0);
  const [mode, setMode] = useState<"iterations" | "time">("iterations");
  const [iterations, setIterations] = useState(1_000_000);
  const [seconds, setSeconds] = useState(30);
  const [restarts, setRestarts] = useState(5);
  const [fromResult, setFromResult] = useState(false);
  const [job, setJobState] = useState<JobSnapshot | null>(lastJob);
  const setJob = (j: JobSnapshot | null) => {
    lastJob = j;
    setJobState(j);
  };
  const running = job?.status === "running";

  const follow = (id: string) =>
    watchJob(id, (j) => {
      setJob(j);
      if (j.status !== "running") void onDone();
    });

  useEffect(() => {
    if (lastJob?.status === "running") return follow(lastJob.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const start = async () => {
    try {
      const j = await api.optimize({
        seed,
        restarts,
        start: fromResult && result.present ? "result" : "greedy",
        ...(mode === "iterations" ? { iterations } : { time_limit_s: seconds }),
      });
      setJob(j);
      follow(j.id);
    } catch (e) {
      onError(e);
    }
  };
  const stop = async () => {
    if (job) await api.stop(job.id).catch(onError);
  };

  const p = job?.progress ?? {};
  const best = p.best_cost ?? result.cost;
  const greedy = p.initial_cost ?? result.greedy_cost;
  // While running, the live samples; afterwards the denser history stored with the result.
  const history = running ? (job?.history ?? []) : result.history?.length ? result.history : (job?.history ?? []);
  const pinned = project.items?.filter((i) => i.pin_source === "ui").length ?? 0;

  return (
    <>
      <section className="card stack">
        <h2>Run the optimizer</h2>
        <div className="row top">
          <label className="field">
            Seed
            <input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} disabled={running} />
          </label>
          <label className="field">
            Budget
            <div className="seg">
              <button className={mode === "iterations" ? "active" : ""} onClick={() => setMode("iterations")} disabled={running}>Iterations</button>
              <button className={mode === "time" ? "active" : ""} onClick={() => setMode("time")} disabled={running}>Time limit</button>
            </div>
          </label>
          {mode === "iterations" ? (
            <label className="field">
              Iterations
              <input type="number" min={0} step={50000} value={iterations} onChange={(e) => setIterations(Number(e.target.value))} disabled={running} />
            </label>
          ) : (
            <label className="field">
              Seconds
              <input type="number" min={1} value={seconds} onChange={(e) => setSeconds(Number(e.target.value))} disabled={running} />
            </label>
          )}
          <label className="field" title="Independent runs, each with the full budget. The best one is kept.">
            Restarts
            <input type="number" min={1} max={100} value={restarts} onChange={(e) => setRestarts(Math.max(1, Number(e.target.value)))} disabled={running} style={{ width: 80 }} />
          </label>
          <label className="field">
            Start from
            <select value={fromResult ? "result" : "greedy"} onChange={(e) => setFromResult(e.target.value === "result")} disabled={running || !result.present}>
              <option value="greedy">Greedy solution</option>
              <option value="result">Last result</option>
            </select>
          </label>
          <span className="spacer" />
          <div className="row" style={{ alignSelf: "flex-end" }}>
            {running ? (
              <button className="danger" onClick={stop}>Stop and keep the best so far</button>
            ) : (
              <button className="primary" onClick={start}>{result.present ? "Optimize again" : "Optimize"}</button>
            )}
          </div>
        </div>
        <p className="muted small">
          {mode === "iterations"
            ? "With an iteration budget the same seed always gives the same result. Each restart runs the full number of iterations and the best one is kept."
            : "A time limit depends on the speed of this machine, so results are not exactly reproducible."}
          {pinned > 0 ? ` ${pinned} item${pinned === 1 ? " is" : "s are"} pinned from the floor plan.` : ""}
        </p>
      </section>

      {(job || result.present) && (
        <>
          <div className="tiles">
            <div className="tile">
              <div className="label">Status</div>
              <div className="value" style={{ fontSize: 18 }}>
                {running ? "Running…" : job?.status === "stopped" || (!job && result.stopped_early) ? "Stopped early" : job?.status === "error" ? "Failed" : "Finished"}
              </div>
              <div className="bar progress" style={{ marginTop: 8 }}>
                <span style={{ width: `${running ? (p.fraction ?? 0) * 100 : 100}%` }} />
              </div>
            </div>
            <div className="tile">
              <div className="label">Best cost</div>
              <div className="value">{best !== undefined ? best.toFixed(4) : "–"}</div>
              <div className="sub">1.0 is roughly the greedy solution</div>
            </div>
            <div className="tile">
              <div className="label">Against the greedy start</div>
              <div className="value">{best !== undefined && greedy ? fmtPct(best / greedy - 1) : "–"}</div>
              <div className="sub">greedy cost {greedy?.toFixed(4) ?? "–"}</div>
            </div>
            <div className="tile">
              <div className="label">Iterations</div>
              <div className="value">{(p.iteration ?? result.iterations ?? 0).toLocaleString()}</div>
              <div className="sub">{(p.elapsed_s ?? result.elapsed_s ?? 0).toFixed(1)} s</div>
            </div>
          </div>
          {job?.error && <div className="banner error">{job.error}</div>}
          <section className="card">
            <h2>Best cost found so far</h2>
            <LineChart points={history} reference={greedy} referenceLabel="greedy start" xLabel="Iteration" yLabel="Best cost" />
          </section>
          {!running && result.present && (
            <div className="row">
              <button className="primary" onClick={goTo}>See the results</button>
            </div>
          )}
        </>
      )}
    </>
  );
}
