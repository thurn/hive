import { z } from "zod";

const FeedPosition = z
  .object({
    search: z.string().max(4096),
    count: z.number().int().nonnegative().max(100000),
    scroll: z.number().finite().nonnegative(),
  })
  .readonly();
const Entry = z
  .object({
    id: z.string(),
    scroll: z.number().finite().nonnegative(),
    feed: FeedPosition,
  })
  .readonly();
export type NavigationEntry = Readonly<z.infer<typeof Entry>>;
const queryKeys = [
  "project",
  "window",
  "q",
  "role",
  "state",
  "active",
  "older-completed",
];
export function feedSearch(search: string): string {
  const source = new URLSearchParams(search),
    result = new URLSearchParams();
  for (const key of queryKeys) {
    const value = source.get(key);
    if (value) result.set(key, value);
  }
  const value = result.toString();
  return value ? "?" + value : "";
}
export function entry(): NavigationEntry {
  const state: unknown = history.state;
  const parsed = Entry.safeParse(state);
  if (
    parsed.success &&
    parsed.data.feed.search === feedSearch(parsed.data.feed.search)
  )
    return parsed.data;
  return {
    id: "",
    scroll: 0,
    feed: {
      search: location.pathname === "/" ? feedSearch(location.search) : "",
      count: 0,
      scroll: 0,
    },
  };
}
export function snapshot(): string {
  return location.pathname + location.search + "#" + entry().id;
}
export function saveScroll() {
  const current = entry();
  history.replaceState(
    {
      ...current,
      scroll: Math.max(0, window.scrollY),
      feed:
        location.pathname === "/"
          ? { ...current.feed, scroll: Math.max(0, window.scrollY) }
          : current.feed,
    },
    "",
  );
}
export function rememberExtent(search: string, count: number) {
  if (location.pathname !== "/" || feedSearch(location.search) !== search)
    return;
  const current = entry();
  history.replaceState(
    { ...current, feed: { ...current.feed, search, count } },
    "",
  );
}
export function subscribe(callback: () => void) {
  const previous = history.scrollRestoration;
  history.scrollRestoration = "manual";
  window.addEventListener("scroll", saveScroll, { passive: true });
  window.addEventListener("pagehide", saveScroll);
  window.addEventListener("popstate", callback);
  return () => {
    history.scrollRestoration = previous;
    window.removeEventListener("scroll", saveScroll);
    window.removeEventListener("pagehide", saveScroll);
    window.removeEventListener("popstate", callback);
  };
}
export function navigate(path: string) {
  // Only same-app paths can enter route history; feed return state contains no URL.
  if (
    !/^\/(?:$|\?|(?:bead|session|ledger)\/[^/]+)/.test(path) ||
    path.startsWith("//")
  )
    return;
  const url = new URL(path, location.origin);
  if (url.origin !== location.origin) return;
  saveScroll();
  const current = entry();
  const search = feedSearch(url.search);
  const returning =
    location.pathname !== "/" &&
    url.pathname === "/" &&
    search === current.feed.search;
  const feed =
    url.pathname === "/" && !returning
      ? { search, count: 0, scroll: 0 }
      : current.feed;
  history.pushState(
    { id: crypto.randomUUID(), scroll: returning ? feed.scroll : 0, feed },
    "",
    url.pathname === "/" ? "/" + search : url.pathname + url.search,
  );
  window.dispatchEvent(new PopStateEvent("popstate"));
}
export function backToFeed(): string {
  return "/" + entry().feed.search;
}
export function projectPath(project: string): string {
  const params = new URLSearchParams(entry().feed.search);
  if (project) params.set("project", project);
  else params.delete("project");
  return "/" + feedSearch(params.toString());
}
