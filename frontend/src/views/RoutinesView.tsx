import { useEffect, useState } from "react";
import {
  api,
  ApiError,
  describeStep,
  routineToYaml,
  type ConstraintsProposal,
  type Coverage,
  type DiaryProposal,
  type Project,
  type RoutineProposal,
  type RoutinesProposal,
} from "../api";

interface Props {
  project: Project;
  onProject: (p: Project) => void;
  onError: (e: unknown) => void;
  showRoutine: (id: string) => void;
}

const SECTIONS = [
  ["list", "Routines"],
  ["generate", "From a description"],
  ["diary", "From a diary"],
  ["coverage", "Coverage"],
  ["constraints", "Constraints from comments"],
] as const;
type Section = (typeof SECTIONS)[number][0];

const NEW_ROUTINE = `- id: new_routine
  name: New routine
  frequency_per_week: 1
  start: entrance
  steps:
    - take: item_id
    - use: item_id at poi_or_zone
    - store: item_id
`;

function LlmNotice({ project }: { project: Project }) {
  if (project.llm.available) return null;
  return (
    <div className="banner warning">
      The assistant features need an Anthropic API key ({project.llm.reason}). Everything else works without one:
      set <code>ANTHROPIC_API_KEY</code> and restart the server to enable them.
    </div>
  );
}

