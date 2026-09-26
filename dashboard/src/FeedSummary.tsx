import { keyPath, label, type Feed } from "./data";
import { Amount, Disclosure, Link } from "./ui";

function Findings({ items }: { items: Feed["hotspots"] }) {
  return (
    <ol className="findings">
      {items.map((item, index) => (
        <li key={item.kind + ":" + index}>
          <strong>{item.title}</strong>
          <p>
            <Amount value={item.amount_picos} /> in this window
          </p>
          <ul className="affected-work">
            {item.keys.map((key) => (
              <li key={key}>
                <Link to={keyPath(key)}>{key}</Link>
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ol>
  );
}

export function FeedSummary({ data, search }: { data: Feed; search: string }) {
  const params = new URLSearchParams(search);
  const coverage = data.hotspots.filter((item) => item.kind === "coverage");
  const findings = data.hotspots.filter((item) => item.kind !== "coverage");
  const incomplete =
    BigInt(data.summary.incomplete_picos) > 0n || data.summary.unpriced > 0;
  const ordered = [...data.summary.roles].sort((a, b) =>
    BigInt(a.amount_picos) > BigInt(b.amount_picos)
      ? -1
      : BigInt(a.amount_picos) < BigInt(b.amount_picos)
        ? 1
        : a.role.localeCompare(b.role),
  );
  return (
    <section className="recorded-summary" aria-label="Spend summary">
      <div className="recorded-total">
        <span className="muted">Recorded spend</span>
        <div className="total">
          <Amount value={data.summary.amount_picos} coverage={incomplete} />
        </div>
      </div>
      <div className="summary-actions">
        <Disclosure
          title={
            incomplete || coverage.length
              ? "Coverage incomplete"
              : "About this amount"
          }
        >
          <p>
            API-equivalent retained estimates for the selected window; unpriced
            requests are excluded.
          </p>
          <p>
            {data.summary.unpriced} requests unpriced.{" "}
            <Amount value={data.summary.incomplete_picos} /> of recorded spend
            has incomplete coverage. A recorded zero does not establish complete
            observation.
          </p>
          {coverage.length > 0 && <Findings items={coverage} />}
        </Disclosure>
        <Disclosure title="Spend by role">
          <p>
            Selected window:{" "}
            {data.summary.window === "today"
              ? "Today"
              : "Last " + data.summary.window.replace("d", " days")}{" "}
            · {data.summary.timezone}. Project:{" "}
            {params.get("project") || "All projects"}; request role:{" "}
            {params.has("role") ? label(params.get("role") ?? "") : "All roles"}
            .
          </p>
          <p>
            Search, state, activity and older-completed filters affect work
            rows, not these window totals. Work rows show lifetime spend.
          </p>
          {ordered.length ? (
            <dl className="role-totals">
              {ordered.map((role) => (
                <div key={role.role}>
                  <dt>{label(role.role)}</dt>
                  <dd>
                    <Amount value={role.amount_picos} />
                  </dd>
                </div>
              ))}
            </dl>
          ) : (
            <p>No priced role amounts in this window.</p>
          )}
        </Disclosure>
        {findings.length > 0 && (
          <Disclosure title={`Issues (${findings.length})`}>
            <Findings items={findings} />
          </Disclosure>
        )}
      </div>
    </section>
  );
}
