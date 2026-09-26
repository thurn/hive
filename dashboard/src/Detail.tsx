import { backToFeed } from "./navigation";
import { useEffect, useRef, useState } from "react";
import {
  DetailSchema,
  cardTitle,
  RecordSchema,
  diagnosticAt,
  diagnosticKind,
  duration,
  label,
  text,
  when,
  type Detail,
  type Diagnostic,
} from "./data";
import { request } from "./network";
import { EpicProgress } from "./EpicProgress";
import { Breakdown, Timeline, inRange, type Range } from "./Timeline";
import { Amount, Empty, Link, Panel, State } from "./ui";
import { BeadsPanel, Delivery, RequestTable } from "./DetailEvidence";
import {
  DeliverySummary,
  RecentActivity,
  Sessions,
  Ownership,
} from "./DetailOverview";
import {
  detailApiQuery,
  readDetailState,
  writeDetailState,
  validRange,
  type DetailState,
  type DetailSection,
  type DetailTab,
} from "./detailState";
export { SafeMarkdown } from "./DetailEvidence";
export function DetailView({ path, search }: { path: string; search: string }) {
  const [detail, setDetail] = useState<Detail | null>(null),
    [error, setError] = useState(""),
    [view, setView] = useState<DetailState>(() => readDetailState(search)),
    [excerpt, setExcerpt] = useState<{
      title: string;
      value: Record<string, unknown> | null;
      error: string;
    } | null>(null);
  const apiSearch = detailApiQuery(path, search).toString();
  const identity = path + (apiSearch ? "?" + apiSearch : "");
  useEffect(() => setView(readDetailState(search)), [path, search]);
  const range = view.range;
  function change(next: DetailState) {
    const state = validRange(next.range)
      ? next
      : { ...next, range: null, invalidRange: true };
    setView(state);
    if (!state.invalidRange) writeDetailState(path, search, state);
  }
  const sectionTriggers = useRef<
    Partial<Record<DetailSection, HTMLButtonElement | null>>
  >({});
  function closeSection() {
    const previous = view.section;
    change({ ...view, section: null });
    if (previous) sectionTriggers.current[previous]?.focus();
  }
  function setRange(next: Range) {
    change({ ...view, range: next, invalidRange: false });
  }
  function section(next: DetailSection) {
    change({
      ...view,
      tab: "overview",
      section: view.section === next ? null : next,
    });
  }
  useEffect(() => {
    const controller = new AbortController();
    setDetail(null);
    setError("");
    setExcerpt(null);
    void request("/api" + identity, DetailSchema, controller.signal)
      .then((value) => {
        if (value) setDetail(value.data);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "Could not read detail");
      });
    return () => controller.abort();
  }, [identity]);
  const sourceRequest = useRef<AbortController | null>(null);
  const sourcePanel = useRef<HTMLElement | null>(null);
  const sourceTrigger = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (excerpt?.title) sourcePanel.current?.focus();
  }, [excerpt?.title]);
  useEffect(
    () => () => {
      sourceRequest.current?.abort();
    },
    [identity],
  );
  async function readSource(title: string, url: string) {
    sourceTrigger.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
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
      sourceTrigger.current =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
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
        <div className="detail-breadcrumb">
          <Link to={backToFeed()} className="back">
            ← Back to newsfeed
          </Link>
        </div>
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
  const allKinds = [...new Set(detail.diagnostics.map(diagnosticKind))];
  const selectedKind = allKinds.includes(view.kind) ? view.kind : "";
  const diagnostics = detail.diagnostics.filter(
    (item) =>
      inRange(diagnosticAt(item), range) &&
      (!selectedKind || diagnosticKind(item) === selectedKind),
  );
  const sessions = [
    ...new Set([
      ...detail.card.owners,
      ...detail.contributors.map((c) => c.thread),
      ...(detail.card.thread ? [detail.card.thread] : []),
    ]),
  ];
  const singleSession = sessions.length === 1 ? sessions[0] : undefined;
  const scope =
    detail.card.kind === "tail"
      ? "Unowned tail · excludes owned request shares"
      : path.startsWith("/session/")
        ? "Whole session · includes owned and unowned requests"
        : detail.card.kind === "small_tails"
          ? "Small unowned tails · amounts below the individual display threshold"
          : detail.card.kind === "unattributable"
            ? "Unattributable spend · ownership evidence is incomplete or unreliable"
            : "Task-attributed request shares · lifetime";
  const tabs: readonly DetailTab[] = ["overview", "diagnostics", "delivery"];
  const sections: readonly Readonly<{
    id: DetailSection;
    title: string;
    available: boolean;
  }>[] = [
    {
      id: "coverage",
      title:
        detail.card.coverage || detail.card.unpriced
          ? "Coverage incomplete"
          : "About this amount",
      available: true,
    },
    {
      id: "requests",
      title: "View requests",
      available: !path.startsWith("/ledger/"),
    },
    { id: "breakdowns", title: "View all breakdowns", available: true },
    { id: "ownership", title: "Ownership details", available: true },
    { id: "sessions", title: "Sessions", available: true },
    { id: "task", title: "View task description", available: !!detail.beads },
    {
      id: "filed",
      title: "Work filed here",
      available: detail.created_beads.length > 0,
    },
  ];
  return (
    <>
      <div className="detail-breadcrumb">
        <Link to={backToFeed()} className="back">
          ← Back to newsfeed
        </Link>
        <span>{detail.card.project}</span>
      </div>
      <header className="detail-workspace-header">
        <div>
          <State value={detail.card.state} />
          <h1>{cardTitle(detail.card)}</h1>
          <p className="detail-scope muted">{scope}</p>
          <p className="detail-identity">
            <code>
              {detail.card.bead ?? detail.card.thread ?? detail.card.key}
            </code>{" "}
            · {label(detail.card.kind)} · {label(detail.card.primary_role)} ·{" "}
            {sessions.length} {sessions.length === 1 ? "session" : "sessions"}
          </p>
        </div>
        <div className="detail-actions">
          {singleSession ? (
            <Link to={"/session/" + singleSession} className="action-link">
              Open session
            </Link>
          ) : sessions.length > 1 ? (
            <button onClick={() => section("sessions")}>
              Sessions ({sessions.length})
            </button>
          ) : (
            <span className="muted">No observed session source</span>
          )}
          {detail.card.kind === "tail" && (
            <Link className="action-link" to={path}>
              View whole session
            </Link>
          )}
        </div>
      </header>
      <div className="detail-tabs" role="tablist" aria-label="Evidence views">
        {tabs.map((tab) => (
          <button
            key={tab}
            id={"tab-" + tab}
            role="tab"
            tabIndex={view.tab === tab ? 0 : -1}
            onKeyDown={(event) => {
              const index = tabs.indexOf(tab);
              const next =
                event.key === "ArrowRight"
                  ? tabs[(index + 1) % tabs.length]
                  : event.key === "ArrowLeft"
                    ? tabs[(index + tabs.length - 1) % tabs.length]
                    : event.key === "Home"
                      ? tabs[0]
                      : event.key === "End"
                        ? tabs[tabs.length - 1]
                        : undefined;
              if (next) {
                event.preventDefault();
                change({ ...view, tab: next });
                document.getElementById("tab-" + next)?.focus();
              }
            }}
            aria-selected={view.tab === tab}
            aria-controls={"view-" + tab}
            onClick={() => change({ ...view, tab })}
          >
            {label(tab)}
          </button>
        ))}
      </div>
      {view.invalidRange && (
        <p role="alert" className="warning">
          Invalid range: use valid timestamps with the start no later than the
          end.{" "}
          <button onClick={() => setRange(null)}>Clear invalid range</button>
        </p>
      )}
      {range && (
        <div className="selected-range" role="status">
          <span>
            Selected range: {when(range.since)} → {when(range.until)}. Requests
            and evidence are filtered; totals remain lifetime.
          </span>
          <button onClick={() => setRange(null)}>Clear range</button>
        </div>
      )}
      {excerpt && (
        <section
          ref={sourcePanel}
          tabIndex={-1}
          className="panel excerpt"
          aria-label="Source excerpt"
        >
          <header>
            <h2>{excerpt.title}</h2>
            <button
              onClick={() => {
                sourceRequest.current?.abort();
                setExcerpt(null);
                if (sourceTrigger.current?.isConnected)
                  sourceTrigger.current.focus();
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
      <section
        id={"view-" + view.tab}
        role="tabpanel"
        aria-labelledby={"tab-" + view.tab}
      >
        {view.tab === "overview" && (
          <>
            <div className="overview-grid">
              <div className="overview-main">
                <section
                  className="detail-recorded"
                  aria-label="Recorded lifetime spend"
                >
                  <span className="muted">Recorded spend · lifetime</span>
                  <div className="total">
                    <Amount
                      value={detail.amount_picos}
                      coverage={
                        !!detail.card.coverage || detail.card.unpriced > 0
                      }
                    />
                  </div>
                  <button
                    ref={(node) => {
                      sectionTriggers.current.coverage = node;
                    }}
                    aria-expanded={view.section === "coverage"}
                    aria-controls="section-coverage"
                    onClick={() => section("coverage")}
                  >
                    {detail.card.coverage || detail.card.unpriced
                      ? "Coverage incomplete"
                      : "About this amount"}
                  </button>
                </section>
                <Timeline
                  detail={detail}
                  range={range}
                  onRange={setRange}
                  onDiagnostic={(item) => {
                    change({
                      ...view,
                      tab: "diagnostics",
                      kind: diagnosticKind(item),
                    });
                    void readExcerpt(item);
                  }}
                />
                <RecentActivity detail={detail} />
              </div>
              <aside className="overview-support">
                <DeliverySummary
                  candidates={detail.candidates}
                  open={() => change({ ...view, tab: "delivery" })}
                />
                <Panel title="Lifetime spend by role">
                  <dl className="role-totals">
                    {(detail.breakdown.role ?? []).map((role) => (
                      <div key={role.label}>
                        <dt>{label(role.label)}</dt>
                        <dd>
                          <Amount value={role.amount_picos} />
                        </dd>
                      </div>
                    ))}
                  </dl>
                  {!detail.breakdown.role?.length && (
                    <p className="muted">No role amounts observed.</p>
                  )}
                </Panel>
                {detail.beads && (
                  <button
                    ref={(node) => {
                      sectionTriggers.current.task = node;
                    }}
                    aria-expanded={view.section === "task"}
                    aria-controls="section-task"
                    onClick={() => section("task")}
                  >
                    View task description
                  </button>
                )}
              </aside>
            </div>
            {detail.epic && (
              <EpicProgress epic={detail.epic} direct={detail.card} />
            )}
            <div className="evidence-sections">
              <div className="section-actions">
                {sections
                  .filter(
                    (s) => s.available && !["coverage", "task"].includes(s.id),
                  )
                  .map((s) => (
                    <button
                      ref={(node) => {
                        sectionTriggers.current[s.id] = node;
                      }}
                      key={s.id}
                      aria-expanded={view.section === s.id}
                      aria-controls={"section-" + s.id}
                      onClick={() => section(s.id)}
                    >
                      {s.title}
                    </button>
                  ))}
              </div>
              {view.section &&
                sections.some((s) => s.id === view.section && s.available) && (
                  <section
                    className="supporting-section"
                    onKeyDown={(event) => {
                      if (event.key === "Escape") {
                        event.stopPropagation();
                        closeSection();
                      }
                    }}
                    id={"section-" + view.section}
                    aria-label={
                      sections.find((s) => s.id === view.section)?.title
                    }
                  >
                    <button className="section-close" onClick={closeSection}>
                      Close section
                    </button>
                    {view.section === "coverage" && (
                      <Panel title="Coverage">
                        <p>
                          Lifetime API-equivalent retained estimates;{" "}
                          {detail.card.unpriced} requests are unpriced and
                          excluded.{" "}
                          {detail.card.coverage
                            ? "Host observation coverage is incomplete."
                            : "No incomplete-coverage flag was reported; this does not prove every request was observed."}
                        </p>
                      </Panel>
                    )}
                    {view.section === "requests" && (
                      <RequestTable path={path} search={search} range={range} />
                    )}
                    {view.section === "breakdowns" && (
                      <Breakdown detail={detail} />
                    )}
                    {view.section === "ownership" && (
                      <Ownership detail={detail} />
                    )}
                    {view.section === "sessions" && (
                      <Sessions detail={detail} sessions={sessions} />
                    )}
                    {view.section === "task" && detail.beads && (
                      <BeadsPanel beads={detail.beads} />
                    )}
                    {view.section === "filed" && (
                      <Panel title="Work filed here">
                        {detail.created_beads.map((b) => (
                          <div className="row" key={b.bead}>
                            <Link to={"/bead/" + b.bead}>{b.bead}</Link>
                          </div>
                        ))}
                      </Panel>
                    )}
                  </section>
                )}
            </div>
          </>
        )}
        {view.tab === "diagnostics" && (
          <Panel title="Diagnostics">
            <label className="diagnostic-filter">
              Kind{" "}
              <select
                aria-label="Diagnostic kind"
                value={selectedKind}
                onChange={(event) =>
                  change({ ...view, kind: event.target.value })
                }
              >
                <option value="">All kinds</option>
                {allKinds.map((kind) => (
                  <option key={kind} value={kind}>
                    {label(kind)} (
                    {
                      detail.diagnostics.filter(
                        (d) =>
                          diagnosticKind(d) === kind &&
                          inRange(diagnosticAt(d), range),
                      ).length
                    }
                    )
                  </option>
                ))}
              </select>
            </label>
            <p className="muted">
              {diagnostics.length} matching observations
              {range ? " in the selected range" : " across this work"}.
              Unknown-time events remain visible when no range is selected.
            </p>
            {diagnostics.length ? (
              diagnostics.map((item, index) => (
                <div
                  className="diagnostic-row"
                  key={item.call_id ?? item.id ?? index}
                >
                  <div>
                    <strong>{item.tool ?? label(diagnosticKind(item))}</strong>
                    <p className="muted">
                      {Number.isFinite(Date.parse(diagnosticAt(item)))
                        ? when(diagnosticAt(item))
                        : "Unknown time"}{" "}
                      · {label(diagnosticKind(item))} · {item.host} ·{" "}
                      {item.agent || "Main"}
                      {item.duration_ms != null
                        ? " · " + duration(item.duration_ms)
                        : ""}
                    </p>
                    <Link to={"/session/" + item.thread}>{item.thread}</Link>
                    {item.amount_picos && (
                      <Amount value={item.amount_picos} digits={4} />
                    )}
                  </div>
                  <button onClick={() => void readExcerpt(item)}>
                    Excerpt
                  </button>
                </div>
              ))
            ) : (
              <p className="muted">No diagnostics in this range.</p>
            )}
          </Panel>
        )}
        {view.tab === "delivery" && (
          <Delivery
            candidates={detail.candidates}
            range={range}
            clearRange={() => setRange(null)}
            onLog={(candidate, step, attempt) =>
              void readLog(candidate, step, attempt)
            }
          />
        )}
      </section>
    </>
  );
}
