import { useState } from "react";
import { fmtMetric, fmtPct, METRIC_LABEL, METRIC_UNIT, type Metric, type Project, type Result } from "../api";

interface Props {
  project: Project;
  result: Result;
  goOptimize: () => void;
  showRoutine: (id: string) => void;
}

const SHOWN: Metric[] = ["time", "distance", "openings", "effort", "clearings"];

function Delta({ change }: { change: number | null }) {
  if (change === null) return <span className="muted">–</span>;
  const cls = change < -0.005 ? "better" : change > 0.005 ? "worse" : "";
  return <span className={`delta ${cls}`}>{fmtPct(change)}</span>;
}

export function ResultsView({ project, result, goOptimize, showRoutine }: Props) {
  const [choice, setVersus] = useState<"current" | "greedy" | null>(null);
  // Until the user picks, compare with the current layout whenever the project has one.
  const versus = choice ?? (result.vs_current ? "current" : "greedy");
  const [filter, setFilter] = useState("");
  if (!result.present || !result.evaluation || !result.locations) {
    return (
      <div className="card empty stack">
        <p>No result yet.</p>
        <div><button className="primary" onClick={goOptimize}>Go to Optimize</button></div>
      </div>
    );
  }
  const cmp = versus === "current" && result.vs_current ? result.vs_current : result.vs_greedy!;
  const label = versus === "current" && result.vs_current ? "current layout" : "greedy start";
  const extraTrips = result.evaluation.totals.extra_trips > 0;
  const metrics = extraTrips ? [...SHOWN, "extra_trips" as Metric] : SHOWN;
  const needle = filter.trim().toLowerCase();
  const locations = result.locations
    .filter((l) => l.items.length > 0)
    .filter((l) => !needle || l.name.toLowerCase().includes(needle) || l.items.some((i) => i.name.toLowerCase().includes(needle)));
  const pinnedUi = new Set(project.items?.filter((i) => i.pin_source === "ui").map((i) => i.id));

  return (
    <>
      <div className="row">
        <span className="muted">Compare with</span>
        <div className="seg">
          {result.vs_current && (
            <button className={versus === "current" ? "active" : ""} onClick={() => setVersus("current")}>Current layout</button>
          )}
          <button className={versus === "greedy" ? "active" : ""} onClick={() => setVersus("greedy")}>Greedy start</button>
        </div>
        <span className="spacer" />
        <a className="button" href="/api/export/assignment.csv" download="assignment.csv">Export assignment (CSV)</a>
      </div>
      {result.stale && (
        <div className="banner warning">The project changed after this result was computed. It is still valid; optimize again to improve it.</div>
      )}
      {result.violations && result.violations.length > 0 && (
        <div className="banner error">
          This result breaks constraints: {result.violations.map((v) => v.message).join("; ")}
        </div>
      )}

      <div className="tiles">
        <div className="tile">
          <div className="label">Weekly cost</div>
          <div className="value">{cmp.cost_after.toFixed(3)}</div>
          <div className="sub"><Delta change={cmp.cost_change} /> vs {label} ({cmp.cost_before.toFixed(3)})</div>
        </div>
        {metrics.map((m) => (
          <div className="tile" key={m}>
            <div className="label">{METRIC_LABEL[m]} per week</div>
            <div className="value">
              {fmtMetric(m, cmp.totals[m].after, m === "openings" || m === "clearings" ? 0 : 1)}
              <small>{METRIC_UNIT[m]}</small>
            </div>
            <div className="sub">
              <Delta change={cmp.totals[m].change} /> from {fmtMetric(m, cmp.totals[m].before, m === "openings" || m === "clearings" ? 0 : 1)}
            </div>
          </div>
        ))}
      </div>
      {versus === "current" && result.current_violations && result.current_violations.length > 0 && (
        <div className="banner warning">
          <strong>Your current layout breaks {result.current_violations.length} of your own rules:</strong>
          <ul>{result.current_violations.map((v) => <li key={v.message}>{v.message}</li>)}</ul>
        </div>
      )}

      <section className="card">
        <h2>Routines, per week</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Routine</th>
                <th className="num">Times</th>
                {metrics.map((m) => (
                  <th className="num" key={m}>{METRIC_LABEL[m]}{METRIC_UNIT[m] ? ` (${METRIC_UNIT[m]})` : ""}</th>
                ))}
                <th className="num">Cost</th>
                <th className="num">vs {label}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {cmp.routines.map((r) => (
                <tr key={r.id}>
                  <td>{r.name}</td>
                  <td className="num">{+r.frequency.toFixed(2)}</td>
                  {metrics.map((m) => (
                    <td className="num" key={m} title={`${label}: ${fmtMetric(m, r.before[m])}`}>{fmtMetric(m, r.after[m])}</td>
                  ))}
                  <td className="num">{r.cost_after.toFixed(3)}</td>
                  <td className="num"><Delta change={r.cost_change} /></td>
                  <td><button className="link" onClick={() => showRoutine(r.id)}>Show path</button></td>
                </tr>
              ))}
              {cmp.occasional.items > 0 && (
                <tr>
                  <td title="Items that no routine uses: a round trip from the central point to their compartment, times their estimated weekly use">
                    Occasional items <span className="muted">({cmp.occasional.items} not in any routine)</span>
                  </td>
                  <td />
                  {metrics.map((m) => (
                    <td className="num" key={m} title={`${label}: ${fmtMetric(m, cmp.occasional.before[m])}`}>{fmtMetric(m, cmp.occasional.after[m])}</td>
                  ))}
                  <td className="num">{cmp.occasional.cost_after.toFixed(3)}</td>
                  <td className="num"><Delta change={cmp.occasional.cost_change} /></td>
                  <td />
                </tr>
              )}
              <tr className="total">
                <td>Weekly total</td>
                <td />
                {metrics.map((m) => <td className="num" key={m}>{fmtMetric(m, cmp.totals[m].after)}</td>)}
                <td className="num">{cmp.cost_after.toFixed(3)}</td>
                <td className="num"><Delta change={cmp.cost_change} /></td>
                <td />
              </tr>
            </tbody>
          </table>
        </div>
        <p className="muted small" style={{ marginTop: 8 }}>
          Hover a number to see the value under the {label}.
          {cmp.occasional.items > 0 ? ` Occasional items are ${Math.round(cmp.occasional.share * 100)}% of the weekly cost.` : ""} Cost is the weighted sum of the normalized metrics
          {result.evaluation.soft_total >= 0.0005 ? `, plus ${result.evaluation.soft_total.toFixed(3)} of soft-constraint penalties` : ""}.
        </p>
      </section>

      <section className="card stack">
        <div className="row">
          <h2 className="grow">What goes where</h2>
          <span className="muted small">{cmp.moved.length} items move compared with the {label}</span>
          <input placeholder="Filter by item or compartment" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Location</th><th>Level</th><th style={{ width: 150 }}>Capacity used</th><th>Items</th></tr>
            </thead>
            <tbody>
              {locations.map((l) => (
                <tr key={l.id}>
                  <td>{l.name}</td>
                  <td>{l.assignable ? <span className={`level-chip level-${l.level}`}>L{l.level}</span> : <span className="muted small">fixed</span>}</td>
                  <td>
                    {l.fill !== null ? (
                      <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
                        <div className={`bar grow${l.fill > 1 ? " over" : ""}`}><span style={{ width: `${Math.min(100, l.fill * 100)}%` }} /></div>
                        <span className="small" style={{ width: 36, textAlign: "right" }}>{Math.round(l.fill * 100)}%</span>
                      </div>
                    ) : (
                      <span className="muted small">–</span>
                    )}
                  </td>
                  <td>
                    <div className="chips">
                      {l.items.map((i) => (
                        <span key={i.id} className={`chip${i.pinned ? " pinned" : ""}`} title={i.pinned ? (pinnedUi.has(i.id) ? "Pinned in the app" : "Pinned in items.csv") : undefined}>
                          {i.name}{i.quantity > 1 ? ` ×${i.quantity}` : ""}
                        </span>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted small">Outlined items are pinned. Pin or unpin items from the floor plan, then optimize again.</p>
      </section>
    </>
  );
}
