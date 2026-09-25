import { useState, type ReactNode, type MouseEvent } from "react";
import { cardPath, cardTitle, label, money, type Card } from "./data";

export const roles = [
  "executor",
  "bead",
  "warden",
  "weaver",
  "sage",
  "mason",
  "vizier",
  "justiciar",
  "archivist",
  "ad_hoc",
  "unknown",
];
const paths: Record<string, string> = {
  executor: "M7 5l6-3 12 12-5 5L8 7m7 7L3 27l3 3 13-13",
  bead: "M16 2v28 M12 5a4 4 0 1 0 8 0a4 4 0 1 0-8 0 M12 16a4 4 0 1 0 8 0a4 4 0 1 0-8 0 M12 27a4 4 0 1 0 8 0a4 4 0 1 0-8 0",
  warden: "M16 2 28 7v9c0 7-7 12-12 14C11 28 4 23 4 16V7Z M10 16l4 4 8-9",
  weaver:
    "M8 5h16M8 27h16M11 5v22M21 5v22M11 10h10M11 15h10M11 20h10M21 24c8 0 8 6 4 6",
  sage: "M16 7C10 3 5 3 2 5v23c5-2 9-2 14 1 5-3 9-3 14-1V5c-4-2-9-2-14 2Z M16 7v22",
  mason: "M5 29h22v-8H5Z M16 21v8 M4 18h24M14 2 4 14l15 1Z M17 11l10 7",
  vizier:
    "M7 27h18M11 23l-4 4M21 23l4 4M16 2a11 11 0 1 0 0 22a11 11 0 1 0 0-22 M11 8l3-2",
  justiciar:
    "M18 2c2 9-6 9-5 15-4-2-4-5-4-7-8 9-6 20 7 20 14 0 17-15 7-22 2 7-4 8-4 10 0-5-5-7-1-16Z",
  archivist: "M3 5h26v7H3Z M6 12v17h20V12M12 17h8",
  ad_hoc: "M16 3a13 13 0 1 0 0 26a13 13 0 1 0 0-26 M15 16h2",
  unknown:
    "M16 3a13 13 0 1 0 0 26a13 13 0 1 0 0-26 M12 11c0-5 9-5 9 0 0 4-5 4-5 8M16 24h.01",
};
export function Icon({
  role = "ad_hoc",
  small = false,
}: {
  role?: string;
  small?: boolean;
}) {
  return (
    <svg
      className={small ? "role-icon small" : "role-icon"}
      viewBox="0 0 32 32"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={paths[role] ?? paths.unknown} />
    </svg>
  );
}
export function Hex({
  project,
  brand = false,
}: {
  project?: string;
  brand?: boolean;
}) {
  return (
    <svg
      className={brand ? "brand-icon" : "project-icon"}
      viewBox="0 0 32 36"
      aria-hidden="true"
    >
      <path
        d="M16 1 30 9v18l-14 8L2 27V9Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
      />
      {brand ? (
        <path
          d="M16 9l7 4v10l-7 4-7-4V13Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.4"
        />
      ) : project && project !== "Other" ? (
        <text
          x="16"
          y="24"
          textAnchor="middle"
          fill="currentColor"
          fontSize="18"
        >
          {project[0]?.toUpperCase()}
        </text>
      ) : null}
    </svg>
  );
}
export function navigate(path: string) {
  sessionStorage.setItem(
    "hive-scroll:" + location.pathname + location.search,
    String(window.scrollY),
  );
  history.pushState(null, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}
export function Link({
  to,
  children,
  className,
  onClick,
}: {
  to: string;
  children: ReactNode;
  className?: string;
  onClick?: () => void;
}) {
  return (
    <a
      href={to}
      className={className}
      onClick={(e: MouseEvent<HTMLAnchorElement>) => {
        if (
          e.button === 0 &&
          !e.metaKey &&
          !e.ctrlKey &&
          !e.altKey &&
          !e.shiftKey
        ) {
          e.preventDefault();
          navigate(to);
          onClick?.();
        }
      }}
    >
      {children}
    </a>
  );
}
export function State({ value }: { value: string }) {
  const tone = ["Working", "Complete"].includes(value)
    ? "success"
    : ["Stalled", "Blocked", "Awaiting approval", "Deferred"].includes(value)
      ? "attention"
      : value === "Needs attention"
        ? "failure"
        : ["In CI", "Idle"].includes(value)
          ? "information"
          : "neutral";
  return (
    <span className={"state " + tone}>
      <span aria-hidden="true">
        {value === "Complete"
          ? "✓"
          : tone === "failure" || tone === "attention"
            ? "!"
            : "●"}
      </span>
      {value}
    </span>
  );
}
export function Amount({
  value,
  coverage = false,
  digits = 2,
}: {
  value: string | null | undefined;
  coverage?: boolean;
  digits?: number;
}) {
  return (
    <span
      className="money"
      title={value == null ? "Unpriced" : value + " picodollars"}
    >
      {coverage ? "≥ " : ""}
      {money(value, digits)}
    </span>
  );
}
export function Copy({
  value,
  label: description = "Copy",
}: {
  value: string;
  label?: string;
}) {
  const [state, setState] = useState("");
  return (
    <span className="copy">
      <button
        onClick={() => {
          void navigator.clipboard.writeText(value).then(
            () => setState("Copied"),
            () => setState("Copy unavailable"),
          );
        }}
      >
        {state || description}
      </button>
      <span className="sr-only" role="status">
        {state}
      </span>
    </span>
  );
}
export function WorkCard({ card }: { card: Card }) {
  const ordered = Object.entries(card.roles)
    .filter(([, v]) => BigInt(v) > 0n)
    .sort((a, b) => (BigInt(a[1]) > BigInt(b[1]) ? -1 : 1));
  const badgeNames: Record<string, string> = {
    ci_failures: "CI failures",
    tool_errors: "tool errors",
    slow_tools: "slow tools",
    api_errors: "API errors",
    human_waits: "human waits",
    retry_loops: "retry loops",
  };
  return (
    <Link to={cardPath(card)} className="work-card">
      <div className="card-top">
        <span className="emblem">
          {card.kind === "bead" ? (
            <Hex project={card.project} />
          ) : card.kind === "agent" || card.kind === "tail" ? (
            <Icon role={card.primary_role} />
          ) : (
            <Hex />
          )}
        </span>
        <span className="project-name">{card.project}</span>
        <State value={card.state} />
      </div>
      <h2>{cardTitle(card)}</h2>
      <p className="card-description">
        {card.bead && (
          <>
            <code>{card.bead}</code> ·{" "}
          </>
        )}
        {card.subtitle ||
          [
            card.kind === "tail"
              ? "Unowned tail"
              : card.kind === "agent"
                ? "Agent session"
                : label(card.kind),
            label(card.primary_role),
          ].join(" · ")}
      </p>
      <footer>
        <Amount value={card.amount_picos} coverage={!!card.coverage} />
        <div className="card-roles">
          {ordered.slice(0, 2).map(([role]) => (
            <span key={role} title={label(role)}>
              <Icon role={role} small />
              {label(role)}
            </span>
          ))}
        </div>
        <div className="badges">
          {Object.entries(card.badges)
            .filter(([, n]) => n > 0)
            .slice(0, 2)
            .map(([key, count]) => (
              <span
                key={key}
                title={`${count} ${badgeNames[key] ?? label(key)}`}
              >
                ! {count} {key === "ci_failures" ? "CI" : ""}
                <span className="sr-only">{badgeNames[key]}</span>
              </span>
            ))}
        </div>
        <span className="card-arrow" aria-hidden="true">
          →
        </span>
      </footer>
    </Link>
  );
}
export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty panel">
      <span className="emblem">
        <Hex />
      </span>
      <h2>{title}</h2>
      <p>{children}</p>
      {action}
    </div>
  );
}
export function Panel({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className="panel">
      <header className="panel-header">
        <h2>{title}</h2>
        {action}
      </header>
      {children}
    </section>
  );
}
