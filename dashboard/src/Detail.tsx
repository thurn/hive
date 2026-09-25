import { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import {
  DetailSchema,
  cardTitle,
  RecordSchema,
  RequestsSchema,
  diagnosticAt,
  diagnosticKind,
  duration,
  label,
  text,
  when,
  type Beads,
  type Candidate,
  type Detail,
  type Diagnostic,
  type Requests,
} from "./data";
import { request } from "./network";
import { Breakdown, Timeline, inRange, type Range } from "./Timeline";
import { Amount, Copy, Empty, Hex, Icon, Link, Panel, State } from "./ui";

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
function BeadsPanel({ beads }: { beads: Beads }) {
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
function Delivery({
  candidates,
  range,
  onLog,
}: {
  candidates: Candidate[];
  range: Range;
  onLog: (candidate: string, step: string, attempt: string) => void;
}) {
  return (
    <Panel title="Delivery">
      {candidates.length ? (
        candidates.map((candidate) => (
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
                {candidate.submitted_at
                  ? " · " +
                    duration(
                      Date.parse(candidate.promoted_at) -
                        Date.parse(candidate.submitted_at),
                    ) +
                    " after submission"
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
        <p className="muted">No observed delivery candidates.</p>
      )}
    </Panel>
  );
}
function RequestTable({
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
  const params = new URLSearchParams(search);
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
          if (!controller.signal.aborted)
            setError(
              e instanceof Error ? e.message : "Could not read requests",
            );
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
export function DetailView({ path, search }: { path: string; search: string }) {
  const [detail, setDetail] = useState<Detail | null>(null),
    [error, setError] = useState(""),
    [range, setRange] = useState<Range>(null),
    [excerpt, setExcerpt] = useState<{
      title: string;
      value: Record<string, unknown> | null;
      error: string;
    } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setDetail(null);
    setRange(null);
    setError("");
    setExcerpt(null);
    void request("/api" + path + search, DetailSchema, controller.signal)
      .then((value) => {
        if (value) setDetail(value.data);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "Could not read detail");
      });
    return () => controller.abort();
  }, [path, search]);
  const sourceRequest = useRef<AbortController | null>(null);
  useEffect(
    () => () => {
      sourceRequest.current?.abort();
    },
    [path, search],
  );
  async function readSource(title: string, url: string) {
    sourceRequest.current?.abort();
    const controller = new AbortController();
    sourceRequest.current = controller;
    setExcerpt({ title, value: null, error: "" });
    try {
      const result = await request(url, RecordSchema, controller.signal);
      if (!controller.signal.aborted)
        setExcerpt({ title, value: result?.data ?? null, error: "" });
    } catch (e) {
      if (!controller.signal.aborted)
        setExcerpt({
          title,
          value: null,
          error: e instanceof Error ? e.message : "Source unavailable",
        });
    }
  }
  async function readExcerpt(item: Diagnostic) {
    const title = label(diagnosticKind(item));
    const query = new URLSearchParams({ thread: item.thread });
    if (item.agent) query.set("agent", item.agent);
    if (item.call_id) query.set("call", item.call_id);
    else if (item.id) query.set("event", item.id);
    else {
      sourceRequest.current?.abort();
      setExcerpt({
        title,
        value: null,
        error: "No independently verifiable source excerpt.",
      });
      return;
    }
    await readSource(title, "/api/excerpt?" + query);
  }
  async function readLog(candidate: string, step: string, attempt: string) {
    await readSource(
      "CI log · " + step,
      "/api/ci-log?" + new URLSearchParams({ candidate, step, attempt }),
    );
  }
  if (!detail)
    return (
      <>
        <Link to="/" className="back">
          ← Back to newsfeed
        </Link>
        <Empty
          title={error ? "Detail unavailable" : "Loading this work"}
          action={
            error ? (
              <button onClick={() => location.reload()}>Try again</button>
            ) : undefined
          }
        >
          {error || "Reading requests, ownership and delivery evidence."}
        </Empty>
      </>
    );
  const diagnostics = detail.diagnostics.filter((item) =>
    inRange(diagnosticAt(item), range),
  );
  const kinds = [...new Set(diagnostics.map(diagnosticKind))];
  return (
    <>
      <Link
        to={sessionStorage.getItem("hive-feed-url") ?? "/"}
        className="back"
      >
        ← Back to newsfeed
      </Link>
      <header className="detail-header">
        <div>
          <div className="detail-meta">
            <span className="emblem">
              {detail.card.kind === "bead" ? (
                <Hex project={detail.card.project} />
              ) : (
                <Icon role={detail.card.primary_role} />
              )}
            </span>
            <span>
              {detail.card.project} ·{" "}
              <code>
                {detail.card.bead ??
                  detail.card.thread?.slice(0, 8) ??
                  label(detail.card.kind)}
              </code>
            </span>
            <State value={detail.card.state} />
          </div>
          <h1>{cardTitle(detail.card)}</h1>
          <p>
            {detail.card.subtitle ||
              label(detail.card.kind) + " · " + label(detail.card.primary_role)}
          </p>
        </div>
        <div className="detail-total">
          <Amount
            value={detail.amount_picos}
            coverage={!!detail.card.coverage}
          />
          <p className="muted">
            {detail.card.coverage
              ? "Incomplete host coverage"
              : "Retained request estimates"}
            {detail.card.unpriced
              ? " · " + detail.card.unpriced + " unpriced"
              : ""}
          </p>
        </div>
      </header>
      <div className="owner-chips">
        {detail.card.owners.map((owner) => (
          <Link key={owner} to={"/session/" + owner}>
            Session {owner.slice(0, 8)} ↗
          </Link>
        ))}
        {detail.card.thread && (
          <Copy value={detail.card.thread} label="Copy session ID" />
        )}
        {detail.card.kind === "tail" && (
          <Link to={path}>View whole session →</Link>
        )}
      </div>
      <Timeline
        detail={detail}
        range={range}
        onRange={setRange}
        onDiagnostic={(item) => void readExcerpt(item)}
      />
      <Breakdown detail={detail} />
      {excerpt && (
        <section className="panel excerpt" aria-label="Source excerpt">
          <header>
            <h2>{excerpt.title}</h2>
            <button
              onClick={() => {
                sourceRequest.current?.abort();
                setExcerpt(null);
              }}
            >
              Close excerpt
            </button>
          </header>
          {excerpt.error ? (
            <p role="alert">{excerpt.error}</p>
          ) : excerpt.value ? (
            <>
              {excerpt.value.input_summary && (
                <p>{text(excerpt.value.input_summary)}</p>
              )}
              {excerpt.value.reason && <p>{text(excerpt.value.reason)}</p>}
              <pre>
                {text(
                  excerpt.value.result ??
                    excerpt.value.tail ??
                    excerpt.value.pointer ??
                    "",
                )}
              </pre>
              {excerpt.value.truncated && (
                <p className="muted">Only the last 4 KiB are shown.</p>
              )}
              {excerpt.value.pending && (
                <p className="muted">This call has no observed result yet.</p>
              )}
            </>
          ) : (
            <p>Reading source…</p>
          )}
        </section>
      )}
      <div className="detail-columns">
        <div>
          <Panel title="Diagnostics">
            {kinds.length ? (
              kinds.map((kind) => (
                <details
                  key={kind}
                  open={kind !== "tool_call" && kind !== "intentional_wait"}
                >
                  <summary>
                    {label(kind)} (
                    {
                      diagnostics.filter((d) => diagnosticKind(d) === kind)
                        .length
                    }
                    )
                  </summary>
                  {diagnostics
                    .filter((d) => diagnosticKind(d) === kind)
                    .map((item, index) => (
                      <div
                        className="diagnostic-row"
                        key={item.call_id ?? item.id ?? index}
                      >
                        <div>
                          <strong>{item.tool ?? label(kind)}</strong>
                          <p className="muted">
                            {when(diagnosticAt(item))} · {item.host} ·{" "}
                            {item.agent || "Main"}
                            {item.duration_ms != null
                              ? " · " + duration(item.duration_ms)
                              : ""}
                          </p>
                          {item.amount_picos && (
                            <Amount value={item.amount_picos} digits={4} />
                          )}
                        </div>
                        <button onClick={() => void readExcerpt(item)}>
                          Excerpt
                        </button>
                      </div>
                    ))}
                </details>
              ))
            ) : (
              <p className="muted">No diagnostics in this range.</p>
            )}
          </Panel>
          {!path.startsWith("/ledger/") && (
            <RequestTable path={path} search={search} range={range} />
          )}
          <Panel title="Ownership intervals">
            {detail.intervals.length ? (
              detail.intervals.map((i) => (
                <div className="row" key={i.thread + i.start}>
                  <div>
                    <Link to={"/bead/" + i.bead}>{i.bead}</Link>
                    <p className="muted">
                      {when(i.start)} → {i.end ? when(i.end) : "Open"}
                    </p>
                  </div>
                  <Link to={"/session/" + i.thread}>
                    {i.thread.slice(0, 8)} ↗
                  </Link>
                </div>
              ))
            ) : (
              <p className="muted">No observed ownership intervals.</p>
            )}
          </Panel>
        </div>
        <div>
          <Delivery
            candidates={detail.candidates}
            range={range}
            onLog={(candidate, step, attempt) =>
              void readLog(candidate, step, attempt)
            }
          />
          {detail.role_spans.length > 0 && (
            <Panel title="Roles and subagents">
              {detail.role_spans.map((r, index) => (
                <div className="row" key={r.thread + r.agent + r.start + index}>
                  <span className="role-label">
                    <Icon role={r.role} small />
                    {label(r.role)}
                    <span className="muted">
                      {r.agent ? "↳ " + r.agent.slice(0, 12) : "Main"}
                      {r.inherited ? " · inherited" : ""}
                    </span>
                  </span>
                  <span className="muted">{when(r.start)}</span>
                </div>
              ))}
              {detail.subagents.map((agent) => (
                <div className="row" key={agent.label}>
                  <span>{agent.label}</span>
                  <Amount value={agent.amount_picos} />
                </div>
              ))}
            </Panel>
          )}
          <Panel title="Contributors">
            {detail.contributors.map((c) => (
              <div className="row" key={c.thread}>
                <Link to={"/session/" + c.thread}>
                  {c.thread.slice(0, 8)} ↗
                </Link>
                <Amount value={c.amount_picos} coverage={!!c.coverage} />
              </div>
            ))}
            {detail.card.kind === "unattributable" && (
              <p className="muted">
                Ownership evidence is incomplete or cannot be assigned reliably.
              </p>
            )}
            {detail.card.kind === "small_tails" && (
              <p className="muted">
                Unowned amounts below the individual tail threshold.
              </p>
            )}
          </Panel>
          {detail.created_beads.length > 0 && (
            <Panel title="Work filed here">
              {detail.created_beads.map((b) => (
                <div className="row" key={b.bead}>
                  <Link to={"/bead/" + b.bead}>{b.bead} →</Link>
                </div>
              ))}
            </Panel>
          )}
        </div>
      </div>
      {detail.beads && <BeadsPanel beads={detail.beads} />}
    </>
  );
}