/** An editable routine with validate / save. Used for existing routines and for proposals. */
function RoutineEditor(props: {
  initial: string;
  issues?: string[];
  saveLabel: string;
  onSaved: (p: Project) => void;
  onDelete?: () => void;
  extra?: React.ReactNode;
}) {
  const [text, setText] = useState(props.initial);
  const [issues, setIssues] = useState<string[]>(props.issues ?? []);
  const [status, setStatus] = useState<"" | "valid" | "saved">("");
  useEffect(() => {
    setText(props.initial);
    setIssues(props.issues ?? []);
    setStatus("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.initial]);

  const validate = async () => {
    try {
      const r = await api.validateRoutine(text);
      setIssues(r.issues);
      setStatus(r.ok ? "valid" : "");
    } catch (e) {
      setIssues([(e as ApiError).message]);
      setStatus("");
    }
  };
  const save = async () => {
    try {
      props.onSaved(await api.saveRoutine(text));
      setIssues([]);
      setStatus("saved");
    } catch (e) {
      const err = e as ApiError;
      setIssues(err.issues.length ? err.issues : [err.message]);
      setStatus("");
    }
  };
  return (
    <div className="stack">
      <textarea className="code" rows={Math.min(26, Math.max(8, text.split("\n").length + 1))} value={text} spellCheck={false} onChange={(e) => { setText(e.target.value); setStatus(""); }} />
      {issues.length > 0 && (
        <div className="banner warning">
          <ul style={{ margin: 0 }}>{issues.map((i) => <li key={i}>{i}</li>)}</ul>
        </div>
      )}
      <div className="row">
        <button onClick={validate}>Validate</button>
        <button className="primary" onClick={save}>{props.saveLabel}</button>
        {props.onDelete && <button className="danger" onClick={props.onDelete}>Delete</button>}
        {props.extra}
        {status === "valid" && <span className="badge good"><i className="dot" /> valid</span>}
        {status === "saved" && <span className="badge good"><i className="dot" /> saved</span>}
      </div>
    </div>
  );
}

function Proposals({ result, onProject }: { result: RoutinesProposal; onProject: (p: Project) => void }) {
  return (
    <div className="stack">
      {result.notes && <p className="muted">{result.notes}</p>}
      {result.missing_items.length > 0 && (
        <div className="banner warning">
          <strong>Suggested missing items</strong> (not in your catalog, so they were left out of the steps):
          <ul>{result.missing_items.map((m) => <li key={m.name}>{m.name}: {m.reason}</li>)}</ul>
        </div>
      )}
      {result.routines.length === 0 && <p className="muted">No routines were proposed.</p>}
      {result.routines.map((r: RoutineProposal) => (
        <section className="card stack" key={r.routine.id}>
          <h3>{r.routine.name} <span className="muted">· {r.routine.frequency_per_week} per week</span></h3>
          <RoutineEditor initial={r.yaml} issues={r.issues} saveLabel="Approve and add" onSaved={onProject} />
        </section>
      ))}
    </div>
  );
}

export function RoutinesView({ project, onProject, onError, showRoutine }: Props) {
  const [section, setSection] = useState<Section>("list");
  const routines = project.routines ?? [];
  const [selected, setSelected] = useState<string | null>(routines[0]?.id ?? null);
  const [busy, setBusy] = useState(false);
  const [description, setDescription] = useState("");
  const [url, setUrl] = useState("");
  const [generated, setGenerated] = useState<RoutinesProposal | null>(null);
  const [diaryText, setDiaryText] = useState("");
  const [diaryInfo, setDiaryInfo] = useState<string>("");
  const [diary, setDiary] = useState<DiaryProposal | null>(null);
  const [applied, setApplied] = useState<Set<string>>(new Set());
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [cover, setCover] = useState<RoutinesProposal | null>(null);
  const [constraints, setConstraints] = useState<ConstraintsProposal | null>(null);
  const [accepted, setAccepted] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (section === "coverage") api.coverage().then(setCoverage).catch(onError);
  }, [section, project, onError]);

  const llm = async <T,>(fn: () => Promise<T>, done: (value: T) => void) => {
    setBusy(true);
    try {
      done(await fn());
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };
  const current = selected === "__new__" ? null : routines.find((r) => r.id === selected);

  const readDiary = async (file: File | undefined) => {
    if (!file) return;
    const text = await file.text();
    setDiaryText(text);
    setDiary(null);
    try {
      const parsed = await api.parseDiary(text);
      const dated = parsed.entries.filter((e) => e.date).length;
      setDiaryInfo(`${parsed.days.length} days, ${dated} dated entries, ${parsed.problems.length} lines without a clear date.`);
    } catch (e) {
      onError(e);
    }
  };
  const applyFrequency = async (id: string, value: number) => {
    try {
      onProject(await api.setFrequencies({ [id]: value }));
      setApplied(new Set(applied).add(id));
    } catch (e) {
      onError(e);
    }
  };
  const acceptConstraint = async (kind: "hard" | "soft", c: Record<string, unknown>) => {
    try {
      onProject(await api.addConstraints({ hard: kind === "hard" ? [c] : [], soft: kind === "soft" ? [c] : [] }));
      setAccepted(new Set(accepted).add(String(c.id)));
    } catch (e) {
      onError(e);
    }
  };

  return (
    <>
      <div className="row">
        <div className="seg">
          {SECTIONS.map(([id, label]) => (
            <button key={id} className={section === id ? "active" : ""} onClick={() => setSection(id)}>{label}</button>
          ))}
        </div>
        <span className="spacer" />
        <a className="button" href="/api/files/routines.yaml" download="routines.yaml">Download routines.yaml</a>
      </div>

      {section === "list" && (
        <div className="split" style={{ gridTemplateColumns: "360px minmax(0, 1fr)" }}>
          <section className="card stack">
            <div className="row">
              <h2 className="grow">{routines.length} routines</h2>
              <button className="mini" onClick={() => setSelected("__new__")}>New</button>
            </div>
            <div className="scroll" style={{ maxHeight: 560 }}>
              <table>
                <tbody>
                  {routines.map((r) => (
                    <tr key={r.id} className={`clickable${selected === r.id ? " selected" : ""}`} onClick={() => setSelected(r.id)}>
                      <td>{r.name}<div className="muted small">{r.steps.length} steps</div></td>
                      <td className="num">{r.frequency_per_week}<span className="muted small"> /wk</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section className="card stack">
            {current ? (
              <>
                <h2>{current.name}</h2>
                <p className="muted small">{current.steps.map(describeStep).join(" → ")}</p>
                <RoutineEditor
                  initial={routineToYaml(current)}
                  saveLabel="Save changes"
                  onSaved={onProject}
                  onDelete={async () => {
                    try {
                      onProject(await api.deleteRoutine(current.id));
                      setSelected(null);
                    } catch (e) {
                      onError(e);
                    }
                  }}
                  extra={<button onClick={() => showRoutine(current.id)}>Show path on the floor plan</button>}
                />
              </>
            ) : selected === "__new__" ? (
              <>
                <h2>New routine</h2>
                <RoutineEditor initial={NEW_ROUTINE} saveLabel="Add routine" onSaved={(p) => { onProject(p); }} />
              </>
            ) : (
              <p className="muted">Select a routine to edit it.</p>
            )}
            <p className="muted small">
              Steps: <code>go_to: place</code>, <code>take: item</code>, <code>use: item at place</code>,{" "}
              <code>put_down: item at place</code>, <code>store: item</code>, <code>set_state: obstruction open|closed</code>.
              Saving re-validates the project; optimize again afterwards.
            </p>
          </section>
        </div>
      )}

      {section === "generate" && (
        <>
          <LlmNotice project={project} />
          <section className="card stack">
            <h2>Describe an activity or paste a recipe</h2>
            <textarea rows={6} placeholder="e.g. On Sundays I make pancakes: flour, eggs and milk in the big bowl, whisk, fry in the non-stick pan…" value={description} onChange={(e) => setDescription(e.target.value)} />
            <div className="row">
              <input className="grow" placeholder="…or a recipe URL (optional)" value={url} onChange={(e) => setUrl(e.target.value)} />
              <button className="primary" disabled={busy || !project.llm.available || (!description.trim() && !url.trim())} onClick={() => llm(() => api.llmRoutine(description, url), setGenerated)}>
                {busy ? "Thinking…" : generated ? "Regenerate" : "Draft routines"}
              </button>
            </div>
            <p className="muted small">
              The draft uses only ids from your catalog and is validated like a hand-written routine. Nothing is added until you approve it.
            </p>
          </section>
          {generated && <Proposals result={generated} onProject={onProject} />}
        </>
      )}

      {section === "diary" && (
        <>
          <LlmNotice project={project} />
          <section className="card stack">
            <h2>Import a usage diary</h2>
            <p className="muted">
              A plain-text note with one header per day in <code>DD/MM</code> format and, below it, what you did in the kitchen
              that day. The sample project ships a two-week example, <code>data/sample/diary.txt</code>.
            </p>
            <div className="row">
              <input type="file" accept=".txt,text/plain" onChange={(e) => readDiary(e.target.files?.[0])} />
              <span className="muted small">{diaryInfo}</span>
              <span className="spacer" />
              <button className="primary" disabled={busy || !diaryText || !project.llm.available} onClick={() => { setApplied(new Set()); void llm(() => api.llmDiary(diaryText), setDiary); }}>
                {busy ? "Interpreting…" : "Interpret the diary"}
              </button>
            </div>
          </section>
          {diary && (
            <>
              <section className="card stack">
                <h2>Activities over {diary.recorded_days} recorded days</h2>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr><th>Routine</th><th>Status</th><th className="num">Times</th><th className="num">Measured /wk</th><th className="num">Currently /wk</th><th /></tr>
                    </thead>
                    <tbody>
                      {diary.activities.map((a) => (
                        <tr key={a.routine_id} title={a.entries.join("\n")}>
                          <td>{a.name}</td>
                          <td><span className="badge">{a.existing ? "existing" : "new"}</span></td>
                          <td className="num">{a.occurrences}</td>
                          <td className="num">{a.frequency_per_week}</td>
                          <td className="num">{a.previous_frequency ?? "–"}</td>
                          <td className="right">
                            {a.existing ? (
                              applied.has(a.routine_id) ? (
                                <span className="badge good"><i className="dot" /> applied</span>
                              ) : (
                                <button className="mini" onClick={() => applyFrequency(a.routine_id, a.frequency_per_week)}>Use measured frequency</button>
                              )
                            ) : (
                              <span className="muted small">review below</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="muted small">Frequency = times ÷ recorded days × 7. Hover a row to see the diary entries behind it.</p>
              </section>
              {diary.unclear.length > 0 && (
                <section className="card">
                  <h2>Entries that were not used</h2>
                  <table>
                    <tbody>
                      {diary.unclear.map((u, n) => (
                        <tr key={n}><td className="num muted">line {u.line}</td><td>{u.text}</td><td className="muted">{u.reason}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </section>
              )}
              <Proposals
                result={{ routines: diary.activities.flatMap((a) => (a.proposal ? [a.proposal] : [])), missing_items: diary.missing_items, notes: "" }}
                onProject={onProject}
              />
            </>
          )}
        </>
      )}

      {section === "coverage" && coverage && (
        <>
          <div className="tiles">
            <div className="tile"><div className="label">Items used by a routine</div><div className="value">{coverage.covered}<small>of {coverage.total}</small></div></div>
            <div className="tile"><div className="label">Never used</div><div className="value">{coverage.unused_items.length}</div><div className="sub">the optimizer cannot place these well</div></div>
            <div className="tile"><div className="label">Routines to check</div><div className="value">{new Set(coverage.findings.filter((f) => f.severity === "warning").map((f) => f.routine)).size}</div></div>
            <div className="tile"><div className="label">Frequency mismatches</div><div className="value">{coverage.frequencies.filter((f) => f.flag === "underestimated" || f.flag === "overestimated").length}</div></div>
          </div>
          <section className="card stack">
            <div className="row">
              <h2 className="grow">Items that no routine uses</h2>
              <button className="primary" disabled={busy || !project.llm.available || !coverage.unused_items.length} onClick={() => llm(api.llmCover, setCover)} title={project.llm.available ? "" : project.llm.reason}>
                {busy ? "Thinking…" : "Propose routines for these"}
              </button>
            </div>
            <div className="chips">
              {coverage.unused_items.map((i) => <span className="chip" key={i.id} title={i.category}>{i.name}</span>)}
            </div>
            {!project.llm.available && <p className="muted small">The proposal button needs an API key: {project.llm.reason}.</p>}
          </section>
          {cover && <Proposals result={cover} onProject={onProject} />}
          <div className="cols-2">
            <section className="card">
              <h2>Suspicious routines</h2>
              {coverage.findings.length ? (
                <table>
                  <tbody>
                    {coverage.findings.map((f, n) => (
                      <tr key={n}>
                        <td><code>{f.routine}</code></td>
                        <td>{f.message}</td>
                        <td><span className={`badge ${f.severity === "warning" ? "warning" : ""}`}><i className="dot" /> {f.severity}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="muted">Nothing looks wrong.</p>
              )}
            </section>
            <section className="card">
              <h2>Estimated vs measured use</h2>
              <div className="scroll" style={{ maxHeight: 360 }}>
                <table>
                  <thead><tr><th>Item</th><th className="num">items.csv /wk</th><th className="num">From routines /wk</th><th /></tr></thead>
                  <tbody>
                    {coverage.frequencies.filter((f) => f.flag === "underestimated" || f.flag === "overestimated").map((f) => (
                      <tr key={f.item}>
                        <td>{f.name}</td>
                        <td className="num">{f.estimated}</td>
                        <td className="num">{+f.derived.toFixed(1)}</td>
                        <td className="muted small">{f.flag === "underestimated" ? "used more than estimated" : "used less than estimated"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="muted small" style={{ marginTop: 8 }}>The optimizer always uses the frequency from the routines.</p>
            </section>
          </div>
        </>
      )}

      {section === "constraints" && (
        <>
          <LlmNotice project={project} />
          <section className="card stack">
            <div className="row">
              <h2 className="grow">Constraints from the comments in items.csv</h2>
              <button className="primary" disabled={busy || !project.llm.available} onClick={() => { setAccepted(new Set()); void llm(api.llmConstraints, setConstraints); }}>
                {busy ? "Reading…" : constraints ? "Propose again" : "Propose constraints"}
              </button>
            </div>
            <p className="muted">
              {project.items?.filter((i) => i.comments).length} items have a comment. Proposals are shown here for review and
              are only added when you approve them one by one.
            </p>
          </section>
          {constraints && (
            <>
              {(["hard", "soft"] as const).flatMap((kind) =>
                constraints.constraints[kind].map((c) => (
                  <section className="card stack" key={String(c.id)}>
                    <div className="row">
                      <h3 className="grow"><code>{String(c.id)}</code> <span className="badge">{kind} · {String(c.type)}</span></h3>
                      {accepted.has(String(c.id)) ? (
                        <span className="badge good"><i className="dot" /> added</span>
                      ) : (
                        <button className="primary" onClick={() => acceptConstraint(kind, c)}>Approve and add</button>
                      )}
                    </div>
                    <p className="muted">{constraints.rationale[String(c.id)]}</p>
                    <pre className="yaml">{JSON.stringify(c, null, 2)}</pre>
                  </section>
                )),
              )}
              {constraints.rejected.length > 0 && (
                <section className="card">
                  <h2>Discarded proposals</h2>
                  <table>
                    <tbody>
                      {constraints.rejected.map((r, n) => (
                        <tr key={n}><td><code>{String(r.constraint.id)}</code></td><td>{r.problems.join("; ")}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </section>
              )}
              {constraints.constraints.hard.length + constraints.constraints.soft.length === 0 && (
                <p className="muted">No new constraints were proposed.</p>
              )}
            </>
          )}
        </>
      )}
    </>
  );
}
