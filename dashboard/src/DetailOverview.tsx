import { duration, label, when, type Candidate, type Detail } from "./data";
import { Amount, Copy, Icon, Link, Panel } from "./ui";

export function DeliverySummary({
  candidates,
  open,
}: {
  candidates: readonly Candidate[];
  open: () => void;
}) {
  const counts = new Map<string, number>();
  for (const candidate of candidates)
    counts.set(candidate.state, (counts.get(candidate.state) ?? 0) + 1);
  const latest = candidates
    .filter((c) => c.promoted_at && Number.isFinite(Date.parse(c.promoted_at)))
    .sort(
      (a, b) =>
        Date.parse(b.promoted_at ?? "") - Date.parse(a.promoted_at ?? ""),
    )[0];
  return (
    <Panel
      title="Delivery"
      action={<button onClick={open}>View delivery</button>}
    >
      {candidates.length ? (
        <>
          <p>
            {candidates.length} observed{" "}
            {candidates.length === 1 ? "candidate" : "candidates"} · lifetime
          </p>
          <ul className="delivery-states">
            {[...counts].map(([state, count]) => (
              <li
                className={
                  /fail|reject|block/.test(state) ? "failure-text" : ""
                }
                key={state}
              >
                {count} {label(state)}
              </li>
            ))}
          </ul>
          {latest && (
            <p className="muted">
              Latest observed promotion: {when(latest.promoted_at)}
              {candidateDuration(latest)
                ? " · " + candidateDuration(latest) + " after submission"
                : ""}
              .
            </p>
          )}
          <p className="muted">
            Candidate outcomes do not change the task’s native status.
          </p>
        </>
      ) : (
        <p className="muted">No observed delivery candidates.</p>
      )}
    </Panel>
  );
}

export function RecentActivity({ detail }: { detail: Detail }) {
  const milestones = detail.candidates.flatMap((candidate) => [
    {
      at: candidate.submitted_at,
      title: "Candidate submitted",
      id: candidate.id,
      tone: "neutral",
    },
    {
      at: candidate.promoted_at,
      title: "Candidate promoted",
      id: candidate.id,
      tone: "success",
    },
    ...candidate.attempts
      .filter((a) => /pass|fail|timed.out/.test(a.state))
      .map((a) => ({
        at: a.finished_at,
        title: `Attempt ${a.number} · ${label(a.state)}`,
        id: a.id,
        tone: /pass/.test(a.state) ? "success" : "failure",
      })),
  ]);
  const seen = new Set<string>();
  const recent = milestones
    .filter((item) => {
      const key = item.id + item.title + item.at;
      if (!item.at || !Number.isFinite(Date.parse(item.at)) || seen.has(key))
        return false;
      seen.add(key);
      return true;
    })
    .sort((a, b) => Date.parse(b.at ?? "") - Date.parse(a.at ?? ""))
    .slice(0, 3);
  return (
    <section className="recent-activity" aria-label="Recent activity">
      <h2>Recent activity</h2>
      {recent.length ? (
        <ol>
          {recent.map((item) => (
            <li key={item.id + item.title}>
              <span className={"activity-dot " + item.tone} aria-hidden="true">
                ●
              </span>
              <div>
                {item.title}
                <span className="muted block">{when(item.at)}</span>
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted">No timestamped delivery milestones observed.</p>
      )}
    </section>
  );
}

export function Sessions({
  detail,
  sessions,
}: {
  detail: Detail;
  sessions: readonly string[];
}) {
  return (
    <Panel title="Sessions">
      {sessions.length ? (
        sessions.map((thread) => {
          const contributor = detail.contributors.find(
            (c) => c.thread === thread,
          );
          return (
            <div className="row" key={thread}>
              <Link to={"/session/" + thread}>{thread}</Link>
              {contributor ? (
                <Amount
                  value={contributor.amount_picos}
                  coverage={!!contributor.coverage}
                />
              ) : (
                <span className="muted">Amount not observed</span>
              )}
            </div>
          );
        })
      ) : (
        <p>No observed session source.</p>
      )}
      {detail.card.thread && (
        <Copy value={detail.card.thread} label="Copy session ID" />
      )}
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
      <h3>Roles and subagents</h3>
      {detail.role_spans.map((r, index) => (
        <div className="row" key={r.thread + r.agent + r.start + index}>
          <span className="role-label">
            <Icon role={r.role} small />
            {label(r.role)}
            <span className="muted">
              {r.agent ? "Subagent " + r.agent : "Main"}
              {r.inherited ? " · inherited" : ""}
            </span>
          </span>
          <span className="muted">
            {when(r.start)} · {r.evidence}
          </span>
        </div>
      ))}
      {detail.subagents.map((agent) => (
        <div className="row" key={agent.label}>
          <span>{agent.label}</span>
          <Amount value={agent.amount_picos} />
        </div>
      ))}
      {!detail.role_spans.length && !detail.subagents.length && (
        <p className="muted">No observed role or subagent details.</p>
      )}
    </Panel>
  );
}

export function Ownership({ detail }: { detail: Detail }) {
  return (
    <Panel title="Ownership details">
      {detail.intervals.length ? (
        detail.intervals.map((i) => (
          <div className="row" key={i.thread + i.start}>
            <div>
              <Link to={"/bead/" + i.bead}>{i.bead}</Link>
              {i.deleted && <span className="muted"> · deleted record</span>}
              <p className="muted">
                {when(i.start)} → {i.end ? when(i.end) : "Open"}
              </p>
            </div>
            <Link to={"/session/" + i.thread}>{i.thread}</Link>
          </div>
        ))
      ) : (
        <p className="muted">No observed ownership intervals.</p>
      )}
      <h3>Outside ownership context</h3>
      <p className="muted">
        These requests are outside this work’s attributed scope and are not
        included in its lifetime total.
      </p>
      {detail.context_requests.length ? (
        detail.context_requests.map((p) => (
          <div className="row" key={p.response}>
            <span>
              {when(p.observed_at)} · {p.response}
            </span>
            <Link to={"/session/" + p.thread}>{p.thread}</Link>
          </div>
        ))
      ) : (
        <p className="muted">No outside-scope requests observed.</p>
      )}
    </Panel>
  );
}

export function candidateDuration(candidate: Candidate): string | null {
  if (!candidate.submitted_at || !candidate.promoted_at) return null;
  const elapsed =
    Date.parse(candidate.promoted_at) - Date.parse(candidate.submitted_at);
  return Number.isFinite(elapsed) && elapsed >= 0 ? duration(elapsed) : null;
}
