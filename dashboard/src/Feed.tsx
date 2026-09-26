import { useEffect, useRef, useState } from "react";
import {
  FeedSchema,
  HealthSchema,
  label,
  type Feed,
  type Health,
} from "./data";
import { rememberExtent } from "./navigation";
import { request } from "./network";
import { Disclosure, Empty, navigate, roles } from "./ui";
import { FeedSummary } from "./FeedSummary";
import { WorkList } from "./WorkList";

export function useFeed(search: string, restoreCount = 0, entryKey = "") {
  const [data, setData] = useState<Feed | null>(null),
    [health, setHealth] = useState<Health | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false);
  const saved = useRef<{
    search: string;
    key: string;
    data: Feed;
    etag: string | null;
  } | null>(null);
  const query = new URLSearchParams(search).toString();
  useEffect(() => {
    const controller = new AbortController();
    let busy = false;
    if (saved.current?.search !== query || saved.current?.key !== entryKey) {
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
          const count = Math.max(
            restoreCount,
            saved.current?.data.cards.length ?? 0,
          );
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
          saved.current = {
            search: query,
            key: entryKey,
            data: value,
            etag: next.etag,
          };
          rememberExtent(search, value.cards.length);
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
  }, [query, restoreCount, search, entryKey]);
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
        rememberExtent(search, value.cards.length);
        setData(value);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load more work");
    } finally {
      setLoading(false);
    }
  }
  return {
    data:
      saved.current?.search === query && saved.current?.key === entryKey
        ? data
        : null,
    health,
    error,
    more,
    loading,
  };
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
  const filterKeys = [
    "q",
    "project",
    "role",
    "state",
    "active",
    "older-completed",
  ];
  const count = filterKeys.filter((key) => params.has(key)).length;
  function reset() {
    const next = new URLSearchParams(search);
    for (const key of [...filterKeys, "cursor"]) next.delete(key);
    setQuery("");
    navigate("/?" + next.toString());
  }
  return (
    <form
      className="work-toolbar"
      onSubmit={(e) => {
        e.preventDefault();
        change("q", query);
      }}
    >
      <h2>All work</h2>
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
      <Disclosure title={count ? `Filters (${count})` : "Filters"}>
        <div className="filter-fields">
          <select
            aria-label="Project"
            value={params.get("project") ?? ""}
            onChange={(e) => change("project", e.target.value)}
          >
            <option value="">All projects</option>
            {params.get("project") &&
              !projects.some((p) => p.id === params.get("project")) && (
                <option value={params.get("project") ?? ""}>
                  {params.get("project")}
                </option>
              )}
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
          <button type="button" onClick={reset}>
            Reset filters
          </button>
        </div>
      </Disclosure>
      <button type="submit" className="sr-only">
        Search
      </button>
    </form>
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
    next.delete("cursor");
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
          <h1>Newsfeed</h1>
        </div>
        <div className="segmented" aria-label="Time window">
          {[
            ["today", "Today"],
            ["7d", "Last 7 days"],
            ["30d", "Last 30 days"],
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
      </header>
      <p className="feed-freshness">
        {issues.length
          ? "! Some observations unavailable"
          : health?.summaries_behind
            ? "◷ Catching up"
            : "● Live observations"}
      </p>
      {error && (
        <div role="alert" className="warning">
          {error}
        </div>
      )}
      {data ? (
        <FeedSummary data={data} search={search} />
      ) : (
        <div className="summary-loading" role="status">
          {error ? "Recorded spend unavailable" : "Loading recorded spend…"}
        </div>
      )}
      <Filters search={search} projects={data?.projects ?? []} />
      {data ? (
        <>
          {data.cards.length ? (
            <WorkList cards={data.cards} />
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
