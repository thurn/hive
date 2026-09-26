import { useMemo, useState, type PointerEvent } from "react";
import {
  diagnosticAt,
  diagnosticKind,
  duration,
  label,
  money,
  ratio,
  when,
  type Detail,
  type Diagnostic,
} from "./data";
import { Panel } from "./ui";

export type Range = Readonly<{ since: string; until: string }> | null;
const palette = [
  "#c4b5fd",
  "#93c5fd",
  "#6ee7b7",
  "#f3c969",
  "#fda4af",
  "#d8b4fe",
  "#67e8f9",
  "#a6a4b8",
];
export function inRange(at: string, range: Range): boolean {
  return (
    !range ||
    (Date.parse(at) >= Date.parse(range.since) &&
      Date.parse(at) <= Date.parse(range.until))
  );
}
function mark(item: Diagnostic): string {
  const kind = diagnosticKind(item);
  return kind === "tool_error"
    ? "×"
    : kind === "slow_tool"
      ? "◷"
      : kind === "retry_loop"
        ? "↻"
        : kind === "api_error"
          ? "ϟ"
          : kind === "compaction"
            ? "↓"
            : kind === "cache_rewrite"
              ? "⟳"
              : "!";
}
export function Timeline({
  detail,
  range,
  onRange,
  onDiagnostic,
}: {
  detail: Detail;
  range: Range;
  onRange: (r: Range) => void;
  onDiagnostic: (d: Diagnostic) => void;
}) {
  const points = useMemo(
    () => [...detail.timeline].sort((a, b) => a.at.localeCompare(b.at)),
    [detail.timeline],
  );
  const now = Date.now();
  const dates = [
    ...points.map((p) => p.at),
    ...detail.intervals.flatMap((i) => [i.start, i.end]),
    ...detail.context_requests.map((p) => p.observed_at),
    ...detail.diagnostics.flatMap((d) => [
      diagnosticAt(d),
      d.finished_at,
      d.until,
    ]),
    ...detail.candidates.flatMap((c) => [
      c.submitted_at,
      c.promoted_at,
      ...c.attempts.flatMap((a) => [a.created_at, a.started_at, a.finished_at]),
    ]),
    detail.card.created,
    detail.card.closed,
    detail.card.last_activity,
  ]
    .flatMap((value) => (value ? [Date.parse(value)] : []))
    .filter(Number.isFinite);
  const start = dates.length
    ? dates.reduce((a, b) => Math.min(a, b))
    : now - 3600000;
  const observedEnd = dates.reduce((a, b) => Math.max(a, b), start + 1000);
  const end = Math.max(
    observedEnd,
    detail.card.closed || detail.card.state === "Finished" ? observedEnd : now,
  );
  const x = (at: string) =>
    55 +
    Math.max(0, Math.min(1, (Date.parse(at) - start) / (end - start))) * 890;
  const y = (amount: string) => 190 - ratio(amount, detail.amount_picos) * 155;
  let cumulative = 0n;
  const chart = points.map((p) => {
    const previous = cumulative;
    cumulative += BigInt(p.amount_picos ?? "0");
    return {
      ...p,
      total: cumulative.toString(),
      previous: previous.toString(),
    };
  });
  const roleNames = [...new Set(points.map((p) => p.role))];
  const color = (role: string) =>
    palette[roleNames.indexOf(role) % palette.length] ?? palette[0];
  const diagnostic = detail.diagnostics.filter(
    (d) =>
      Number.isFinite(Date.parse(diagnosticAt(d))) &&
      !["tool_call", "intentional_wait"].includes(diagnosticKind(d)),
  );
  const [drag, setDrag] = useState<number | null>(null),
    [hover, setHover] = useState<string>("");
  const date = (position: number) =>
    new Date(
      start +
        ((Math.max(55, Math.min(945, position)) - 55) / 890) * (end - start),
    ).toISOString();
  function position(event: PointerEvent<SVGSVGElement>): number {
    const bounds = event.currentTarget.getBoundingClientRect();
    return ((event.clientX - bounds.left) / bounds.width) * 1000;
  }
  function finish(event: PointerEvent<SVGSVGElement>) {
    if (drag !== null) {
      const finish = position(event);
      if (Math.abs(finish - drag) > 4)
        onRange({
          since: date(Math.min(drag, finish)),
          until: date(Math.max(drag, finish)),
        });
      setDrag(null);
    }
  }
  const waits = detail.diagnostics.filter(
    (d) => d.kind === "human_wait" && Number.isFinite(Date.parse(d.at ?? "")),
  );
  const ownership = detail.intervals.slice(0, 5);
  const height = 260 + ownership.length * 25;
  return (
    <Panel
      title="Spend over time"
      action={<span className="muted">Drag to select a range</span>}
    >
      <div className="timeline-wrap">
        <svg
          className="timeline"
          viewBox={`0 0 1000 ${height}`}
          role="img"
          aria-label="Cumulative spend, ownership intervals and diagnostics"
          onPointerDown={(e) => {
            if (e.pointerType === "touch") return;
            setDrag(position(e));
            e.currentTarget.setPointerCapture(e.pointerId);
          }}
          onPointerUp={finish}
          onPointerCancel={() => setDrag(null)}
        >
          {[0, 0.5, 1].map((fraction, index) => (
            <g key={fraction}>
              <line
                x1="55"
                x2="945"
                y1={190 - fraction * 155}
                y2={190 - fraction * 155}
                className="chart-grid"
              />
              <text
                x="48"
                y={194 - fraction * 155}
                textAnchor="end"
                className="axis-label"
              >
                {money(
                  (
                    (BigInt(detail.amount_picos) * BigInt(index)) /
                    2n
                  ).toString(),
                )}
              </text>
            </g>
          ))}
          {waits.map((w, index) => (
            <rect
              key={"wait" + index}
              x={x(w.at ?? "")}
              y="32"
              width={Math.max(
                2,
                x(w.until ?? new Date(end).toISOString()) - x(w.at ?? ""),
              )}
              height="160"
              className="wait-band"
            >
              <title>Human wait · {when(w.at)}</title>
            </rect>
          ))}
          {points.slice(1).map((p, index) => {
            const previous = points[index];
            return previous &&
              Date.parse(p.at) - Date.parse(previous.at) > 1800000 ? (
              <rect
                key={"idle" + p.response}
                x={x(previous.at)}
                y="32"
                width={x(p.at) - x(previous.at)}
                height="160"
                className="idle-band"
              >
                <title>
                  No requests observed for{" "}
                  {duration(Date.parse(p.at) - Date.parse(previous.at))}
                </title>
              </rect>
            ) : null;
          })}
          {range && (
            <rect
              x={x(range.since)}
              y="30"
              width={Math.max(2, x(range.until) - x(range.since))}
              height="180"
              className="selection-band"
            />
          )}
          {chart.map((p, index) => {
            const previous = chart[index - 1];
            return (
              <path
                key={p.response}
                d={`M${previous ? x(previous.at) : 55},${y(p.previous)} L${x(p.at)},${y(p.total)}`}
                fill="none"
                stroke={color(p.role)}
                strokeWidth="2.5"
              >
                <title>
                  {when(p.at)} · {label(p.role)} · {money(p.amount_picos, 6)}
                </title>
              </path>
            );
          })}
          {detail.context_requests.map((p) => (
            <circle
              key={p.response}
              cx={x(p.observed_at)}
              cy="202"
              r="3"
              className="context-point"
            >
              <title>
                Outside ownership — not in total · {when(p.observed_at)}
              </title>
            </circle>
          ))}
          {diagnostic.map((d, index) => (
            <g
              key={d.call_id ?? d.id ?? index}
              className="diagnostic-mark"
              role="button"
              tabIndex={0}
              aria-label={`${label(diagnosticKind(d))} at ${when(diagnosticAt(d))}`}
              onPointerDown={(e) => e.stopPropagation()}
              onClick={() => onDiagnostic(d)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onDiagnostic(d);
                }
              }}
              onMouseEnter={() =>
                setHover(
                  label(diagnosticKind(d)) + " · " + when(diagnosticAt(d)),
                )
              }
            >
              <text
                x={x(diagnosticAt(d))}
                y={220 + (index % 2) * 15}
                textAnchor="middle"
              >
                {mark(d)}
              </text>
              <title>
                {label(diagnosticKind(d))} · {when(diagnosticAt(d))}
              </title>
            </g>
          ))}
          {detail.candidates.flatMap((c) =>
            c.attempts.map((a) => (
              <g key={a.id}>
                <circle
                  cx={x(
                    a.finished_at ??
                      a.started_at ??
                      a.created_at ??
                      new Date(end).toISOString(),
                  )}
                  cy="205"
                  r="4"
                  fill={a.state.includes("fail") ? "#fda4af" : "#6ee7b7"}
                >
                  <title>
                    CI attempt {a.number} · {a.state}
                  </title>
                </circle>
                {a.started_at && (
                  <rect
                    x={x(a.started_at)}
                    y="32"
                    width={Math.max(
                      1,
                      x(a.finished_at ?? new Date(end).toISOString()) -
                        x(a.started_at),
                    )}
                    height="160"
                    className="ci-band"
                  >
                    <title>CI running</title>
                  </rect>
                )}
              </g>
            )),
          )}
          {ownership.map((interval, index) => {
            const role =
              detail.role_spans.find(
                (r) =>
                  r.thread === interval.thread &&
                  Date.parse(r.start) <= Date.parse(interval.start),
              )?.role ?? "Owner";
            const host =
              detail.diagnostics.find((d) => d.thread === interval.thread)
                ?.host ?? "session";
            return (
              <g key={interval.thread + interval.start}>
                <rect
                  x={x(interval.start)}
                  y={250 + index * 25}
                  width={Math.max(
                    2,
                    x(interval.end ?? new Date(end).toISOString()) -
                      x(interval.start),
                  )}
                  height="20"
                  rx="3"
                  className="ownership-band"
                />
                <text
                  x={Math.min(820, x(interval.start) + 5)}
                  y={265 + index * 25}
                  className="band-label"
                >
                  {label(role)} · {host} · {interval.thread.slice(0, 8)}
                </text>
              </g>
            );
          })}
          <text x="55" y={height - 4} className="axis-label">
            {when(new Date(start).toISOString())}
          </text>
          <text x="945" y={height - 4} textAnchor="end" className="axis-label">
            {when(new Date(end).toISOString())}
          </text>
        </svg>
      </div>
      <div className="chart-legend">
        {roleNames.map((role) => (
          <span key={role}>
            <svg width="10" height="10" aria-hidden="true">
              <circle cx="5" cy="5" r="4" fill={color(role)} />
            </svg>{" "}
            {label(role)}
          </span>
        ))}
        <span>× Error</span>
        <span>◷ Slow tool</span>
        <span>↻ Retry</span>
        {detail.context_requests.length > 0 && (
          <span>○ Outside ownership — not in total</span>
        )}
      </div>
      <div className="range-controls">
        <label>
          From
          <input
            aria-label="Range start"
            type="datetime-local"
            value={range ? localTime(range.since) : ""}
            onChange={(e) => {
              if (e.target.value)
                onRange({
                  since: new Date(e.target.value).toISOString(),
                  until: range?.until ?? new Date(end).toISOString(),
                });
            }}
          />
        </label>
        <label>
          To
          <input
            aria-label="Range end"
            type="datetime-local"
            value={range ? localTime(range.until) : ""}
            onChange={(e) => {
              if (e.target.value)
                onRange({
                  since: range?.since ?? new Date(start).toISOString(),
                  until: new Date(e.target.value).toISOString(),
                });
            }}
          />
        </label>
        <span className="muted">
          {range
            ? "Tables show the selected range."
            : hover || "Select a marker to inspect its source."}
        </span>
      </div>
      {ownership.length < detail.intervals.length && (
        <p className="muted">
          {detail.intervals.length - ownership.length} additional ownership
          intervals are listed below.
        </p>
      )}
    </Panel>
  );
}
function localTime(value: string): string {
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}
export function Breakdown({ detail }: { detail: Detail }) {
  const [dimension, setDimension] = useState("role");
  const slices = detail.breakdown[dimension] ?? [];
  let offset = 0;
  return (
    <Panel
      title="Where the spend went"
      action={
        <select
          aria-label="Breakdown dimension"
          value={dimension}
          onChange={(e) => setDimension(e.target.value)}
        >
          {Object.keys(detail.breakdown).map((key) => (
            <option value={key} key={key}>
              {key === "role" ? "Request role" : label(key)}
              {key === "tool" ? " · estimated" : ""}
            </option>
          ))}
        </select>
      }
    >
      <p className="muted">
        Lifetime amount ·{" "}
        {dimension === "tool"
          ? "Tool allocation is estimated; missing evidence remains Unallocated."
          : "Every segment is an exact share of the selected work."}
      </p>
      <svg
        className="breakdown-bar"
        viewBox="0 0 1000 20"
        role="img"
        aria-label={label(dimension) + " cost breakdown"}
      >
        {slices.map((slice, index) => {
          const width = ratio(slice.amount_picos, detail.amount_picos) * 1000;
          const start = offset;
          offset += width;
          return (
            <rect
              key={slice.label}
              x={start}
              width={width}
              height="20"
              fill={palette[index % palette.length]}
            >
              <title>
                {slice.label} · {money(slice.amount_picos, 6)}
              </title>
            </rect>
          );
        })}
      </svg>
      <div className="breakdown-list">
        {slices.map((slice) => (
          <div key={slice.label}>
            <span>{slice.label}</span>
            <span className="money">{money(slice.amount_picos, 4)}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}
