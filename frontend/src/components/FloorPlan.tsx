import type { Layout, Placement, Trace, Wall } from "../api";

const DEPTH = 60; // drawn depth of cabinets and counters, in cm

/** Point at `offset` cm along a wall and `depth` cm into the room. */
function wallPoint(wall: Wall, offset: number, depth: number): [number, number] {
  const dx = (wall.end[0] - wall.start[0]) / wall.length;
  const dy = (wall.end[1] - wall.start[1]) / wall.length;
  // Walls run left to right as seen from inside, so the room is on the right-hand side.
  return [wall.start[0] + dx * offset - dy * depth, wall.start[1] + dy * offset + dx * depth];
}

function footprint(wall: Wall, p: { offset_cm: number; width_cm: number }, depth = DEPTH): string {
  return [
    wallPoint(wall, p.offset_cm, 0),
    wallPoint(wall, p.offset_cm + p.width_cm, 0),
    wallPoint(wall, p.offset_cm + p.width_cm, depth),
    wallPoint(wall, p.offset_cm, depth),
  ]
    .map((c) => c.join(","))
    .join(" ");
}

/** Horizontal room, in cm, for a label on something `along` cm wide on this wall. */
function span(wall: Wall, along: number, depth = DEPTH): number {
  const dx = Math.abs(wall.end[0] - wall.start[0]) / wall.length;
  return dx * along + (1 - dx) * depth;
}

/** A label broken into short lines so it fits a footprint about `width` cm wide. */
function Label({ x, y, text, width, strong }: { x: number; y: number; text: string; width: number; strong?: boolean }) {
  const max = Math.max(6, Math.floor(width / 4.6));
  const lines: string[] = [];
  text.split(" ").forEach((word) => {
    const last = lines[lines.length - 1];
    if (last !== undefined && `${last} ${word}`.length <= max) lines[lines.length - 1] = `${last} ${word}`;
    else lines.push(word);
  });
  return (
    <text className={strong ? "name" : undefined} x={x} y={y - (lines.length - 1) * 4.5 + 3} textAnchor="middle" pointerEvents="none">
      {lines.map((line, n) => (
        <tspan key={n} x={x} dy={n ? 9 : 0}>{line}</tspan>
      ))}
    </text>
  );
}

/** Where the actor is at time `t`, interpolating between trace events. */
export function positionAt(trace: Trace, t: number): { x: number; y: number; index: number } {
  const ev = trace.events;
  if (!ev.length) return { x: 0, y: 0, index: 0 };
  let i = 0;
  while (i < ev.length - 1 && ev[i + 1].t <= t) i += 1;
  const a = ev[i];
  const b = ev[Math.min(i + 1, ev.length - 1)];
  if (b.t <= a.t || t <= a.t) return { x: a.x, y: a.y, index: i };
  const f = Math.min(1, (t - a.t) / (b.t - a.t));
  return { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f, index: i };
}

interface Props {
  layout: Layout;
  activeWall: string | null;
  onWall: (id: string) => void;
  trace: Trace | null;
  time: number | null;
}

