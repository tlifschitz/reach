import type { Layout, LocationRow } from "../api";

interface Props {
  layout: Layout;
  wallId: string;
  locations: Map<string, LocationRow>;
  selected: string | null;
  onSelect: (id: string) => void;
  highlight?: string | null;
}

/** Front view of one wall: compartments at their real size and height, coloured by level. */
export function Elevation({ layout, wallId, locations, selected, onSelect, highlight }: Props) {
  const wall = layout.walls.find((w) => w.id === wallId);
  if (!wall) return null;
  const comps = layout.compartments.filter((c) => c.placement.wall === wallId);
  const fixtures = [...layout.pois, ...layout.zones].filter((f) => f.placement?.wall === wallId);
  const top = Math.max(240, ...comps.map((c) => c.placement.top_cm + 10));
  const pad = { l: 34, r: 8, t: 22, b: 20 };
  const W = wall.length + pad.l + pad.r;
  const H = top + pad.t + pad.b;
  const y = (cm: number) => pad.t + top - cm;
  const x = (cm: number) => pad.l + cm;
  const onWall = new Set(comps.map((c) => c.id));
  const overlays: { key: string; x: number; y: number; w: number; h: number; label: string }[] = [];
  layout.obstructions.forEach((o) => {
    if (o.wall_range?.wall === wallId) {
      overlays.push({
        key: o.id,
        x: x(o.wall_range.from_cm),
        y: y(top) + 2,
        w: o.wall_range.to_cm - o.wall_range.from_cm,
        h: top - 2,
        label: `${o.name} (${o.blocks_when})`,
      });
    }
    o.blocks
      .filter((id) => onWall.has(id) && !o.wall_range)
      .forEach((id) => {
        const p = comps.find((c) => c.id === id)!.placement;
        overlays.push({
          key: `${o.id}:${id}`,
          x: x(p.offset_cm),
          y: y(p.top_cm),
          w: p.width_cm,
          h: p.top_cm - p.bottom_cm,
          label: `${o.name} (${o.blocks_when})`,
        });
      });
  });
  const labelled = new Set<string>();

  return (
    <svg className="elev" viewBox={`0 0 ${W} ${H}`} width="100%" style={{ maxHeight: 520 }} role="img" aria-label={`Elevation of ${wall.name}`}>
      <defs>
        <pattern id="hatch-elev" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="8" stroke="var(--critical)" strokeWidth="2" opacity="0.5" />
        </pattern>
      </defs>
      {[0, 50, 100, 150, 200].map((cm) => (
        <g key={cm}>
          <line x1={pad.l} x2={W - pad.r} y1={y(cm)} y2={y(cm)} stroke="var(--grid)" strokeWidth={1} />
          <text x={pad.l - 5} y={y(cm) + 2.5} textAnchor="end">{cm}</text>
        </g>
      ))}
      <text x={pad.l - 5} y={pad.t - 8} textAnchor="end">cm</text>
      {fixtures.map((f) => {
        const p = f.placement!;
        return (
          <g key={f.id}>
            <rect className="fixture" x={x(p.offset_cm)} y={y(p.top_cm)} width={p.width_cm} height={p.top_cm - p.bottom_cm} rx={2}>
              <title>{f.name}</title>
            </rect>
            {p.top_cm - p.bottom_cm >= 14 && (
              <text x={x(p.offset_cm + p.width_cm / 2)} y={y((p.top_cm + p.bottom_cm) / 2) + 2.5} textAnchor="middle">{f.name}</text>
            )}
          </g>
        );
      })}
      {comps.map((c) => {
        const p = c.placement;
        const row = locations.get(c.id);
        const height = p.top_cm - p.bottom_cm;
        const fill = row?.fill ?? 0;
        const count = row?.items.length ?? 0;
        const cx = x(p.offset_cm + p.width_cm / 2);
        const isSel = selected === c.id || highlight === c.id;
        return (
          <g key={c.id} onClick={() => onSelect(c.id)} style={{ color: `var(--level-ink-${c.level})` }}>
            <rect
              className={`comp${isSel ? " selected" : ""}`}
              x={x(p.offset_cm)}
              y={y(p.top_cm)}
              width={p.width_cm}
              height={height}
              rx={3}
              fill={`var(--level-${c.level})`}
            >
              <title>
                {`${c.name} · level ${c.level}${c.level_override ? " (manual)" : ""} · ${count} item${count === 1 ? "" : "s"} · ${Math.round(fill * 100)}% of ${c.capacity_l.toFixed(0)} l`}
              </title>
            </rect>
            {height >= 14 && (
              <text className="comp-label" x={cx} y={y(p.top_cm) + 9} textAnchor="middle" style={{ fill: "currentColor", fontWeight: 600 }}>
                {count ? `${count} item${count === 1 ? "" : "s"}` : "empty"}
              </text>
            )}
            {height >= 26 && (
              <text className="comp-label" x={cx} y={y(p.top_cm) + 17} textAnchor="middle" style={{ fill: "currentColor" }}>
                {Math.round(fill * 100)}% full
              </text>
            )}
            <rect className="fillbar-bg" x={x(p.offset_cm) + 4} y={y(p.bottom_cm) - 6} width={p.width_cm - 8} height={3} rx={1.5} />
            <rect className="fillbar" x={x(p.offset_cm) + 4} y={y(p.bottom_cm) - 6} width={(p.width_cm - 8) * Math.min(1, fill)} height={3} rx={1.5} />
          </g>
        );
      })}
      {overlays.map((o) => {
        const first = !labelled.has(o.label);
        labelled.add(o.label);
        return (
          <g key={o.key}>
            <rect className="blocked" fill="url(#hatch-elev)" x={o.x} y={o.y} width={o.w} height={o.h} />
            {first && <text x={o.x + 2} y={pad.t - 8} style={{ fill: "var(--critical)", fontWeight: 600 }}>⊘ {o.label}</text>}
          </g>
        );
      })}
      <line className="floor" x1={pad.l} x2={W - pad.r} y1={y(0)} y2={y(0)} />
    </svg>
  );
}

export function LevelLegend() {
  const names = ["low (bend down)", "comfortable", "high (reach up)", "step stool"];
  return (
    <div className="legend">
      {names.map((n, i) => (
        <span key={n}>
          <i className={`level-chip level-${i}`}>L{i}</i> {n}
        </span>
      ))}
      <span>
        <svg width="16" height="14" aria-hidden>
          <rect width="16" height="14" fill="none" stroke="var(--critical)" strokeDasharray="4 3" />
          <line x1="2" y1="12" x2="14" y2="2" stroke="var(--critical)" strokeWidth="2" />
        </svg>
        blocked by an obstruction
      </span>
    </div>
  );
}
