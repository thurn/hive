import { z } from "zod";
import { saveScroll } from "./navigation";
import type { Range } from "./Timeline";

const Tab = z.enum(["overview", "diagnostics", "delivery"]);
const Section = z.enum([
  "coverage",
  "requests",
  "breakdowns",
  "ownership",
  "sessions",
  "task",
  "filed",
]);
export type DetailTab = z.infer<typeof Tab>;
export type DetailSection = z.infer<typeof Section>;
export type DetailState = Readonly<{
  tab: DetailTab;
  section: DetailSection | null;
  kind: string;
  range: Range;
  invalidRange: boolean;
}>;

const Timestamp = z.iso.datetime({ offset: true });
export function validRange(range: Range): boolean {
  return (
    !range ||
    (Timestamp.safeParse(range.since).success &&
      Timestamp.safeParse(range.until).success &&
      Date.parse(range.since) <= Date.parse(range.until))
  );
}
export function readDetailState(search: string): DetailState {
  const params = new URLSearchParams(search);
  const tab = Tab.safeParse(params.get("view"));
  const section = Section.safeParse(params.get("section"));
  const range =
    params.has("since") || params.has("until")
      ? { since: params.get("since") ?? "", until: params.get("until") ?? "" }
      : null;
  const kind = params.get("kind") ?? "";
  return {
    tab: tab.success ? tab.data : "overview",
    section: section.success ? section.data : null,
    kind: /^[a-z_]{1,64}$/.test(kind) ? kind : "",
    range: validRange(range) ? range : null,
    invalidRange: !validRange(range),
  };
}
// UI-only keys never reach strict read-only API endpoints.
export function detailApiQuery(path: string, search: string): URLSearchParams {
  const source = new URLSearchParams(search),
    result = new URLSearchParams();
  if (
    path.startsWith("/session/") &&
    source.has("tail") &&
    ["", "1", "true"].includes(source.get("tail") ?? "")
  )
    result.set("tail", "1");
  return result;
}
export function writeDetailState(
  path: string,
  search: string,
  state: DetailState,
): void {
  const params = detailApiQuery(path, search);
  if (state.tab !== "overview") params.set("view", state.tab);
  if (state.section) params.set("section", state.section);
  if (state.kind) params.set("kind", state.kind);
  if (state.range && validRange(state.range)) {
    params.set("since", state.range.since);
    params.set("until", state.range.until);
  }
  saveScroll();
  // View changes belong to this detail visit, preserving its feed-return entry.
  history.replaceState(
    history.state,
    "",
    path + (params.size ? "?" + params : ""),
  );
  window.dispatchEvent(new PopStateEvent("popstate"));
}
