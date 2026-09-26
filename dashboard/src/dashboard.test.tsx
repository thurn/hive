import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";
import { App } from "./App";
import { DetailView, SafeMarkdown } from "./Detail";
import { Filters, FeedView, useFeed } from "./Feed";
import { Breakdown, Timeline } from "./Timeline";
import { money, when, type Card, type Detail, type Feed } from "./data";
import { request } from "./network";
import { Disclosure } from "./ui";
import { WorkList } from "./WorkList";
import { FeedSummary } from "./FeedSummary";
const at = "2026-09-24T12:00:00Z";
const card: Card = {
  key: "bead:hv-test",
  kind: "bead",
  project: "hive",
  title: "Repair the collector",
  state: "Working",
  amount_picos: "1234567890123456789",
  coverage: 1,
  unpriced: 1,
  last_activity: at,
  roles: { executor: "100", warden: "200" },
  badges: { tool_errors: 2 },
  primary_role: "executor",
  owners: ["session-a"],
};
const feed: Feed = {
  cards: [card],
  projects: [
    { id: "hive", count: 1 },
    { id: "battlement", count: 2 },
  ],
  summary: {
    amount_picos: card.amount_picos,
    incomplete_picos: card.amount_picos,
    unpriced: 1,
    roles: [{ role: "warden", amount_picos: "200" }],
    window: "7d",
    start: at,
    timezone: "America/Los_Angeles",
  },
  hotspots: [
    {
      kind: "error",
      title: "Repeated tool errors",
      amount_picos: "200",
      keys: [card.key],
    },
  ],
  revision: "one",
  next_cursor: null,
  collector: { summaries_behind: false },
};
const detail: Detail = {
  card,
  amount_picos: card.amount_picos,
  timeline: [
    {
      at,
      response: "request-one",
      thread: "session-a",
      role: "warden",
      amount_picos: card.amount_picos,
    },
  ],
  intervals: [],
  context_requests: [],
  breakdown: Object.fromEntries(
    [
      "role",
      "tool",
      "model",
      "host",
      "agent",
      "skill",
      "token_type",
      "hour",
    ].map((key) => [
      key,
      [{ label: key + " segment", amount_picos: card.amount_picos }],
    ]),
  ),
  diagnostics: [
    {
      thread: "session-a",
      host: "codex",
      agent: "",
      call_id: "call-one",
      started_at: at,
      status: "error",
      tool: "exec",
    },
  ],
  candidates: [
    {
      id: "candidate-a",
      state: "promoted",
      branch: "codex/test",
      subject: "Test delivery",
      links: [{ method: "branch", beads: ["hv-test"], thread: "session-a" }],
      attempts: [
        {
          id: "attempt-1",
          number: 1,
          state: "failed",
          started_at: at,
          steps: [
            {
              name: "check",
              exit_code: 1,
              result_class: "failed",
              elapsed_ms: 4000,
            },
          ],
        },
        {
          id: "attempt-2",
          number: 2,
          state: "passed",
          started_at: at,
          steps: [
            {
              name: "check",
              exit_code: 0,
              result_class: "passed",
              elapsed_ms: 3000,
            },
          ],
        },
      ],
    },
  ],
  role_spans: [],
  subagents: [],
  created_beads: [],
  contributors: [],
};
function response(value: unknown, headers: Record<string, string> = {}) {
  return new Response(
    JSON.stringify({
      code: "Dashboard",
      schema: 1,
      ...z.record(z.string(), z.unknown()).parse(value),
    }),
    { headers },
  );
}
beforeEach(() => {
  history.replaceState(null, "", "/");
  sessionStorage.clear();
  vi.stubGlobal("scrollTo", vi.fn());
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
describe("work presentation", () => {
  it("preserves large integer costs, coverage and tail navigation", () => {
    expect(money(card.amount_picos, 6)).toBe("$1,234,567.890123");
    render(
      <WorkList
        cards={[{ ...card, key: "session:session-a", kind: "tail" }]}
      />,
    );
    expect(screen.getByRole("link")).toHaveAttribute(
      "href",
      "/session/session-a?tail=1",
    );
    expect(screen.getAllByRole("row")[1]).toHaveTextContent("≥");
  });
  it("keeps filters in the URL and exposes all backing hotspot cards", () => {
    render(<Filters search="?window=today" projects={feed.projects} />);
    fireEvent.click(screen.getByRole("button", { name: "Filters" }));
    fireEvent.change(screen.getByLabelText("Project"), {
      target: { value: "battlement" },
    });
    expect(new URLSearchParams(location.search).get("window")).toBe("today");
    expect(new URLSearchParams(location.search).get("project")).toBe(
      "battlement",
    );
    fireEvent.change(screen.getByLabelText("Search work"), {
      target: { value: "a & b" },
    });
    fireEvent.submit(screen.getByLabelText("Search work").closest("form")!);
    expect(new URLSearchParams(location.search).get("q")).toBe("a & b");
  });
  it("keeps all work kinds, states and exact lifetime amounts in title-linked rows", () => {
    const kinds = ["bead", "agent", "tail", "small_tails", "unattributable"];
    const keys = [
      "bead:hv-test",
      "session:a",
      "session:b",
      "ledger:small_tails:hive",
      "ledger:unattributable:Other",
    ];
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
    render(
      <WorkList
        cards={states.map((state, i) => ({
          ...card,
          key: `${keys[i % 5] ?? "bead:missing"}${i}`,
          kind: kinds[i % 5] ?? "bead",
          title: `Full title ${i} ` + "important words ".repeat(20),
          state,
        }))}
      />,
    );
    expect(screen.getAllByRole("row")).toHaveLength(14);
    for (const state of states) expect(screen.getByText(state)).toBeVisible();
    expect(screen.getAllByText("Agent session").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Unowned tail").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Small tails ledger").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Unattributable ledger").length).toBeGreaterThan(
      0,
    );
    expect(
      screen.getAllByTitle(card.amount_picos + " picodollars"),
    ).toHaveLength(13);
    expect(screen.getAllByRole("link")[0]).toHaveTextContent(
      "important words ".repeat(20).trim(),
    );
    expect(
      screen.getByRole("columnheader", { name: "Lifetime spend" }),
    ).toBeVisible();
  });
  it("reveals every window role and all ranked findings without duplicating coverage", () => {
    const roles = ["executor", "warden", "weaver", "sage", "mason", "unknown"];
    render(
      <FeedSummary
        search="?project=hive&role=warden&q=repair&state=Working"
        data={{
          ...feed,
          summary: {
            ...feed.summary,
            roles: roles.map((role, i) => ({
              role,
              amount_picos: String(i * 1000),
            })),
          },
          hotspots: [
            {
              kind: "coverage",
              title: "Partial window",
              amount_picos: "100",
              keys: ["bead:hv-a", "bead:hv-b", "bead:hv-c", "bead:hv-d"],
            },
            ...feed.hotspots,
          ],
        }}
      />,
    );
    expect(screen.queryByText("Partial window")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Spend by role" }));
    for (const role of [
      "Executor",
      "Warden",
      "Weaver",
      "Sage",
      "Mason",
      "Unknown",
    ])
      expect(screen.getByText(role)).toBeVisible();
    expect(screen.getByText(/Search, state, activity/)).toHaveTextContent(
      "not these window totals",
    );
    expect(screen.getByText(/Selected window:/)).toHaveTextContent(
      "Project: hive; request role: Warden",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Coverage incomplete" }),
    );
    expect(screen.getByText("Partial window")).toBeVisible();
    expect(screen.getByRole("link", { name: "bead:hv-d" })).toHaveAttribute(
      "href",
      "/bead/hv-d",
    );
    fireEvent.click(screen.getByRole("button", { name: "Issues (1)" }));
    expect(screen.getByText("Repeated tool errors")).toBeVisible();
    expect(screen.getAllByText("Partial window")).toHaveLength(1);
  });
  it("keeps partial zero explicit and omits an empty issues action", () => {
    render(
      <FeedSummary
        search=""
        data={{
          ...feed,
          summary: {
            ...feed.summary,
            amount_picos: "0",
            incomplete_picos: "0",
            unpriced: 2,
            roles: [],
          },
          hotspots: [],
        }}
      />,
    );
    expect(screen.getByText("≥ $0.00")).toBeVisible();
    expect(screen.queryByRole("button", { name: /Issues/ })).toBeNull();
    fireEvent.click(
      screen.getByRole("button", { name: "Coverage incomplete" }),
    );
    expect(screen.getByText(/2 requests unpriced/)).toBeVisible();
  });
  it("never turns hostile markdown into live HTML, scripts, images or javascript links", () => {
    const { container } = render(
      <SafeMarkdown
        value={
          "<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[attack](javascript:alert(1))\n\n![remote](https://example.org/track)\n\n[Safe](https://example.org)\n\n**Good**"
        }
      />,
    );
    expect(
      container.querySelector('script,img,[onerror],a[href^="javascript:"]'),
    ).toBeNull();
    expect(screen.getByText("Good").tagName).toBe("STRONG");
    expect(screen.getByRole("link", { name: "Safe" })).toHaveAttribute(
      "rel",
      "noopener noreferrer",
    );
  });
  it("switches all exact breakdowns and exposes range controls and diagnostic sources", () => {
    const onRange = vi.fn(),
      onDiagnostic = vi.fn();
    render(
      <>
        <Timeline
          detail={detail}
          range={null}
          onRange={onRange}
          onDiagnostic={onDiagnostic}
        />
        <Breakdown detail={detail} />
      </>,
    );
    for (const dimension of Object.keys(detail.breakdown)) {
      fireEvent.change(screen.getByLabelText("Breakdown dimension"), {
        target: { value: dimension },
      });
      expect(screen.getByText(dimension + " segment")).toBeInTheDocument();
    }
    fireEvent.change(screen.getByLabelText("Range start"), {
      target: { value: "2026-09-24T12:00" },
    });
    expect(onRange).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: /Tool error at/ }));
    expect(onDiagnostic).toHaveBeenCalledWith(detail.diagnostics[0]);
  });
});
it("keeps post-request evidence inside a finished timeline", () => {
  const late = "2026-09-24T14:00:00Z";
  render(
    <Timeline
      detail={{
        ...detail,
        card: { ...card, state: "Finished" },
        diagnostics: [
          {
            thread: "session-a",
            agent: "",
            host: "claude",
            kind: "human_wait",
            at,
            until: late,
          },
        ],
      }}
      range={null}
      onRange={() => {}}
      onDiagnostic={() => {}}
    />,
  );
  expect(
    screen.getByRole("img", {
      name: "Cumulative spend, ownership intervals and diagnostics",
    }),
  ).toHaveTextContent(when(late));
});
describe("read-only data journeys", () => {
  it("loads, pages and sorts requests, filters by time, reads excerpts and both CI attempts", async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/excerpt"))
        return response({
          input_summary: "command",
          result: "Permission denied",
        });
      if (url.includes("/ci-log"))
        return response({ tail: "Failed assertion" });
      if (url.includes("requests=1")) {
        const query = new URL(url, "http://localhost").searchParams;
        return response({
          requests: [
            {
              response: query.has("cursor") ? "request-two" : "request-one",
              observed_at: at,
              share_picos: "1000000000000",
            },
          ],
          next_cursor: query.has("cursor") ? null : "next",
        });
      }
      return response(detail);
    });
    vi.stubGlobal("fetch", fetcher);
    render(<DetailView path="/bead/hv-test" search="" />);
    await screen.findByRole("heading", { name: card.title });
    fireEvent.click(screen.getByRole("tab", { name: "Delivery" }));
    expect(
      screen.getByText("Matched by branch", { exact: false }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Attempt 1/)).toBeInTheDocument();
    expect(screen.getByText(/Attempt 2/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Overview" }));
    fireEvent.click(screen.getByRole("button", { name: "View requests" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Load more requests" }),
    );
    await screen.findByText("request-two…");
    fireEvent.change(screen.getByLabelText("Request sort"), {
      target: { value: "share" },
    });
    await waitFor(() =>
      expect(
        fetcher.mock.calls.some(([url]) => String(url).includes("sort=share")),
      ).toBe(true),
    );
    fireEvent.change(screen.getByLabelText("Range start"), {
      target: { value: "2026-09-24T11:00" },
    });
    await waitFor(() =>
      expect(
        fetcher.mock.calls.some(([url]) => String(url).includes("since=")),
      ).toBe(true),
    );
    fireEvent.click(screen.getByRole("button", { name: "Clear range" }));
    fireEvent.click(screen.getByRole("tab", { name: "Diagnostics" }));
    fireEvent.click(screen.getByRole("button", { name: "Excerpt" }));
    await screen.findByText("Permission denied");
    fireEvent.click(screen.getByRole("tab", { name: "Delivery" }));
    fireEvent.click(screen.getAllByRole("button", { name: "Log tail" })[0]!);
    await screen.findByText("Failed assertion");
    fireEvent.click(screen.getByRole("button", { name: "Close excerpt" }));
    expect(screen.queryByText("Failed assertion")).toBeNull();
  });
  it("refreshes every loaded page and never keeps stale cards after a revision", async () => {
    let revision = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/status") return response({ summaries_behind: false });
        if (url.includes("cursor="))
          return response({
            ...feed,
            cards: [{ ...card, key: revision ? "bead:new" : "bead:stale" }],
            next_cursor: null,
          });
        return response(
          {
            ...feed,
            cards: [card],
            next_cursor: "page-two",
            revision: String(revision),
          },
          { ETag: String(revision) },
        );
      }),
    );
    function Harness() {
      const data = useFeed("");
      return <FeedView search="" {...data} />;
    }
    render(<Harness />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Load more work" }),
    );
    await waitFor(() =>
      expect(document.querySelector('a[href="/bead/stale"]')).not.toBeNull(),
    );
    revision = 1;
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() =>
      expect(document.querySelector('a[href="/bead/new"]')).not.toBeNull(),
    );
    expect(document.querySelector('a[href="/bead/stale"]')).toBeNull();
  });
  it("accepts ETag 304 and stops rendering an unsupported schema", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 304 }))
      .mockImplementation(async () => response({ schema: 2 }));
    vi.stubGlobal("fetch", fetcher);
    expect(
      await request("/api/feed", z.object({}), undefined, "etag"),
    ).toBeNull();
    expect(fetcher).toHaveBeenCalledWith(
      "/api/feed",
      expect.objectContaining({ headers: { "If-None-Match": "etag" } }),
    );
    render(<App />);
    await screen.findByRole("button", { name: "New version — reload" });
    expect(screen.queryByRole("heading", { name: "Newsfeed" })).toBeNull();
  });
});