export function FloorPlan({ layout, activeWall, onWall, trace, time }: Props) {
  const walls = new Map(layout.walls.map((w) => [w.id, w]));
  const pts = [
    ...layout.outline,
    ...layout.walls.flatMap((w) => [w.start, w.end]),
    ...layout.pois.map((p) => p.position),
    ...layout.zones.map((z) => z.position),
  ];
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  const pad = 44;
  const minX = Math.min(...xs) - pad;
  const minY = Math.min(...ys) - pad;
  const w = Math.max(...xs) - minX + pad;
  const h = Math.max(...ys) - minY + pad;

  const placed = (p: Placement | null) => (p ? walls.get(p.wall) : undefined);
  const runs = new Map<string, { wall: Wall; offset_cm: number; width_cm: number }>();
  layout.compartments.forEach((c) => {
    const wall = walls.get(c.placement.wall);
    if (wall) {
      const { offset_cm, width_cm } = c.placement;
      runs.set(`${wall.id}:${offset_cm}:${width_cm}`, { wall, offset_cm, width_cm });
    }
  });
  const spans = new Map<string, Placement>();
  [...layout.compartments, ...layout.zones, ...layout.pois].forEach((o) => {
    if (o.placement) spans.set(o.id, o.placement);
  });
  const blocked: { key: string; points: string; label: string }[] = [];
  layout.obstructions.forEach((o) => {
    if (o.wall_range && walls.has(o.wall_range.wall)) {
      const wall = walls.get(o.wall_range.wall)!;
      const r = { offset_cm: o.wall_range.from_cm, width_cm: o.wall_range.to_cm - o.wall_range.from_cm };
      blocked.push({ key: o.id, points: footprint(wall, r, DEPTH + 8), label: o.name });
    } else {
      const seen = new Set<string>();
      o.blocks.forEach((id) => {
        const p = spans.get(id);
        const wall = p && walls.get(p.wall);
        if (!p || !wall || seen.has(`${p.wall}:${p.offset_cm}`)) return;
        seen.add(`${p.wall}:${p.offset_cm}`);
        blocked.push({ key: `${o.id}:${id}`, points: footprint(wall, p, DEPTH + 8), label: o.name });
      });
    }
  });

  const pathPoints = trace ? trace.events.map((e) => `${e.x},${e.y}`).join(" ") : "";
  const now = trace && time !== null ? positionAt(trace, time) : null;
  const donePoints =
    trace && now
      ? [...trace.events.slice(0, now.index + 1).map((e) => `${e.x},${e.y}`), `${now.x},${now.y}`].join(" ")
      : "";
  const stops = trace
    ? Array.from(new Map(trace.events.filter((e) => e.kind !== "move").map((e) => [`${e.x},${e.y}`, e])).values())
    : [];

  return (
    <svg className="plan" viewBox={`${minX} ${minY} ${w} ${h}`} width="100%" style={{ maxHeight: 560 }} role="img" aria-label="Floor plan, top view">
      <defs>
        <pattern id="hatch-plan" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="8" stroke="var(--critical)" strokeWidth="2" opacity="0.55" />
        </pattern>
      </defs>
      {layout.outline.length > 2 && <polygon className="room" points={layout.outline.map((p) => p.join(",")).join(" ")} />}
      {[...runs.values()].map((r) => (
        <polygon key={`${r.wall.id}${r.offset_cm}`} className="cabinet" points={footprint(r.wall, r)} />
      ))}
      {layout.zones.map((z) => {
        const wall = placed(z.placement);
        if (wall && z.placement) {
          // Label the widest stretch of the zone that no fixture sits on.
          const from = z.placement.offset_cm;
          const to = from + z.placement.width_cm;
          const covered = layout.pois
            .map((o) => o.placement)
            .filter((o): o is Placement => !!o && o.wall === wall.id && o.width_cm >= 20)
            .map((o) => [Math.max(from, o.offset_cm), Math.min(to, o.offset_cm + o.width_cm)])
            .filter(([a, b]) => b > a)
            .sort((a, b) => a[0] - b[0]);
          let best: [number, number] = [from, from];
          let cursor = from;
          [...covered, [to, to]].forEach(([a, b]) => {
            if (a - cursor > best[1] - best[0]) best = [cursor, a];
            cursor = Math.max(cursor, b);
          });
          const free = best[1] - best[0];
          const [cx, cy] = wallPoint(wall, (best[0] + best[1]) / 2, free === to - from ? DEPTH / 2 : DEPTH - 16);
          return (
            <g key={z.id}>
              <polygon className="counter" points={footprint(wall, z.placement)}><title>{z.name}</title></polygon>
              {free >= 24 && <Label x={cx} y={cy} text={z.name} width={span(wall, free)} strong />}
            </g>
          );
        }
        const [sw, sh] = z.size ?? [50, 50];
        return (
          <g key={z.id}>
            <rect className="counter" x={z.position[0] - sw / 2} y={z.position[1] - sh / 2} width={sw} height={sh} rx={6}>
              <title>{z.name}</title>
            </rect>
            <Label x={z.position[0]} y={z.position[1]} text={z.name} width={sw} strong />
          </g>
        );
      })}
      {layout.pois.map((p) => {
        const wall = placed(p.placement);
        if (wall && p.placement) {
          // Small fixtures such as outlets get a marker only; the tooltip names them.
          if (p.placement.width_cm < 20) {
            const [mx, my] = wallPoint(wall, p.placement.offset_cm + p.placement.width_cm / 2, 5);
            return <rect key={p.id} className="poi" x={mx - 3} y={my - 3} width={6} height={6}><title>{p.name}</title></rect>;
          }
          // Fixtures stacked in the same column (stove over oven) share one outline and label.
          const same = layout.pois.filter(
            (o) => o.placement && o.placement.wall === p.placement!.wall && o.placement.offset_cm === p.placement!.offset_cm && o.placement.width_cm >= 20,
          );
          if (same[0].id !== p.id) return null;
          const [cx, cy] = wallPoint(wall, p.placement.offset_cm + p.placement.width_cm / 2, 18);
          const name = same.map((o) => o.name).join(" / ");
          return (
            <g key={p.id}>
              <polygon className="poi" points={footprint(wall, { offset_cm: p.placement.offset_cm + 3, width_cm: p.placement.width_cm - 6 }, 36)}><title>{name}</title></polygon>
              <Label x={cx} y={cy} text={name} width={span(wall, p.placement.width_cm - 6, 36)} />
            </g>
          );
        }
        return (
          <g key={p.id}>
            <circle className="poi" cx={p.position[0]} cy={p.position[1]} r={7}><title>{p.name}</title></circle>
            <Label x={p.position[0]} y={p.position[1] + 17} text={p.name} width={70} />
          </g>
        );
      })}
      {blocked.map((b) => (
        <polygon key={b.key} className="blocked" fill="url(#hatch-plan)" points={b.points}><title>Blocked by: {b.label}</title></polygon>
      ))}
      {layout.walls.map((wall) => {
        const [lx, ly] = wallPoint(wall, wall.length / 2, -16);
        let angle = (Math.atan2(wall.end[1] - wall.start[1], wall.end[0] - wall.start[0]) * 180) / Math.PI;
        if (angle > 90 || angle < -90) angle += 180;
        return (
          <g key={wall.id} onClick={() => onWall(wall.id)}>
            <line className="wall-hit" x1={wall.start[0]} y1={wall.start[1]} x2={wall.end[0]} y2={wall.end[1]}>
              <title>Open the elevation of {wall.name}</title>
            </line>
            <line className={`wall${activeWall === wall.id ? " active" : ""}`} x1={wall.start[0]} y1={wall.start[1]} x2={wall.end[0]} y2={wall.end[1]} />
            <text x={lx} y={ly + 4} textAnchor="middle" transform={`rotate(${angle} ${lx} ${ly})`} style={{ fontWeight: 600, cursor: "pointer" }}>{wall.name} ›</text>
          </g>
        );
      })}
      {trace && (
        <g>
          <polyline className="path" points={pathPoints} opacity={now ? 0.3 : 0.9} />
          {now && <polyline className="path-done" points={donePoints} />}
          {stops.map((e) => (
            <circle key={`${e.x},${e.y}`} className="stop" cx={e.x} cy={e.y} r={4.5}><title>{e.location ?? ""}</title></circle>
          ))}
          {now && <circle className="actor" cx={now.x} cy={now.y} r={8} />}
        </g>
      )}
    </svg>
  );
}
