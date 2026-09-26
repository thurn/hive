import { cardPath, cardTitle, type Card } from "./data";
import { Amount, Link, State } from "./ui";

function qualifier(kind: string): string {
  switch (kind) {
    case "bead":
      return "";
    case "agent":
      return "Agent session";
    case "tail":
      return "Unowned tail";
    case "small_tails":
      return "Small tails ledger";
    case "unattributable":
      return "Unattributable ledger";
    default:
      return kind.replaceAll("_", " ");
  }
}

export function WorkList({ cards }: { cards: readonly Card[] }) {
  return (
    <table className="work-list" aria-label="All work">
      <caption className="narrow-lifetime">Lifetime spend per item</caption>
      <thead>
        <tr>
          <th scope="col">Work</th>
          <th scope="col">Project</th>
          <th scope="col">State</th>
          <th scope="col">Lifetime spend</th>
        </tr>
      </thead>
      <tbody>
        {cards.map((card) => (
          <tr key={card.key}>
            <td className="work-title">
              <Link to={cardPath(card)}>{cardTitle(card)}</Link>
              {qualifier(card.kind) && (
                <span className="work-kind">{qualifier(card.kind)}</span>
              )}
            </td>
            <td className="work-project">{card.project}</td>
            <td className="work-state">
              <State value={card.state} />
            </td>
            <td className="work-amount">
              <Amount
                value={card.amount_picos}
                coverage={!!card.coverage || card.unpriced > 0}
              />
              {card.unpriced > 0 && (
                <span className="unpriced">{card.unpriced} unpriced</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
