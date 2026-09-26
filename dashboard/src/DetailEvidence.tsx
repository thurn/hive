import { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import {
  RecordSchema,
  RequestsSchema,
  duration,
  label,
  text,
  when,
  type Beads,
  type Candidate,
  type Requests,
} from "./data";
import { detailApiQuery } from "./detailState";
import { request } from "./network";
import { candidateDuration } from "./DetailOverview";
import { inRange, type Range } from "./Timeline";
import { Amount, Copy, Panel } from "./ui";
export function SafeMarkdown({ value }: { value: string }) {
  return (
    <div className="markdown">
      <Markdown
        skipHtml
        urlTransform={(url) => (/^(https?:|mailto:)/i.test(url) ? url : "")}
        components={{
          a: ({ href, children }) =>
            href ? (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            ) : (
              <span>{children}</span>
            ),
          img: ({ alt }) => <span>{alt}</span>,
        }}
      >
        {value}
      </Markdown>
    </div>
  );
}
export function BeadsPanel({ beads }: { beads: Beads }) {
  const bead = beads.bead;
  const metadata = RecordSchema.safeParse(bead.metadata);
  return (
    <Panel
      title="Beads · read-only"
      action={
        <span className="muted">
          {beads.source === "live" ? "Live" : "Cached"} ·{" "}
          {when(beads.refreshed)}
        </span>
      }
    >
      {beads.error && <p role="alert">{beads.error}</p>}
      {beads.source !== "live" && (
        <p className="warning">
          Live Beads data is unavailable. Showing the last collected record
          {beads.age_seconds !== undefined
            ? " · " + duration(beads.age_seconds * 1000) + " old"
            : ""}
          .
        </p>
      )}
      <div className="facts">
        {[
          "status",
          "priority",
          "issue_type",
          "assignee",
          "owner",
          "created_at",
          "updated_at",
        ].map((key) => (
          <div key={key}>
            <dt>{label(key)}</dt>
            <dd>{text(bead[key])}</dd>
          </div>
        ))}
      </div>
      {["description", "acceptance_criteria", "notes"].map((key) =>
        typeof bead[key] === "string" && bead[key] ? (
          <details key={key} open={key === "description"}>
            <summary>{label(key)}</summary>
            <SafeMarkdown value={text(bead[key])} />
          </details>
        ) : null,
      )}
      {metadata.success && Object.keys(metadata.data).length > 0 && (
        <details>
          <summary>Metadata</summary>
          <dl className="facts">
            {Object.entries(metadata.data).map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{text(value)}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
      <details>
        <summary>Dependencies and dependents</summary>
        <pre>{text(bead.dependencies)}</pre>
        {beads.dependents.map((item, index) => (
          <div className="row" key={index}>
            {text(item.id)} · {text(item.title)} · {text(item.status)}
          </div>
        ))}
      </details>
      <details>
        <summary>Comments ({beads.comments.length})</summary>
        {beads.comments.map((item, index) => (
          <div className="comment" key={text(item.id) || index}>
            <div className="muted">
              {text(item.author)} · {text(item.created_at)}
            </div>
            <SafeMarkdown value={text(item.text ?? item.body)} />
          </div>
        ))}
      </details>
      <details>
        <summary>Event history ({beads.events.length})</summary>
        {beads.events.map((event, index) => (
          <div className="event" key={text(event.id) || index}>
            <strong>{label(text(event.event_type))}</strong>
            <span className="muted">
              {text(event.created_at)} · {text(event.actor)}
            </span>
            <pre>
              {text(event.old_value)} → {text(event.new_value)}
            </pre>
          </div>
        ))}
      </details>
      <details>
        <summary>Copy a native command</summary>
        <p className="muted">
          Replace &lt;actor&gt; with your actual session ID.
        </p>
        {Object.entries(beads.commands).map(([name, value]) => (
          <div className="command" key={name}>
            <code>{value}</code>
            <Copy value={value} label={"Copy " + name} />
          </div>
        ))}
      </details>
    </Panel>
  );
}
export function Delivery({
  candidates,
  range,
  onLog,
  clearRange,
}: {
  candidates: Candidate[];
  range: Range;
  clearRange: () => void;
  onLog: (candidate: string, step: string, attempt: string) => void;
}) {
  const visible = candidates.filter(
    (candidate) =>
      !range ||
      [
        candidate.submitted_at,
        candidate.promoted_at,
        ...candidate.attempts.map((a) => a.started_at ?? a.created_at),
      ].some((at) => at && inRange(at, range)),
  );
  const hiddenAttempts = candidates
    .flatMap((c) => c.attempts)
    .filter((a) => !inRange(a.started_at ?? a.created_at ?? "", range)).length;
  return (
    <Panel title="Delivery evidence">
      {range && (
        <p className="range-note">
          {candidates.length - visible.length} candidates and {hiddenAttempts}{" "}
          attempts hidden by the selected range.{" "}
          <button onClick={clearRange}>Show lifetime delivery</button>
        </p>
      )}
      {visible.length ? (
        visible.map((candidate) => (
          <article className="candidate" key={candidate.id}>
            <header>
              <strong>{candidate.subject || candidate.id.slice(0, 8)}</strong>
              <span className="pill">{label(candidate.state)}</span>
            </header>
            <p>
              <code>{candidate.branch}</code>
            </p>
            <p className="muted">
              {candidate.links.some((l) => l.method === "transcript")
                ? "Matched from a session"
                : "Matched by branch"}{" "}
              · Submitted {when(candidate.submitted_at)}
            </p>
            {candidate.promoted_at && (
              <p className="muted">
                Promoted {when(candidate.promoted_at)}
                {candidateDuration(candidate)
                  ? " · " + candidateDuration(candidate) + " after submission"
                  : ""}
              </p>
            )}
            {candidate.attempts
              .filter((a) => inRange(a.started_at ?? a.created_at ?? "", range))
              .map((attempt) => (
                <details key={attempt.id} open>
                  <summary>
                    Attempt {attempt.number} · {label(attempt.state)}
                  </summary>
                  {attempt.steps.length ? (
                    attempt.steps.map((step) => (
                      <div className="ci-step" key={step.name}>
                        <div>
                          <strong>{step.name}</strong>
                          <p className="muted">
                            {step.result_class ?? "Pending"} ·{" "}
                            {duration(step.elapsed_ms)}
                            {step.exit_code !== null
                              ? " · exit " + step.exit_code
                              : ""}
                          </p>
                        </div>
                        <button
                          onClick={() =>
                            onLog(candidate.id, step.name, attempt.id)
                          }
                        >
                          Log tail
                        </button>
                      </div>
                    ))
                  ) : (
                    <p className="muted">No step results yet.</p>
                  )}
                </details>
              ))}
          </article>
        ))
      ) : (
        <p className="muted">
          {range
            ? "No delivery evidence in this range."
            : "No observed delivery candidates."}
        </p>
      )}
    </Panel>
  );
}
export function RequestTable({
  path,
  search,
  range,
}: {
  path: string;
  search: string;
  range: Range;
}) {
  const [rows, setRows] = useState<Requests | null>(null),
    [sort, setSort] = useState("time"),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false);
  const pending = useRef<AbortController | null>(null);
  const params = detailApiQuery(path, search);
  params.set("requests", "1");
  params.set("sort", sort);
  if (range) {
    params.set("since", range.since);
    params.set("until", range.until);
  }
  const query = params.toString();
  useEffect(() => {
    const controller = new AbortController();
    pending.current = controller;
    setLoading(false);
    setRows(null);
    setError("");
    void request("/api" + path + "?" + query, RequestsSchema, controller.signal)
      .then((value) => {
        if (value) setRows(value.data);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "Could not read requests");
      });
    return () => controller.abort();
  }, [path, query]);
  async function more() {
    const controller = pending.current;
    if (!rows?.next_cursor || !controller || loading) return;
    setLoading(true);
    try {
      const value = await request(
        "/api" +
          path +
          "?" +
          query +
          "&cursor=" +
          encodeURIComponent(rows.next_cursor),
        RequestsSchema,
        controller.signal,
      );
      if (value && !controller.signal.aborted)
        setRows((current) =>
          current
            ? {
                requests: [...current.requests, ...value.data.requests],
                next_cursor: value.data.next_cursor,
              }
            : value.data,
        );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not read requests");
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }
  return (
    <Panel
      title="Requests"
      action={
        <select
          aria-label="Request sort"
          value={sort}
          onChange={(e) => setSort(e.target.value)}
        >
          <option value="time">Time</option>
          <option value="share">Largest share</option>
        </select>
      }
    >
      {error && <p role="alert">{error}</p>}
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Time / request</th>
              <th>Model</th>
              <th>Role / agent</th>
              <th>Attributed share</th>
            </tr>
          </thead>
          <tbody>
            {rows?.requests.map((row) => (
              <tr key={row.response}>
                <td>
                  {when(row.observed_at)}
                  <details>
                    <summary className="request-id">
                      {row.response.slice(0, 22)}…
                    </summary>
                    <pre>{JSON.stringify(row, null, 2)}</pre>
                  </details>
                </td>
                <td>{row.model ?? "Unknown model"}</td>
                <td>
                  {label(row.role ?? "unknown")}
                  <span className="muted block">{row.agent ?? "Main"}</span>
                </td>
                <td>
                  <Amount value={row.share_picos} digits={6} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!rows && !error && <p className="muted">Loading requests…</p>}
      {rows?.requests.length === 0 && (
        <p className="muted">No requests in this range.</p>
      )}
      {rows?.next_cursor && (
        <button disabled={loading} onClick={() => void more()}>
          {loading ? "Loading…" : "Load more requests"}
        </button>
      )}
    </Panel>
  );
}