it("shows descendant progress without replacing native epic state or direct spend", async () => {
  const epicDetail = {
    ...detail,
    card: { ...card, state: "Ready", amount_picos: "1000000000000" },
    epic: {
      native_status: "open",
      scope: "All reachable descendants; excludes this epic",
      classification: "Explicit work kinds and retained task conventions",
      groups: [
        { category: "implementation", total: 4, completed: 1 },
        { category: "review", total: 1, completed: 1 },
      ],
      active: 2,
      in_ci: 1,
      blocked: 1,
      held: 1,
      owners: ["same-owner"],
      amount_picos: "2000000000000",
      unpriced: 1,
      incomplete: 1,
      missing_costs: 0,
      refreshed: at,
      collector: {
        summaries_behind: false,
        tollgate_behind: false,
        tollgate_refreshed: at,
        registry: { refreshed: at, error: "beads_unavailable" },
      },
      members: [
        {
          bead: "hv-child",
          title: "Active child",
          category: "implementation",
          direct_child: true,
          current: true,
          native_status: "in_progress",
          state: "In CI",
          completed: false,
          cancelled: false,
          active: true,
          owner: "same-owner",
          blockers: ["hv-cancelled"],
          held: false,
          in_ci: true,
          ci_failed: false,
        },
      ],
    },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) =>
      response(
        String(input).includes("requests=1")
          ? { requests: [], next_cursor: null }
          : epicDetail,
      ),
    ),
  );
  render(<DetailView path="/bead/hv-test" search="" />);
  await screen.findByRole("heading", { name: "Epic progress" });
  expect(screen.getByText("Ready")).toBeInTheDocument();
  expect(
    screen.getByText(/2 active descendants · 1 distinct assigned owners/),
  ).toBeInTheDocument();
  expect(screen.getByText("1 / 4 completed")).toBeInTheDocument();
  expect(screen.getByText(/Direct epic lifetime spend:/)).toHaveTextContent(
    "$1.00 · Descendant lifetime spend: ≥ $2.00",
  );
  expect(screen.getByText(/Cached descendant observation:/)).toHaveTextContent(
    "Collection delayed or unavailable",
  );
  fireEvent.click(screen.getByText("Descendant work (1)"));
  expect(screen.getByRole("link", { name: "Active child" })).toHaveAttribute(
    "href",
    "/bead/hv-child",
  );
  expect(screen.getByRole("link", { name: "hv-cancelled" })).toHaveAttribute(
    "href",
    "/bead/hv-cancelled",
  );
});

