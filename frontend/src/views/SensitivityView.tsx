import { useEffect, useState } from "react";
import { api, watchJob, type JobSnapshot, type Project, type Result, type RunSet, type Sensitivity, type Shares } from "../api";
import { Histogram } from "../components/charts";

interface Props {
  project: Project;
  result: Result;
  onError: (e: unknown) => void;
}

let lastJob: JobSnapshot | null = null;
const BADGE: Record<string, string> = { robust: "good", noisy: "", sensitive: "warning", unused: "", pinned: "" };
const MODES = [
  ["both", "Both"],
  ["baseline", "Noise only"],
  ["perturbed", "Perturbed only"],
] as const;
type Mode = (typeof MODES)[number][0];
const pct = (v: number | undefined) => (v === undefined ? "–" : `${Math.round(v * 100)}%`);

function ShareBar({ shares, by }: { shares: Shares | null; by: "exact" | "area" }) {
  if (!shares) return <span className="muted small">–</span>;
  return (
    <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
      <div className="bar grow"><span style={{ width: `${shares[by] * 100}%` }} /></div>
      <span className="small" style={{ width: 36, textAlign: "right" }}>{pct(shares[by])}</span>
    </div>
  );
}

function SetTile({ title, sub, set }: { title: string; sub: string; set: RunSet }) {
  return (
    <div className="tile">
      <div className="label">{title}</div>
      <div className="value">{pct(set.area_stability)}<small>stay on the same wall and level</small></div>
      <div className="sub">{pct(set.exact_stability)} in the exact compartment · {set.runs} runs · {sub}</div>
    </div>
  );
}

