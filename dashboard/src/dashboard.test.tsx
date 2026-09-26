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
import { Disclosure, WorkCard } from "./ui";
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
      <WorkCard card={{ ...card, key: "session:session-a", kind: "tail" }} />,
    );
    expect(screen.getByRole("link")).toHaveAttribute(
      "href",
      "/session/session-a?tail=1",
    );
    expect(screen.getByRole("link")).toHaveTextContent("≥");
  });
  it("keeps filters in the URL and exposes all backing hotspot cards", () => {
    render(<Filters search="?window=today" projects={feed.projects} />);
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
  it("renders five distinct role emblems and an explicit empty state", () => {
    const { container } = render(
      <FeedView
        search=""
        data={{
          ...feed,
          cards: ["executor", "warden", "weaver", "sage", "mason"].map(
            (role) => ({
              ...card,
              key: "session:" + role,
              kind: "agent",
              primary_role: role,
              roles: { [role]: "1" },
            }),
          ),
        }}
        health={null}
        error=""
        more={async () => {}}
        loading={false}
      />,
    );
    expect(
      new Set(
        Array.from(container.querySelectorAll(".emblem svg")).map(
          (svg) => svg.innerHTML,
        ),
      ).size,
    ).toBe(5);
    expect(screen.getByRole("link", { name: "hv-test →" })).toHaveAttribute(
      "href",
      "/bead/hv-test",
    );
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
    expect(
      screen.getByText("Matched by branch", { exact: false }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Attempt 1/)).toBeInTheDocument();
    expect(screen.getByText(/Attempt 2/)).toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("button", { name: "Excerpt" }));
    await screen.findByText("Permission denied");
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
        name: /Repair the collector.*tool errors/,
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