describe("shell navigation", () => {
  function serve() {
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/status")) return response({});
      if (url.includes("requests=1"))
        return response({ requests: [], next_cursor: null });
      if (!url.startsWith("/api/feed")) return response(detail);
      const query = new URL(url, location.origin).searchParams;
      return response({
        ...feed,
        cards: query.has("cursor")
          ? [{ ...card, key: "bead:second", title: "Second page work" }]
          : [card],
        next_cursor: query.has("cursor") ? null : "page-two",
      });
    });
    vi.stubGlobal("fetch", fetcher);
    return fetcher;
  }
  it("keeps the project feed stable while switching detail views", async () => {
    const fetcher = serve();
    history.replaceState(null, "", "/bead/hv-test");
    render(<App />);
    await screen.findByRole("tab", { name: "Diagnostics" });
    await screen.findByRole("link", { name: "battlement" });
    fireEvent.click(screen.getByRole("tab", { name: "Diagnostics" }));
    fireEvent.click(screen.getByRole("tab", { name: "Delivery" }));
    fireEvent.click(screen.getByRole("tab", { name: "Overview" }));
    expect(
      fetcher.mock.calls.filter(([url]) => String(url).startsWith("/api/feed")),
    ).toHaveLength(1);
    expect(
      fetcher.mock.calls.filter(([url]) => String(url).startsWith("/api/bead/")),
    ).toHaveLength(1);
  });
  it("restores loaded extent before scroll after a detail reload and keeps project scope", async () => {
    const fetcher = serve();
    history.replaceState(null, "", "/?window=today&project=hive&q=collector");
    const first = render(<App />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Load more work" }),
    );
    const second = await screen.findByRole("link", {
      name: /Second page work/,
    });
    vi.stubGlobal("scrollY", 900);
    fireEvent.scroll(window);
    fireEvent.click(second);
    await screen.findByRole("heading", { name: "Repair the collector" });
    expect(screen.getByRole("link", { name: "hive" })).toHaveClass("selected");
    expect(
      screen.getByRole("link", { name: "← Back to newsfeed" }),
    ).toHaveAttribute("href", "/?project=hive&window=today&q=collector");
    first.unmount();
    vi.stubGlobal("scrollY", 0);
    fetcher.mockClear();
    const scroll = vi.fn();
    vi.stubGlobal("scrollTo", scroll);
    render(<App />);
    fireEvent.click(
      await screen.findByRole("link", { name: "← Back to newsfeed" }),
    );
    await screen.findByRole("link", { name: /Second page work/ });
    await waitFor(() => expect(scroll).toHaveBeenCalledWith(0, 900));
    expect(
      fetcher.mock.calls.some(([url]) =>
        String(url).includes("cursor=page-two"),
      ),
    ).toBe(true);
    expect(location.search).toBe("?project=hive&window=today&q=collector");
  });
  it("preserves independent feed entries through browser back and forward", async () => {
    serve();
    render(<App />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Load more work" }),
    );
    await screen.findByRole("link", { name: /Second page work/ });
    vi.stubGlobal("scrollY", 600);
    fireEvent.scroll(window);
    fireEvent.click(screen.getByRole("link", { name: "battlement" }));
    await screen.findByRole("button", { name: "Load more work" });
    expect(screen.queryByRole("link", { name: /Second page work/ })).toBeNull();
    vi.stubGlobal("scrollY", 0);
    fireEvent.scroll(window);
    act(() => history.back());
    await screen.findByRole("link", { name: /Second page work/ });
    await waitFor(() => expect(window.scrollTo).toHaveBeenCalledWith(0, 600));
    act(() => history.forward());
    await waitFor(() => expect(location.search).toBe("?project=battlement"));
    await screen.findByRole("button", { name: "Load more work" });
    expect(screen.queryByRole("link", { name: /Second page work/ })).toBeNull();
  });
  it("restores separate loaded extents for entries with identical filters", async () => {
    serve();
    render(<App />);
    fireEvent.click(
      await screen.findByRole("link", {
        name: "Repair the collector",
      }),
    );
    fireEvent.click(
      await screen.findByRole("link", { name: "← Back to newsfeed" }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Load more work" }),
    );
    await screen.findByRole("link", { name: /Second page work/ });
    act(() => history.back());
    await screen.findByRole("heading", { name: "Repair the collector" });
    act(() => history.back());
    await screen.findByRole("button", { name: "Load more work" });
    expect(screen.queryByRole("link", { name: /Second page work/ })).toBeNull();
  });
  it.each(["/bead/hv-test", "/session/session-a", "/ledger/unowned/hive"])(
    "uses a safe feed fallback on direct %s routes",
    async (path) => {
      serve();
      history.replaceState(
        {
          id: "hostile",
          scroll: 0,
          feed: { search: "//evil.example", count: 50, scroll: 700 },
        },
        "",
        path,
      );
      sessionStorage.setItem("hive-feed-url", "https://evil.example");
      render(<App />);
      expect(
        await screen.findByRole("link", { name: "← Back to newsfeed" }),
      ).toHaveAttribute("href", "/");
      await screen.findByRole("heading", { name: "Repair the collector" });
      const rail = document.querySelector(".rail");
      expect(rail).not.toHaveTextContent("2");
      expect(rail).toHaveTextContent("Local observation");
    },
  );
  it("preserves query scope in project links and makes disclosures keyboard reversible", async () => {
    serve();
    history.replaceState(null, "", "/?window=today&role=warden&project=hive");
    const app = render(<App />);
    expect(
      await screen.findByRole("link", { name: "battlement" }),
    ).toHaveAttribute("href", "/?project=battlement&window=today&role=warden");
    app.unmount();
    render(
      <Disclosure title="Inspect evidence">
        <button>Inner action</button>
      </Disclosure>,
    );
    const trigger = screen.getByRole("button", { name: "Inspect evidence" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(trigger);
    const body = document.getElementById(
      trigger.getAttribute("aria-controls") ?? "",
    );
    expect(body).not.toBeNull();
    if (!body) throw new Error("Missing disclosure content");
    const action = within(body).getByRole("button", { name: "Inner action" });
    action.focus();
    fireEvent.keyDown(action, { key: "Escape" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveFocus();
    expect(screen.queryByRole("button", { name: "Inner action" })).toBeNull();
  });
});

it("counts and resets all filters while preserving the selected window", () => {
  const search =
    "?window=30d&project=hive&role=warden&state=Complete&active=1&older-completed=1&q=needle&cursor=old";
  history.replaceState(null, "", "/" + search);
  render(<Filters search={search} projects={feed.projects} />);
  fireEvent.click(screen.getByRole("button", { name: "Filters (6)" }));
  expect(screen.getByLabelText("Older completed")).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Reset filters" }));
  expect(location.search).toBe("?window=30d");
  expect(screen.getByLabelText("Search work")).toHaveValue("");
});
it("keeps stale readable rows, empty matches and initial errors distinct", () => {
  const base = {
    search: "",
    health: null,
    more: async () => {},
    loading: false,
  };
  const view = render(
    <FeedView {...base} data={feed} error="Refresh unavailable" />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent("Refresh unavailable");
  expect(screen.getByRole("link", { name: card.title })).toBeVisible();
  view.rerender(<FeedView {...base} data={{ ...feed, cards: [] }} error="" />);
  expect(
    screen.getByRole("heading", { name: "No work matches" }),
  ).toBeVisible();
  view.rerender(<FeedView {...base} data={null} error="Initial read failed" />);
  expect(
    screen.getByRole("heading", { name: "Observations unavailable" }),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: "Try again" })).toBeVisible();
});

it("keeps filter disclosure and scope while the next query is loading", () => {
  const base = {
    health: null,
    error: "",
    more: async () => {},
    loading: false,
  };
  const view = render(
    <FeedView {...base} search="?project=hive" data={feed} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Filters (1)" }));
  view.rerender(
    <FeedView {...base} search="?project=hive&role=warden" data={null} />,
  );
  expect(screen.getByRole("button", { name: "Filters (2)" })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  expect(screen.getByLabelText("Project")).toHaveValue("hive");
  expect(screen.getByLabelText("Role")).toHaveValue("warden");
  view.rerender(
    <FeedView {...base} search="?project=hive&role=warden" data={feed} />,
  );
  expect(screen.getByRole("button", { name: "Filters (2)" })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
});

describe("focused detail workspace", () => {
  it("keeps all candidate states visible without rewriting native completion", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        response({
          ...detail,
          candidates: [
            ...detail.candidates,
            {
              ...detail.candidates[0],
              id: "failed-candidate",
              state: "failed",
              attempts: [],
            },
          ],
        }),
      ),
    );
    render(<DetailView path="/bead/hv-test" search="" />);
    await screen.findByRole("heading", { name: card.title });
    expect(screen.getByText("1 Promoted")).toBeVisible();
    expect(screen.getByText("1 Failed")).toBeVisible();
    expect(screen.getByText("Working")).toBeVisible();
    expect(screen.queryByLabelText("Request sort")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "View delivery" }));
    expect(screen.getByText("Failed", { selector: "span.pill" })).toBeVisible();
  });
  it("reloads UI view and range without leaking UI keys to APIs", async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) =>
      String(input).includes("requests=1")
        ? response({ requests: [], next_cursor: null })
        : response(detail),
    );
    vi.stubGlobal("fetch", fetcher);
    const search =
      "?tail=1&view=delivery&since=2026-09-24T13%3A00%3A00Z&until=2026-09-24T14%3A00%3A00Z&section=requests";
    const view = render(
      <DetailView path="/session/session-a" search={search} />,
    );
    await screen.findByRole("heading", { name: card.title });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/session/session-a?tail=1",
      expect.anything(),
    );
    expect(screen.getByRole("tab", { name: "Delivery" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      screen.getByText(/1 candidates and 2 attempts hidden/),
    ).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent(
      "totals remain lifetime",
    );
    fireEvent.click(screen.getByRole("tab", { name: "Overview" }));
    await screen.findByText("No requests in this range.");
    expect(
      fetcher.mock.calls.some(
        ([url]) =>
          String(url).includes("requests=1") && String(url).includes("since="),
      ),
    ).toBe(true);
    expect(
      fetcher.mock.calls.every(
        ([url]) => !/[?&](view|section|kind)=/.test(String(url)),
      ),
    ).toBe(true);
    const saved = location.search;
    view.unmount();
    render(<DetailView path="/session/session-a" search={saved} />);
    await screen.findByText("No requests in this range.");
    expect(
      screen.getByRole("button", { name: "View requests" }),
    ).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(screen.getByRole("button", { name: "Clear range" }));
    expect(new URLSearchParams(location.search).has("since")).toBe(false);
    expect(
      within(
        screen.getByRole("region", { name: "Recorded lifetime spend" }),
      ).getByTitle(detail.amount_picos + " picodollars"),
    ).toHaveTextContent("≥");
  });
  it("falls back from invalid view state and keeps unknown-time diagnostics reachable", async () => {
    const fetcher = vi.fn(async () =>
      response({
        ...detail,
        diagnostics: [
          { thread: "session-a", host: "codex", agent: "", kind: "api_error" },
        ],
      }),
    );
    vi.stubGlobal("fetch", fetcher);
    render(
      <DetailView
        path="/bead/hv-test"
        search="?view=evil&section=evil&kind=%3Cscript%3E&since=oops"
      />,
    );
    await screen.findByRole("heading", { name: card.title });
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Invalid range");
    fireEvent.click(screen.getByRole("tab", { name: "Diagnostics" }));
    expect(screen.getByText(/Unknown time · Api error/)).toBeVisible();
    expect(fetcher.mock.calls).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Excerpt" }));
    expect(
      screen.getByText("No independently verifiable source excerpt."),
    ).toBeVisible();
    expect(fetcher.mock.calls).toHaveLength(1);
  });
  it("provides truthful session actions and keyboard-reversible supporting evidence", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        response({
          ...detail,
          card: { ...card, owners: ["a", "b"] },
          contributors: [
            { thread: "a", amount_picos: "100", coverage: 0 },
            { thread: "b", amount_picos: "200", coverage: 1 },
          ],
          role_spans: [
            {
              thread: "a",
              agent: "child",
              role: "warden",
              start: at,
              inherited: 1,
              evidence: "parent request",
            },
          ],
          subagents: [{ label: "child", amount_picos: "300" }],
        }),
      ),
    );
    render(<DetailView path="/bead/hv-test" search="" />);
    await screen.findByRole("heading", { name: card.title });
    expect(screen.queryByRole("link", { name: "Open session" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Sessions (2)" }));
    expect(screen.getByRole("link", { name: "a" })).toHaveAttribute(
      "href",
      "/session/a",
    );
    expect(screen.getByRole("link", { name: "b" })).toHaveAttribute(
      "href",
      "/session/b",
    );
    expect(screen.getByText(/inherited/)).toBeVisible();
    fireEvent.keyDown(screen.getByRole("button", { name: "Close section" }), {
      key: "Escape",
    });
    expect(screen.getByRole("button", { name: "Sessions" })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole("tab", { name: "Overview" }), {
      key: "ArrowRight",
    });
    expect(screen.getByRole("tab", { name: "Diagnostics" })).toHaveFocus();
    expect(screen.getByRole("tab", { name: "Diagnostics" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});

it.each([
  ["", "agent", "Whole session · includes owned and unowned requests"],
  ["?tail=1", "tail", "Unowned tail · excludes owned request shares"],
])("keeps session scope explicit for %s", async (search, kind, scope) => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      response({
        ...detail,
        card: {
          ...card,
          key: "session:session-a",
          kind,
          thread: "session-a",
          subtitle:
            kind === "agent"
              ? "Whole session · includes owned and unowned requests"
              : "Unowned tail",
        },
      }),
    ),
  );
  render(<DetailView path="/session/session-a" search={search} />);
  expect(await screen.findByText(scope)).toBeVisible();
  if (kind === "tail")
    expect(
      screen.getByRole("link", { name: "View whole session" }),
    ).toHaveAttribute("href", "/session/session-a");
});
it("retains cached native task evidence, commands and sanitized text", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      response({
        ...detail,
        beads: {
          source: "cached",
          refreshed: at,
          age_seconds: 90,
          error: "Beads offline",
          bead: {
            status: "in_progress",
            description: "**Full task text**",
            acceptance_criteria: "Acceptance text",
            notes: "Notes text",
            metadata: { hive_project: "hive" },
            dependencies: [{ id: "hv-parent" }],
          },
          commands: { show: "bd show hv-test" },
          comments: [{ id: "one", text: "Comment evidence" }],
          dependents: [{ id: "hv-child", title: "Child" }],
          events: [{ id: "e", event_type: "claimed" }],
        },
      }),
    ),
  );
  render(<DetailView path="/bead/hv-test" search="" />);
  await screen.findByRole("heading", { name: card.title });
  fireEvent.click(
    screen.getByRole("button", { name: "View task description" }),
  );
  expect(
    screen.getByText(/Showing the last collected record/),
  ).toHaveTextContent("1.5 min old");
  expect(screen.getByRole("alert")).toHaveTextContent("Beads offline");
  expect(screen.getByText("Full task text")).toBeVisible();
  fireEvent.click(screen.getByText("Copy a native command"));
  expect(screen.getByRole("button", { name: "Copy show" })).toBeVisible();
  fireEvent.click(screen.getByText("Comments (1)"));
  expect(screen.getByText("Comment evidence")).toBeVisible();
});
it("keeps unavailable logs explicit and cancels source reads on task change", async () => {
  const source: {
    finish?: (value: Response) => void;
    signal?: AbortSignal | null;
  } = {};
  const fetcher = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes("/excerpt")) {
        source.signal = init?.signal;
        return new Promise<Response>((resolve) => {
          source.finish = resolve;
        });
      }
      if (String(input).includes("/ci-log"))
        throw new Error("Retained log unavailable");
      return response(detail);
    },
  );
  vi.stubGlobal("fetch", fetcher);
  const view = render(
    <DetailView path="/bead/hv-test" search="?view=delivery" />,
  );
  await screen.findByRole("heading", { name: card.title });
  fireEvent.click(screen.getAllByRole("button", { name: "Log tail" })[0]!);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Retained log unavailable",
  );
  fireEvent.click(screen.getByRole("button", { name: "Close excerpt" }));
  fireEvent.click(screen.getByRole("tab", { name: "Diagnostics" }));
  fireEvent.click(screen.getByRole("button", { name: "Excerpt" }));
  await screen.findByText("Reading source…");
  view.rerender(<DetailView path="/bead/hv-other" search="" />);
  expect(source.signal?.aborted).toBe(true);
  await act(async () =>
    source.finish?.(response({ result: "Stale source must not appear" })),
  );
  expect(screen.queryByText("Stale source must not appear")).toBeNull();
});

it.each([[], ["one-source"]])(
  "offers a session link only for a known single destination: %j",
  async (...owners) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        response({ ...detail, card: { ...card, owners }, contributors: [] }),
      ),
    );
    render(<DetailView path="/bead/hv-test" search="" />);
    await screen.findByRole("heading", { name: card.title });
    if (owners.length === 1)
      expect(
        screen.getByRole("link", { name: "Open session" }),
      ).toHaveAttribute("href", "/session/one-source");
    else {
      expect(screen.queryByRole("link", { name: "Open session" })).toBeNull();
      expect(screen.getByText("No observed session source")).toBeVisible();
    }
  },
);
