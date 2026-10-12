import { useRef, useState } from "react";
import { api, type Project } from "../api";

interface Props {
  project: Project;
  onProject: (p: Project) => void;
  onError: (e: unknown) => void;
  goTo: () => void;
}

const EXPECTED = ["kitchen.yaml", "items.csv", "routines.yaml", "constraints.yaml", "current_assignment.csv"];

export function DataView({ project, onProject, onError, goTo }: Props) {
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const run = async (fn: () => Promise<Project>) => {
    setBusy(true);
    try {
      onProject(await fn());
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };
  const upload = (files: FileList | null) => {
    if (files?.length) void run(() => api.upload(Array.from(files)));
  };

  const issues = project.issues ?? [];
  const errors = issues.filter((i) => i.severity === "error");
  const layout = project.layout;

  return (
    <>
      <section className="card stack">
        <h2>Project files</h2>
        <div
          className="drop stack"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            upload(e.dataTransfer.files);
          }}
        >
          <p>
            Drop your files here: <code>{EXPECTED.join(", ")}</code>
          </p>
          <div className="row" style={{ justifyContent: "center" }}>
            <button onClick={() => input.current?.click()} disabled={busy}>Choose files…</button>
            <button className="primary" onClick={() => run(api.loadSample)} disabled={busy}>
              Use the sample kitchen
            </button>
            {project.loaded && (
              <button onClick={() => run(api.close)} disabled={busy}>Close project</button>
            )}
          </div>
          <input ref={input} type="file" multiple hidden accept=".yaml,.yml,.csv" onChange={(e) => upload(e.target.files)} />
          <p className="muted small">
            The constraints and the current assignment are optional. Nothing is written to disk by the server.
          </p>
        </div>
        {project.ignored_files && project.ignored_files.length > 0 && (
          <p className="muted small">Ignored (unrecognised name): {project.ignored_files.join(", ")}</p>
        )}
      </section>

      {project.loaded && (
        <>
          {errors.length > 0 ? (
            <div className="banner error">
              <strong>{errors.length} error{errors.length === 1 ? "" : "s"}.</strong> Fix them in the files and upload again.
            </div>
          ) : !project.feasible ? (
            <div className="banner error">
              <strong>The files are valid, but no assignment can satisfy the constraints and capacities:</strong>
              <ul>{project.infeasible?.map((line) => <li key={line}>{line}</li>)}</ul>
            </div>
          ) : (
            <div className="banner ok row">
              <span className="grow">
                <strong>Valid.</strong> {project.items?.length} items, {layout?.compartments.length} compartments on{" "}
                {layout?.walls.length} walls, {project.routines?.length} routines,{" "}
                {(project.constraints?.hard.length ?? 0) + (project.constraints?.soft.length ?? 0)} constraints,{" "}
                {layout?.obstructions.length} obstructions.
                {project.has_current ? " A current assignment is available for comparison." : ""}
              </span>
              <button className="primary" onClick={goTo}>Open the floor plan</button>
            </div>
          )}

          {issues.length > 0 && (
            <section className="card">
              <h2>Validation</h2>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Severity</th><th>File</th><th className="num">Line</th><th>Field</th><th>Problem</th></tr>
                  </thead>
                  <tbody>
                    {issues.map((i, n) => (
                      <tr key={n}>
                        <td>
                          <span className={`badge ${i.severity === "error" ? "critical" : "warning"}`}>
                            <i className="dot" /> {i.severity}
                          </span>
                        </td>
                        <td><code>{i.file}</code></td>
                        <td className="num">{i.line ?? ""}</td>
                        <td><code>{i.field ?? ""}</code></td>
                        <td>{i.message}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          <div className="cols-2">
            <section className="card">
              <h2>Loaded files</h2>
              <table>
                <tbody>
                  {project.files?.map((f) => (
                    <tr key={f}>
                      <td><code>{f}</code></td>
                      <td className="right"><a href={`/api/files/${f}`} download={f}>Download</a></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="muted small" style={{ marginTop: 8 }}>
                Routines and constraints you approve in the app change these copies. Download them to keep the changes.
              </p>
            </section>
            <section className="card">
              <h2>Constraints</h2>
              <div className="scroll" style={{ maxHeight: 260 }}>
                <table>
                  <tbody>
                    {[...(project.constraints?.hard ?? []), ...(project.constraints?.soft ?? [])].map((c) => (
                      <tr key={String(c.id)}>
                        <td><code>{String(c.id)}</code></td>
                        <td><span className="badge">{c.type === "together" ? "soft" : "hard"} · {String(c.type)}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        </>
      )}
    </>
  );
}
