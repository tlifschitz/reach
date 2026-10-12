import { useEffect, useMemo, useRef, useState } from "react";
import { api, fmtMetric, type LocationRow, type Project, type Report, type Result, type Trace } from "../api";
import { Elevation, LevelLegend } from "../components/Elevation";
import { FloorPlan, positionAt } from "../components/FloorPlan";

interface Props {
  project: Project;
  result: Result;
  onProject: (p: Project) => void;
  onError: (e: unknown) => void;
  routine: string | null;
  setRoutine: (id: string | null) => void;
}

const SOURCE_LABEL: Record<string, string> = {
  result: "Optimized",
  current: "Current",
  greedy: "Greedy start",
};
const SPEED = 4; // the animation runs at 4x real time

export function PlanView({ project, result, onProject, onError, routine, setRoutine }: Props) {
  const layout = project.layout!;
  const sources = [result.present && "result", project.has_current && "current", "greedy"].filter(Boolean) as string[];
  const [source, setSource] = useState<string | null>(null);
  // Until the user picks, show the best layout available: optimized, else current, else greedy.
  const active = source && sources.includes(source) ? source : sources[0];
  const [report, setReport] = useState<Report | null>(null);
  const [wall, setWall] = useState<string | null>(layout.walls[0]?.id ?? null);
  const [selected, setSelected] = useState<string | null>(null);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [time, setTime] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const frame = useRef(0);

  useEffect(() => {
    api.evaluate(active).then(setReport).catch(onError);
  }, [active, project, result, onError]);

  useEffect(() => {
    setTime(null);
    setPlaying(false);
    if (!routine) {
      setTrace(null);
      return;
    }
    api.trace(routine, active).then(setTrace).catch(onError);
  }, [routine, active, project, result, onError]);

  const duration = trace?.events.length ? trace.events[trace.events.length - 1].t : 0;
  useEffect(() => {
    if (!playing || !trace) return;
    let last = performance.now();
    const tick = (now: number) => {
      const dt = ((now - last) / 1000) * SPEED;
      last = now;
      setTime((t) => {
        const next = (t ?? 0) + dt;
        if (next >= duration) {
          setPlaying(false);
          return duration;
        }
        return next;
      });
      frame.current = requestAnimationFrame(tick);
    };
    frame.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame.current);
  }, [playing, trace, duration]);

  const locations = useMemo(
    () => new Map<string, LocationRow>((report?.locations ?? []).map((l) => [l.id, l])),
    [report],
  );
  const now = trace && time !== null ? trace.events[positionAt(trace, time).index] : null;
  const compartment = layout.compartments.find((c) => c.id === selected);
  const row = selected ? locations.get(selected) : undefined;
  const items = new Map((project.items ?? []).map((i) => [i.id, i]));
  const blockers = layout.obstructions.filter((o) => selected && o.blocks.includes(selected));

  const pin = async (item: string, location: string | null) => {
    try {
      onProject(await api.pin(item, location));
    } catch (e) {
      onError(e);
    }
  };
  const play = () => {
    if (time === null || time >= duration) setTime(0);
    setPlaying(true);
  };

  return (
    <>
      <div className="row">
        <span className="muted">Showing</span>
        <div className="seg">
          {sources.map((s) => (
            <button key={s} className={active === s ? "active" : ""} onClick={() => setSource(s)}>
              {SOURCE_LABEL[s]}
            </button>
          ))}
        </div>
        {!result.present && <span className="muted small">Run the optimization to see its layout here.</span>}
        <span className="spacer" />
        <LevelLegend />
      </div>
      <div className="split">
        <section className="card stack">
          <div className="row">
            <h2 className="grow">Floor plan</h2>
            <label className="row small muted">
              Routine path
              <select value={routine ?? ""} onChange={(e) => setRoutine(e.target.value || null)}>
                <option value="">None</option>
                {project.routines?.map((r) => (
                  <option key={r.id} value={r.id}>{r.name}</option>
                ))}
              </select>
            </label>
            {trace && (
              <>
                <button onClick={playing ? () => setPlaying(false) : play}>{playing ? "Pause" : "Play"}</button>
                <button onClick={() => { setPlaying(false); setTime(null); }} disabled={time === null}>Show whole path</button>
              </>
            )}
          </div>
          <FloorPlan layout={layout} activeWall={wall} onWall={(id) => { setWall(id); setSelected(null); }} trace={trace} time={time} />
          <p className="muted small">
            Top view, used for walking distances. Compartments stack on the walls, so click a wall to see them from the front.
          </p>
        </section>
        <aside className="stack">
          {trace ? (
            <section className="card stack">
              <h2>{trace.name}</h2>
              <div className="row small">
                <span>{fmtMetric("distance", trace.metrics.distance)} m</span>
                <span>{trace.metrics.openings.toFixed(0)} openings</span>
                <span>{trace.metrics.time.toFixed(0)} s</span>
                {trace.metrics.clearings > 0 && <span>{trace.metrics.clearings} door clearings</span>}
              </div>
              {time !== null && (
                <div className="bar progress"><span style={{ width: `${duration ? (time / duration) * 100 : 0}%` }} /></div>
              )}
              {now && (now.kind === "clear" || now.kind === "restore") && (
                <span className="badge critical"><i className="dot" /> {now.kind === "clear" ? "Moving" : "Putting back"} {now.obstruction}</span>
              )}
              <ol className="steps scroll" style={{ maxHeight: 300 }}>
                {trace.steps.map((s, n) => (
                  <li key={n} className={now && now.step === n ? "now" : ""}>{s.replace(/_/g, " ")}</li>
                ))}
              </ol>
            </section>
          ) : (
            <section className="card">
              <h2>Routine path</h2>
              <p className="muted">Pick a routine to draw the walk it takes with this layout, then press Play.</p>
            </section>
          )}
        </aside>
      </div>

      {wall && (
        <div className="split">
          <section className="card stack">
            <div className="row">
              <h2 className="grow">Elevation: {layout.walls.find((w) => w.id === wall)?.name}</h2>
              <div className="seg">
                {layout.walls.map((w) => (
                  <button key={w.id} className={w.id === wall ? "active" : ""} onClick={() => { setWall(w.id); setSelected(null); }}>
                    {w.name}
                  </button>
                ))}
              </div>
            </div>
            <Elevation
              layout={layout}
              wallId={wall}
              locations={locations}
              selected={selected}
              onSelect={setSelected}
              highlight={now?.location ?? null}
            />
            <p className="muted small">
              Compartments at their real size and height, as you face the wall. The thin bar shows how full each one is.
            </p>
          </section>
          <aside className="card stack">
            {compartment ? (
              <>
                <div className="row">
                  <h2 className="grow">{compartment.name}</h2>
                  <span className={`level-chip level-${compartment.level}`}>L{compartment.level}</span>
                </div>
                <p className="muted small">
                  {compartment.type.replace("_", " ")} · {compartment.placement.bottom_cm}–{compartment.placement.top_cm} cm high
                  {compartment.level_override ? " · level set manually" : ""}
                  {compartment.tags.length ? ` · ${compartment.tags.join(", ")}` : ""}
                </p>
                {blockers.map((o) => (
                  <span key={o.id} className="badge critical"><i className="dot" /> Blocked while {o.name.toLowerCase()} is {o.blocks_when}</span>
                ))}
                <div>
                  <div className="row small muted">
                    <span className="grow">{(row?.volume ?? 0).toFixed(1)} of {compartment.capacity_l.toFixed(0)} l usable</span>
                    <span>{Math.round((row?.fill ?? 0) * 100)}%</span>
                  </div>
                  <div className={`bar${(row?.fill ?? 0) > 1 ? " over" : ""}`}><span style={{ width: `${Math.min(100, (row?.fill ?? 0) * 100)}%` }} /></div>
                </div>
                <div>
                  {row?.items.length ? (
                    row.items.map((it) => {
                      const info = items.get(it.id);
                      return (
                        <div key={it.id} className="item-row">
                          <span className="grow">
                            {it.name}
                            {it.quantity > 1 ? <span className="muted"> ×{it.quantity}</span> : null}
                          </span>
                          {info?.pinned ? (
                            <button className="mini" onClick={() => pin(it.id, null)} title="Let the optimizer move it">Unpin</button>
                          ) : (
                            <button className="mini" onClick={() => pin(it.id, compartment.id)} title="Keep it here when optimizing">Pin here</button>
                          )}
                        </div>
                      );
                    })
                  ) : (
                    <p className="muted">Empty in this layout.</p>
                  )}
                </div>
                {project.items?.some((i) => i.pin_source === "ui") && (
                  <button className="mini" onClick={async () => { try { onProject(await api.resetPins()); } catch (e) { onError(e); } }}>
                    Reset pins to items.csv
                  </button>
                )}
                <p className="muted small">After pinning or unpinning, run the optimization again.</p>
              </>
            ) : (
              <>
                <h2>Compartment</h2>
                <p className="muted">Click a compartment to see what it holds and to pin or unpin items.</p>
              </>
            )}
          </aside>
        </div>
      )}
    </>
  );
}
