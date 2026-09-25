import { useEffect, useRef, useState } from "react";
import {
  FeedSchema,
  HealthSchema,
  keyPath,
  label,
  type Feed,
  type Health,
} from "./data";
import { request } from "./network";
import { Amount, Empty, Icon, Link, WorkCard, navigate, roles } from "./ui";

export function useFeed(search: string) {
  const [data, setData] = useState<Feed | null>(null),
    [health, setHealth] = useState<Health | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false);
  const saved = useRef<{
    search: string;
    data: Feed;
    etag: string | null;
  } | null>(null);
  const query = new URLSearchParams(search).toString();
  useEffect(() => {
    const controller = new AbortController();
    let busy = false;
    if (saved.current?.search !== query) {
      saved.current = null;
      setData(null);
    }
    async function load() {
      if (busy || document.visibilityState === "hidden") return;
      busy = true;
      try {
        const next = await request(
          "/api/feed?" + query,
          FeedSchema,
          controller.signal,
          saved.current?.etag ?? undefined,
        );
        if (next) {
          // Refresh all loaded pages together, so moved/deleted cards cannot linger.
          const count = saved.current?.data.cards.length ?? 0;
          let value = next.data;
          while (value.cards.length < count && value.next_cursor) {
            const page = await request(
              "/api/feed?" +
                query +
                "&cursor=" +
                encodeURIComponent(value.next_cursor),
              FeedSchema,
              controller.signal,
            );
            if (!page) break;
            value = {
              ...value,
              cards: [...value.cards, ...page.data.cards],
              next_cursor: page.data.next_cursor,
            };
          }
          if (controller.signal.aborted) return;
          saved.current = { search: query, data: value, etag: next.etag };
          setData(value);
        }
        const status = await request(
          "/api/status",
          HealthSchema,
          controller.signal,
        );
        if (status) setHealth(status.data);
        setError("");
      } catch (e) {
        if (!controller.signal.aborted)
          setError(
            e instanceof Error ? e.message : "Could not load observations",
          );
      } finally {
        busy = false;
      }
    }
    void load();
    const timer = setInterval(() => void load(), 15000);
    document.addEventListener("visibilitychange", load);
    return () => {
      controller.abort();
      clearInterval(timer);
      document.removeEventListener("visibilitychange", load);
    };
  }, [query]);
  async function more() {
    const before = saved.current;
    if (!before?.data.next_cursor || loading) return;
    setLoading(true);
    try {
      const next = await request(
        "/api/feed?" +
          query +
          "&cursor=" +
          encodeURIComponent(before.data.next_cursor),
        FeedSchema,
      );
      if (next && saved.current === before) {
        const current = saved.current.data;
        const value = {
          ...current,
          cards: [
            ...current.cards,
            ...next.data.cards.filter(
              (c) => !current.cards.some((n) => n.key === c.key),
            ),
          ],
          next_cursor: next.data.next_cursor,
        };
        saved.current = { ...saved.current, data: value };
        setData(value);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load more work");
    } finally {
      setLoading(false);
    }
  }
  return { data, health, error, more, loading };
}
export function Filters({
  search,
  projects,
}: {
  search: string;
  projects: Feed["projects"];
}) {
  const params = new URLSearchParams(search);
  const [query, setQuery] = useState(params.get("q") ?? "");
  useEffect(() => {
    setQuery(new URLSearchParams(search).get("q") ?? "");
  }, [search]);
  function change(key: string, value: string) {
    const next = new URLSearchParams(search);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("cursor");
    navigate("/?" + next.toString());
  }
  const states = [
    "Working",
    "In CI",
    "Stalled",
    "Ready",
    "Blocked",
    "Awaiting approval",
    "Deferred",
    "Complete",
    "Cancelled",
    "Needs attention",
    "Idle",
    "Finished",
    "Closed",
  ];
  return (
    <form
      className="filters"
      onSubmit={(e) => {
        e.preventDefault();
        change("q", query);
      }}
    >
      <label className="search">
        <span aria-hidden="true">⌕</span>
        <input
          aria-label="Search work"
          placeholder="Search work"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onBlur={() => {
            if (query !== (params.get("q") ?? "")) change("q", query);
          }}
        />
      </label>
      <select
        aria-label="Project"
        value={params.get("project") ?? ""}
        onChange={(e) => change("project", e.target.value)}
      >
        <option value="">All projects</option>
        {projects.map((p) => (
          <option key={p.id} value={p.id}>
            {p.id}
          </option>
        ))}
      </select>
      <select
        aria-label="Role"
        value={params.get("role") ?? ""}
        onChange={(e) => change("role", e.target.value)}
      >
        <option value="">All roles</option>
        {roles.map((r) => (
          <option key={r} value={r}>
            {label(r)}
          </option>
        ))}
      </select>
      <select
        aria-label="State"
        value={params.get("state") ?? ""}
        onChange={(e) => change("state", e.target.value)}
      >
        <option value="">All states</option>
        {states.map((s) => (
          <option key={s}>{s}</option>
        ))}
      </select>
      <label className="check">
        <input
          type="checkbox"
          checked={params.has("active")}
          onChange={(e) => change("active", e.target.checked ? "1" : "")}
        />
        Active in window
      </label>
      <label className="check">
        <input
          type="checkbox"
          checked={params.has("older-completed")}
          onChange={(e) =>
            change("older-completed", e.target.checked ? "1" : "")
          }
        />
        Older completed
      </label>
      <button type="submit" className="sr-only">
        Search
      </button>
    </form>
  );
}
function Hotspots({ items }: { items: Feed["hotspots"] }) {
  const [index, setIndex] = useState(0);
  const item = items[index % Math.max(1, items.length)];
  return item ? (
    <section className="hotspot" aria-label="Fleet hotspot">
      <span className="spark" aria-hidden="true">
        ✧
      </span>
      <div className="hotspot-copy">
        <strong>{item.title}</strong>
        <p>
          <Amount value={item.amount_picos} /> in this window ·{" "}
          {item.keys.length} {item.keys.length === 1 ? "card" : "cards"}
        </p>
        <div className="hotspot-links">
          {item.keys.slice(0, 3).map((key) => (
            <Link to={keyPath(key)} key={key}>
              {key.replace(/^(bead|session|ledger):/, "").slice(0, 30)} →
            </Link>
          ))}
          {item.keys.length > 3 && (
            <details>
              <summary>{item.keys.length - 3} more</summary>
              {item.keys.slice(3).map((key) => (
                <Link key={key} to={keyPath(key)}>
                  {key} →
                </Link>
              ))}
            </details>
          )}
        </div>
      </div>
      <div className="carousel">
        <button
          aria-label="Previous hotspot"
          onClick={() => setIndex((index + items.length - 1) % items.length)}
        >
          ←
        </button>
        <span>
          {(index % items.length) + 1} / {items.length}
        </span>
        <button
          aria-label="Next hotspot"
          onClick={() => setIndex((index + 1) % items.length)}
        >
          →
        </button>
      </div>
    </section>
  ) : (
    <div className="hotspot quiet">
      <span className="spark">✧</span>
      <span>No hotspots in this window</span>
    </div>
  );
}
export function FeedView({
  search,
  data,
  health,
  error,
  more,
  loading,
}: {
  search: string;
  data: Feed | null;
  health: Health | null;
  error: string;
  more: () => Promise<void>;
  loading: boolean;
}) {
  const params = new URLSearchParams(search);
  function window(value: string) {
    const next = new URLSearchParams(search);
    next.set("window", value);
    navigate("/?" + next);
  }
  const issues = [
    health?.summaries_error,
    health?.tollgate_error,
    health?.bead_events_error,
    health?.discovery_error,
  ].filter(Boolean);
  return (
    <>
      <header className="page-header">
        <div>
          <div className="eyebrow">Your work, in view</div>
          <h1>Newsfeed</h1>
          <p>Follow the work. Understand the spend.</p>
        </div>
        <span className="freshness">
          {issues.length
            ? "! Some observations unavailable"
            : health?.summaries_behind
              ? "◷ Catching up"
              : "● Live observations"}
        </span>
      </header>
      {error && (
        <div role="alert" className="warning">
          {error}
        </div>
      )}
      {data ? (
        <>
          <section className="summary-strip" aria-label="Spend summary">
            <div>
              <span className="muted">
                Spend ·{" "}
                {data.summary.window === "today"
                  ? "today"
                  : "last " + data.summary.window.replace("d", " days")}
              </span>
              <div className="total">
                <Amount
                  value={data.summary.amount_picos}
                  coverage={BigInt(data.summary.incomplete_picos) > 0n}
                />
              </div>
              <div className="muted">
                {data.summary.unpriced
                  ? `${data.summary.unpriced} requests unpriced`
                  : "Retained request estimates"}{" "}
                · {data.summary.timezone}
              </div>
            </div>
            <div className="summary-roles">
              {[...data.summary.roles]
                .sort((a, b) =>
                  BigInt(a.amount_picos) > BigInt(b.amount_picos) ? -1 : 1,
                )
                .slice(0, 3)
                .map((r) => (
                  <div key={r.role}>
                    <span className="role-label">
                      <Icon role={r.role} small />
                      {label(r.role)}
                    </span>
                    <Amount value={r.amount_picos} />
                  </div>
                ))}
            </div>
            <div className="segmented" aria-label="Time window">
              {[
                ["today", "Today"],
                ["7d", "7 days"],
                ["30d", "30 days"],
              ].map(([value, title]) => (
                <button
                  key={value}
                  aria-pressed={(params.get("window") ?? "7d") === value}
                  onClick={() => window(value ?? "7d")}
                >
                  {title}
                </button>
              ))}
            </div>
          </section>
          <Hotspots items={data.hotspots} />
          <Filters search={search} projects={data.projects} />
          {data.cards.length ? (
            <div className="card-grid">
              {data.cards.map((card) => (
                <WorkCard key={card.key} card={card} />
              ))}
            </div>
          ) : (
            <Empty
              title="No work matches"
              action={
                <button onClick={() => navigate("/")}>Clear filters</button>
              }
            >
              Try another project or clear your filters.
            </Empty>
          )}
          {data.next_cursor && (
            <div className="load-more">
              <button disabled={loading} onClick={() => void more()}>
                {loading ? "Loading…" : "Load more work"}
              </button>
            </div>
          )}
          <p className="footnote">
            Amounts are API-equivalent estimates. ≥ marks incomplete coverage;
            unpriced requests are excluded from totals.
          </p>
        </>
      ) : (
        <Empty
          title={error ? "Observations unavailable" : "Loading your work"}
          action={
            error ? (
              <button onClick={() => location.reload()}>Try again</button>
            ) : undefined
          }
        >
          {error
            ? "The collector has not supplied a readable snapshot yet."
            : "Reading the latest project activity."}
        </Empty>
      )}
    </>
  );
}
