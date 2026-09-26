import { label, when, type Card, type Epic } from "./data";
import { Amount, Link, Panel } from "./ui";

export function EpicProgress({ epic, direct }: { epic: Epic; direct: Card }) {
  const stale = epic.collector.registry?.error || epic.collector.bead_events_error || epic.collector.tollgate_error ||
    epic.collector.bead_events_behind || epic.collector.summaries_error || epic.collector.summaries_behind || epic.collector.tollgate_behind;
  return <Panel title="Epic progress">
    <p>Native epic status: {epic.native_status} · {epic.active} active descendants · {epic.owners.length} distinct assigned owners</p>
    <p className="muted">Active means assigned work in progress or observed CI; owners are not a count of live processes.</p>
    <p>{epic.in_ci} descendants in CI · {epic.blocked} with uncompleted prerequisites · {epic.held} deferred / held</p>
    <div className="facts">{epic.groups.map(group => <div key={group.category}>
      <dt>{label(group.category)}</dt><dd>{group.completed} / {group.total} completed</dd>
    </div>)}</div>
    <p>Direct epic lifetime spend: <Amount value={direct.amount_picos} coverage={Boolean(direct.coverage || direct.unpriced)} /> · Descendant lifetime spend: <Amount value={epic.amount_picos} coverage={Boolean(epic.incomplete || epic.unpriced || epic.missing_costs)} /></p>
    <p className="muted">{epic.scope}. These amounts remain separate from each other and the feed totals.</p>
    <p className="muted">{epic.unpriced} unpriced descendant requests · {epic.incomplete} descendants with incomplete coverage · {epic.missing_costs} without cost observations</p>
    <p className={stale ? "warning" : "muted"}>Cached descendant observation: {when(epic.refreshed)}{stale ? " · Collection delayed or unavailable; activity may be stale." : " · Read-only collected snapshot."}</p>
    <p className="muted">CI observation: {when(epic.collector.tollgate_refreshed)}</p>
    <details><summary>Descendant work ({epic.members.length})</summary>
      <p className="muted">Classification: {epic.classification}. Closed without a completed resolution does not count as completed.</p>
      {epic.members.map(member => <div className="row" key={member.bead}>
        <div>
          <Link to={"/bead/" + encodeURIComponent(member.bead)}>{member.title}</Link>
          <p className="muted">{label(member.category)} · {member.direct_child ? "Child" : "Descendant"} · {member.current ? "Native" : "Deleted/missing; last native status"} {member.native_status} · {member.state}{member.in_ci ? " · Descendant CI active" : ""}{member.ci_failed ? " · Observed CI failure" : ""}{member.cancelled ? " · Cancelled" : ""}{member.held ? " · Native deferral / hold" : ""}</p>
          {member.owner && <Link to={"/session/" + encodeURIComponent(member.owner)}>Owner {member.owner}</Link>}
          {member.blockers.length > 0 && <p>Uncompleted prerequisites: {member.blockers.map(id => <Link key={id} to={"/bead/" + encodeURIComponent(id)}>{id} </Link>)}</p>}
        </div>
      </div>)}
    </details>
  </Panel>;
}