export function SensitivityView({ project, result, onError }: Props) {
  const [runs, setRuns] = useState(20);
  const [mode, setMode] = useState<Mode>("both");
  const [perturbation, setPerturbation] = useState(30);
  const [iterations, setIterations] = useState(1_000_000);
  const [restarts, setRestarts] = useState(5);
  const [seed, setSeed] = useState(0);
  const [job, setJobState] = useState<JobSnapshot | null>(lastJob);
  const [report, setReport] = useState<Sensitivity>({ present: false });
  const [show, setShow] = useState<"sensitive" | "noisy" | "robust" | "all">("sensitive");
  const [by, setBy] = useState<"area" | "exact">("area");
  const setJob = (j: JobSnapshot | null) => {
    lastJob = j;
    setJobState(j);
  };
  const running = job?.status === "running";
  const load = () => api.sensitivity().then(setReport).catch(onError);
  const follow = (id: string) =>
    watchJob(id, (j) => {
      setJob(j);
      if (j.status !== "running") void load();
    });

  useEffect(() => {
    void load();
    if (lastJob?.status === "running") return follow(lastJob.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project, result]);

  const start = async () => {
    try {
      const j = await api.startSensitivity({
        runs: mode === "baseline" ? 0 : runs,
        noise_runs: mode === "perturbed" ? 0 : runs,
        perturbation: perturbation / 100,
        seed,
        iterations,
        restarts,
      });
      setJob(j);
      follow(j.id);
    } catch (e) {
      onError(e);
    }
  };

  const names = new Map(project.layout?.compartments.map((c) => [c.id, c.name]));
  const walls = new Map(project.layout?.walls.map((w) => [w.id, w.name]));
  const area = (text: string) => {
    const [wall, level] = text.split(" · ");
    return `${walls.get(wall) ?? wall} · ${level}`;
  };
  const items = (report.items ?? [])
    .filter((i) => (show === "all" ? i.label !== "unused" && i.label !== "pinned" : i.label === show))
    .sort((a, b) => (a.perturbed ?? a.noise)!.area - (b.perturbed ?? b.noise)!.area);
  const counts = report.counts ?? {};
  const matter = (counts.robust ?? 0) + (counts.noisy ?? 0) + (counts.sensitive ?? 0);
  const noise = report.noise ?? null;
  const perturbed = report.perturbed ?? null;

  return (
    <>
      <section className="card stack">
        <h2>How much do the results depend on your frequency estimates?</h2>
        <p className="muted">
          Two kinds of re-optimization are compared with the baseline layout. <strong>Noise</strong> runs keep your
          frequencies and only change the random seed: whatever moves there moves because the optimizer finds the
          alternatives equally good. <strong>Perturbed</strong> runs also multiply every routine frequency by a random
          factor. An item that is stable under noise but moves under perturbation really depends on your estimates.
        </p>
        <div className="row top">
          <label className="field">
            Compare
            <div className="seg">
              {MODES.map(([id, label]) => (
                <button key={id} className={mode === id ? "active" : ""} onClick={() => setMode(id)} disabled={running}>{label}</button>
              ))}
            </div>
          </label>
          <label className="field">Runs of each kind<input type="number" min={1} max={500} value={runs} onChange={(e) => setRuns(Number(e.target.value))} disabled={running} /></label>
          <label className="field">Perturbation (± %)<input type="number" min={1} max={99} value={perturbation} onChange={(e) => setPerturbation(Number(e.target.value))} disabled={running || mode === "baseline"} /></label>
          <label className="field">Iterations per run<input type="number" min={0} step={50000} value={iterations} onChange={(e) => setIterations(Number(e.target.value))} disabled={running} /></label>
          <label className="field" title="Independent searches per run; the best one is kept. More restarts mean less noise.">Restarts<input type="number" min={1} max={100} value={restarts} onChange={(e) => setRestarts(Math.max(1, Number(e.target.value)))} disabled={running} style={{ width: 80 }} /></label>
          <label className="field">Seed<input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} disabled={running} style={{ width: 80 }} /></label>
          <span className="spacer" />
          <div style={{ alignSelf: "flex-end" }}>
            {running ? (
              <button className="danger" onClick={() => job && api.stop(job.id).catch(onError)}>Stop</button>
            ) : (
              <button className="primary" onClick={start}>Run the analysis</button>
            )}
          </div>
        </div>
        {running && (
          <div className="row">
            <div className="bar progress grow"><span style={{ width: `${(job?.progress.fraction ?? 0) * 100}%` }} /></div>
            <span className="small muted">{job?.progress.done ?? 0} of {job?.progress.total ?? "?"} runs</span>
          </div>
        )}
        {job?.error && <div className="banner error">{job.error}</div>}
        {!result.present && (
          <p className="muted small">No optimized result yet: the analysis will compute its own baseline first.</p>
        )}
      </section>

      {report.present && (
        <>
          <div className="tiles">
            {noise && <SetTile title="Same frequencies, new seed (noise)" sub={`cost ${noise.summary.min?.toFixed(3)} to ${noise.summary.max?.toFixed(3)}`} set={noise} />}
            {perturbed && <SetTile title={`Frequencies ±${Math.round((report.perturbation ?? 0) * 100)}%`} sub={`cost ${perturbed.summary.min?.toFixed(3)} to ${perturbed.summary.max?.toFixed(3)}`} set={perturbed} />}
            <div className="tile">
              <div className="label">Of {matter} items that affect the cost</div>
              <div className="value">{counts.robust ?? 0}<small>robust</small></div>
              <div className="sub">
                {counts.sensitive ?? 0} depend on the estimates · {counts.noisy ?? 0} move even with the same frequencies
              </div>
            </div>
          </div>
          {noise && perturbed && (
            <div className={`banner ${noise.area_stability < 0.8 ? "warning" : "ok"}`}>
              {noise.area_stability < 0.8 ? (
                <>
                  <strong>Most of the movement is optimizer noise.</strong> With unchanged frequencies only {pct(noise.area_stability)} of
                  the placements stay on the same wall and level, against {pct(perturbed.area_stability)} with perturbed ones. Raise the
                  iterations or the restarts before reading much into individual items.
                </>
              ) : (
                <>
                  <strong>The optimizer is stable at this budget</strong> ({pct(noise.area_stability)} of placements repeat with a new seed), so
                  the {pct(Math.max(0, noise.area_stability - perturbed.area_stability))} lost under perturbation is real sensitivity to the estimates.
                </>
              )}
            </div>
          )}
          <div className="cols-2">
            {noise && (
              <section className="card">
                <h2>Cost spread from the seed alone</h2>
                <Histogram values={noise.costs} reference={report.baseline_cost} referenceLabel="baseline" xLabel={`Weekly cost over ${noise.runs} runs with the same frequencies`} />
              </section>
            )}
            {perturbed && (
              <section className="card">
                <h2>Cost of the baseline layout under perturbed frequencies</h2>
                <Histogram values={report.baseline_costs ?? []} reference={report.baseline_cost} referenceLabel="baseline" xLabel={`Weekly cost of the baseline layout, frequencies ±${Math.round((report.perturbation ?? 0) * 100)}%`} />
              </section>
            )}
          </div>
          <section className="card stack">
            <div className="row">
              <h2 className="grow">Items</h2>
              <span className="muted small">Count as staying:</span>
              <div className="seg">
                <button className={by === "area" ? "active" : ""} onClick={() => setBy("area")}>Same wall and level</button>
                <button className={by === "exact" ? "active" : ""} onClick={() => setBy("exact")}>Exact compartment</button>
              </div>
              <div className="seg">
                {(["sensitive", "noisy", "robust", "all"] as const).map((s) => (
                  <button key={s} className={show === s ? "active" : ""} onClick={() => setShow(s)}>
                    {s[0].toUpperCase() + s.slice(1)}{s !== "all" ? ` (${counts[s] ?? 0})` : ""}
                  </button>
                ))}
              </div>
            </div>
            <div className="table-wrap scroll">
              <table>
                <thead>
                  <tr>
                    <th>Item</th><th>Status</th><th>Baseline location</th>
                    <th style={{ width: 150 }}>Stays (noise)</th><th style={{ width: 150 }}>Stays (perturbed)</th>
                    <th>Most common location</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((i) => {
                    const modal = (i.perturbed ?? i.noise)!;
                    return (
                      <tr key={i.item}>
                        <td>{i.name}</td>
                        <td><span className={`badge ${BADGE[i.label]}`}><i className="dot" /> {i.label}</span></td>
                        <td>{names.get(i.baseline_location) ?? i.baseline_location}<div className="muted small">{area(i.baseline_area)}</div></td>
                        <td><ShareBar shares={i.noise} by={by} /></td>
                        <td><ShareBar shares={i.perturbed} by={by} /></td>
                        <td>{names.get(modal.modal_location) ?? modal.modal_location} <span className="muted small">{pct(modal.modal_share)}</span></td>
                      </tr>
                    );
                  })}
                  {!items.length && <tr><td colSpan={6} className="muted">Nothing in this group.</td></tr>}
                </tbody>
              </table>
            </div>
            <p className="muted small">
              Labels use "same wall and level", so swapping between neighbouring shelves does not count as instability.
              Robust: stays in at least {pct(report.robust_threshold)} of both kinds of run. Noisy: moves even with the same
              frequencies. Sensitive: stable under noise, moves when the frequencies change.
            </p>
          </section>
        </>
      )}
    </>
  );
}
