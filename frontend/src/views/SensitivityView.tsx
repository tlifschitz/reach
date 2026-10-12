import { useEffect, useState } from "react";
import { api, watchJob, type JobSnapshot, type Project, type Result, type Sensitivity } from "../api";
import { Histogram } from "../components/charts";

interface Props {
  project: Project;
  result: Result;
  onError: (e: unknown) => void;
}

let lastJob: JobSnapshot | null = null;
const BADGE: Record<string, string> = { robust: "good", sensitive: "warning", unused: "", pinned: "" };

export function SensitivityView({ project, result, onError }: Props) {
  const [runs, setRuns] = useState(20);
  const [perturbation, setPerturbation] = useState(30);
  const [iterations, setIterations] = useState(50_000);
  const [seed, setSeed] = useState(0);
  const [job, setJobState] = useState<JobSnapshot | null>(lastJob);
  const [report, setReport] = useState<Sensitivity>({ present: false });
  const [show, setShow] = useState<"sensitive" | "robust" | "all">("sensitive");
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
      const j = await api.startSensitivity({ runs, perturbation: perturbation / 100, seed, iterations });
      setJob(j);
      follow(j.id);
    } catch (e) {
      onError(e);
    }
  };

  const names = new Map(project.layout?.compartments.map((c) => [c.id, c.name]));
  const items = (report.items ?? [])
    .filter((i) => (show === "all" ? true : i.label === show))
    .sort((a, b) => a.baseline_share - b.baseline_share);
  const counts = report.counts ?? {};
  const used = (counts.robust ?? 0) + (counts.sensitive ?? 0);

  return (
    <>
      <section className="card stack">
        <h2>How much do the results depend on your frequency estimates?</h2>
        <p className="muted">
          Each run multiplies every routine frequency by a random factor and optimizes again. Items that end up in the
          same place every time have a robust location.
        </p>
        <div className="row top">
          <label className="field">Runs<input type="number" min={1} max={500} value={runs} onChange={(e) => setRuns(Number(e.target.value))} disabled={running} /></label>
          <label className="field">Perturbation (± %)<input type="number" min={1} max={99} value={perturbation} onChange={(e) => setPerturbation(Number(e.target.value))} disabled={running} /></label>
          <label className="field">Iterations per run<input type="number" min={0} step={10000} value={iterations} onChange={(e) => setIterations(Number(e.target.value))} disabled={running} /></label>
          <label className="field">Seed<input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} disabled={running} /></label>
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
            <span className="small muted">{job?.progress.done ?? 0} of {job?.progress.total ?? runs} runs</span>
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
            <div className="tile">
              <div className="label">Robust locations</div>
              <div className="value">{counts.robust ?? 0}<small>of {used} used items</small></div>
              <div className="sub">same place in at least {Math.round((report.robust_threshold ?? 0.8) * 100)}% of runs</div>
            </div>
            <div className="tile">
              <div className="label">Depend on the estimates</div>
              <div className="value">{counts.sensitive ?? 0}</div>
              <div className="sub">worth a second look</div>
            </div>
            <div className="tile">
              <div className="label">Cost across runs</div>
              <div className="value">{report.cost_summary?.median?.toFixed(3) ?? "–"}<small>median</small></div>
              <div className="sub">{report.cost_summary?.min?.toFixed(3)} to {report.cost_summary?.max?.toFixed(3)}</div>
            </div>
            <div className="tile">
              <div className="label">Not in any routine</div>
              <div className="value">{counts.unused ?? 0}</div>
              <div className="sub">their location does not affect the cost</div>
            </div>
          </div>
          <div className="cols-2">
            <section className="card">
              <h2>Cost of the re-optimized layouts</h2>
              <Histogram values={report.costs ?? []} reference={report.baseline_cost} referenceLabel="baseline" xLabel={`Weekly cost over ${report.runs} runs (±${Math.round((report.perturbation ?? 0) * 100)}%)`} />
            </section>
            <section className="card">
              <h2>If you kept the baseline layout instead</h2>
              <Histogram values={report.baseline_costs ?? []} reference={report.baseline_cost} referenceLabel="baseline" xLabel="Weekly cost of the baseline layout under the same perturbations" />
            </section>
          </div>
          <section className="card stack">
            <div className="row">
              <h2 className="grow">Items</h2>
              <div className="seg">
                {(["sensitive", "robust", "all"] as const).map((s) => (
                  <button key={s} className={show === s ? "active" : ""} onClick={() => setShow(s)}>{s[0].toUpperCase() + s.slice(1)}</button>
                ))}
              </div>
            </div>
            <div className="table-wrap scroll">
              <table>
                <thead>
                  <tr><th>Item</th><th>Status</th><th>Baseline location</th><th style={{ width: 170 }}>Stays there</th><th>Most common location</th><th className="num">Share</th></tr>
                </thead>
                <tbody>
                  {items.map((i) => (
                    <tr key={i.item}>
                      <td>{i.name}</td>
                      <td><span className={`badge ${BADGE[i.label]}`}><i className="dot" /> {i.label}</span></td>
                      <td>{names.get(i.baseline_location) ?? i.baseline_location}</td>
                      <td>
                        <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
                          <div className="bar grow"><span style={{ width: `${i.baseline_share * 100}%` }} /></div>
                          <span className="small" style={{ width: 36, textAlign: "right" }}>{Math.round(i.baseline_share * 100)}%</span>
                        </div>
                      </td>
                      <td>{names.get(i.modal_location) ?? i.modal_location}</td>
                      <td className="num">{Math.round(i.modal_share * 100)}%</td>
                    </tr>
                  ))}
                  {!items.length && <tr><td colSpan={6} className="muted">Nothing in this group.</td></tr>}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </>
  );
}
