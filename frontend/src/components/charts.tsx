import { useRef, useState } from "react";

interface LineChartProps {
  points: [number, number][];
  reference?: number;
  referenceLabel?: string;
  xLabel: string;
  yLabel: string;
  height?: number;
}

function niceTicks(min: number, max: number, count: number): number[] {
  if (max <= min) return [min];
  const raw = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) out.push(v);
  return out;
}

const compact = (v: number) =>
  Math.abs(v) >= 1e6 ? `${v / 1e6}M` : Math.abs(v) >= 1e3 ? `${v / 1e3}k` : `${+v.toFixed(3)}`;

/** A single-series line chart with a crosshair tooltip. The card title names the series. */
export function LineChart({ points, reference, referenceLabel, xLabel, yLabel, height = 220 }: LineChartProps) {
  const [hover, setHover] = useState<number | null>(null);
  const ref = useRef<SVGSVGElement>(null);
  const W = 640;
  const pad = { l: 52, r: 16, t: 12, b: 34 };
  if (points.length < 2) return <div className="empty">Waiting for the first progress update…</div>;
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  let y0 = Math.min(...ys, reference ?? Infinity);
  let y1 = Math.max(...ys, reference ?? -Infinity);
  const span = y1 - y0 || 1;
  y0 -= span * 0.08;
  y1 += span * 0.08;
  const sx = (x: number) => pad.l + ((x - x0) / (x1 - x0 || 1)) * (W - pad.l - pad.r);
  const sy = (y: number) => pad.t + (1 - (y - y0) / (y1 - y0)) * (height - pad.t - pad.b);
  const d = points.map((p, i) => `${i ? "L" : "M"}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join("");
  const onMove = (e: React.MouseEvent) => {
    const box = ref.current!.getBoundingClientRect();
    const x = x0 + (((e.clientX - box.left) / box.width) * W - pad.l) / (W - pad.l - pad.r) * (x1 - x0);
    let best = 0;
    points.forEach((p, i) => {
      if (Math.abs(p[0] - x) < Math.abs(points[best][0] - x)) best = i;
    });
    setHover(best);
  };
  const h = hover === null ? null : points[Math.min(hover, points.length - 1)];
  return (
    <div className="chart-wrap">
      <svg
        ref={ref}
        className="chart"
        viewBox={`0 0 ${W} ${height}`}
        width="100%"
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        role="img"
        aria-label={`${yLabel} by ${xLabel}`}
      >
        {niceTicks(y0, y1, 4).map((t) => (
          <g key={t}>
            <line className="gridline" x1={pad.l} x2={W - pad.r} y1={sy(t)} y2={sy(t)} />
            <text x={pad.l - 6} y={sy(t) + 4} textAnchor="end">{+t.toFixed(3)}</text>
          </g>
        ))}
        {niceTicks(x0, x1, 5).map((t) => (
          <text key={t} x={sx(t)} y={height - pad.b + 14} textAnchor="middle">{compact(t)}</text>
        ))}
        <line className="axis" x1={pad.l} x2={W - pad.r} y1={height - pad.b} y2={height - pad.b} />
        <text x={(W + pad.l) / 2} y={height - 4} textAnchor="middle">{xLabel}</text>
        {reference !== undefined && (
          <g>
            <line className="ref" x1={pad.l} x2={W - pad.r} y1={sy(reference)} y2={sy(reference)} />
            <text x={W - pad.r} y={sy(reference) - 4} textAnchor="end">{referenceLabel}</text>
          </g>
        )}
        <path className="line" d={d} />
        {h && (
          <g>
            <line className="crosshair" x1={sx(h[0])} x2={sx(h[0])} y1={pad.t} y2={height - pad.b} />
            <circle className="marker" cx={sx(h[0])} cy={sy(h[1])} r={4.5} />
          </g>
        )}
      </svg>
      {h && (
        <div className="tooltip" style={{ left: `${(sx(h[0]) / W) * 100}%`, top: `${(sy(h[1]) / height) * 100}%` }}>
          <strong>{h[1].toFixed(4)}</strong> {yLabel.toLowerCase()} · {xLabel.toLowerCase()} {Math.round(h[0]).toLocaleString()}
        </div>
      )}
    </div>
  );
}

interface HistogramProps {
  values: number[];
  reference?: number;
  referenceLabel?: string;
  xLabel: string;
  height?: number;
}

/** Distribution of a single measure. Bars sit on the baseline with a 2px gap. */
export function Histogram({ values, reference, referenceLabel, xLabel, height = 200 }: HistogramProps) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 640;
  const pad = { l: 36, r: 16, t: 16, b: 34 };
  if (!values.length) return <div className="empty">No runs yet.</div>;
  let lo = Math.min(...values, reference ?? Infinity);
  let hi = Math.max(...values, reference ?? -Infinity);
  if (hi - lo < 1e-9) {
    lo -= 0.01;
    hi += 0.01;
  }
  const margin = (hi - lo) * 0.05;
  lo -= margin;
  hi += margin;
  const n = Math.min(12, Math.max(4, Math.ceil(Math.sqrt(values.length)) + 1));
  const bins = Array.from({ length: n }, () => 0);
  values.forEach((v) => {
    bins[Math.min(n - 1, Math.floor(((v - lo) / (hi - lo)) * n))] += 1;
  });
  const top = Math.max(...bins);
  const bw = (W - pad.l - pad.r) / n;
  const sx = (v: number) => pad.l + ((v - lo) / (hi - lo)) * (W - pad.l - pad.r);
  const sy = (c: number) => pad.t + (1 - c / top) * (height - pad.t - pad.b);
  return (
    <div className="chart-wrap">
      <svg className="chart" viewBox={`0 0 ${W} ${height}`} width="100%" role="img" aria-label={`Distribution of ${xLabel}`}>
        {niceTicks(0, top, 3).filter(Number.isInteger).map((t) => (
          <g key={t}>
            <line className="gridline" x1={pad.l} x2={W - pad.r} y1={sy(t)} y2={sy(t)} />
            <text x={pad.l - 6} y={sy(t) + 4} textAnchor="end">{t}</text>
          </g>
        ))}
        {bins.map((c, i) => (
          <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
            <rect x={pad.l + i * bw} y={pad.t} width={bw} height={height - pad.t - pad.b} fill="transparent" />
            {c > 0 && (
              <path
                className="barmark"
                opacity={hover === null || hover === i ? 1 : 0.55}
                d={`M${pad.l + i * bw + 1},${height - pad.b} V${sy(c) + 4} q0,-4 4,-4 H${pad.l + (i + 1) * bw - 5} q4,0 4,4 V${height - pad.b} Z`}
              />
            )}
          </g>
        ))}
        <line className="axis" x1={pad.l} x2={W - pad.r} y1={height - pad.b} y2={height - pad.b} />
        {niceTicks(lo, hi, 5).map((t) => (
          <text key={t} x={sx(t)} y={height - pad.b + 14} textAnchor="middle">{+t.toFixed(3)}</text>
        ))}
        <text x={(W + pad.l) / 2} y={height - 4} textAnchor="middle">{xLabel}</text>
        {reference !== undefined && (
          <g>
            <line className="ref" x1={sx(reference)} x2={sx(reference)} y1={pad.t - 4} y2={height - pad.b} />
            <text x={sx(reference) + 4} y={pad.t + 2}>{referenceLabel}</text>
          </g>
        )}
      </svg>
      {hover !== null && (
        <div className="tooltip" style={{ left: `${((pad.l + (hover + 0.5) * bw) / W) * 100}%`, top: `${(sy(bins[hover]) / height) * 100}%` }}>
          <strong>{bins[hover]}</strong> run{bins[hover] === 1 ? "" : "s"} · {(lo + (hover * (hi - lo)) / n).toFixed(3)}–{(lo + ((hover + 1) * (hi - lo)) / n).toFixed(3)}
        </div>
      )}
    </div>
  );
}
