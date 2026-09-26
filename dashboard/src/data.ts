import { z } from "zod";

export const Amount = z.string().regex(/^\d+$/);
const MaybeText = z.string().nullish();
const Totals = z.record(z.string(), Amount);
export const CardSchema = z.object({
  key: z.string(),
  kind: z.string(),
  project: z.string(),
  amount_picos: Amount,
  coverage: z.number(),
  unpriced: z.number(),
  last_activity: z.string(),
  roles: Totals,
  badges: z.record(z.string(), z.number()),
  title: z.string(),
  subtitle: MaybeText,
  state: z.string(),
  thread: MaybeText,
  bead: MaybeText,
  primary_role: z.string().default("unknown"),
  owners: z.array(z.string()).default([]),
  created: MaybeText,
  closed: MaybeText,
});
export type Card = z.infer<typeof CardSchema>;
const RoleTotal = z.object({ role: z.string(), amount_picos: Amount });
export const HealthSchema = z.object({
  summaries_behind: z.boolean().default(false),
  summaries_error: MaybeText,
  summaries_refreshed: MaybeText,
  tollgate_error: MaybeText,
  bead_events_error: MaybeText,
  bead_events_behind: z.boolean().optional(),
  tollgate_refreshed: MaybeText,
  registry: z.object({ refreshed: MaybeText, error: MaybeText }).nullish(),
  discovery_error: MaybeText,
  discovery_behind: z.boolean().optional(),
  tollgate_behind: z.boolean().optional(),
});
export type Health = z.infer<typeof HealthSchema>;
export const FeedSchema = z.object({
  cards: z.array(CardSchema),
  projects: z.array(z.object({ id: z.string(), count: z.number() })),
  summary: z.object({
    amount_picos: Amount,
    incomplete_picos: Amount,
    unpriced: z.number(),
    roles: z.array(RoleTotal),
    window: z.string(),
    start: z.string(),
    timezone: z.string(),
  }),
  hotspots: z.array(
    z.object({
      kind: z.string(),
      title: z.string(),
      amount_picos: Amount,
      keys: z.array(z.string()),
    }),
  ),
  next_cursor: MaybeText,
  revision: z.string(),
  collector: HealthSchema,
});
export type Feed = z.infer<typeof FeedSchema>;
export const SliceSchema = z.object({
  label: z.string(),
  amount_picos: Amount,
});
export type Slice = z.infer<typeof SliceSchema>;
const PointSchema = z.object({
  response: z.string(),
  at: z.string(),
  thread: z.string(),
  agent: MaybeText,
  role: z.string(),
  amount_picos: Amount.nullable(),
});
export type Point = z.infer<typeof PointSchema>;
const IntervalSchema = z.object({
  bead: z.string(),
  thread: z.string(),
  start: z.string(),
  end: MaybeText,
  deleted: z.boolean(),
});
export type Interval = z.infer<typeof IntervalSchema>;
export const DiagnosticSchema = z.object({
  thread: z.string(),
  agent: z.string().default(""),
  host: z.string(),
  call_id: z.string().optional(),
  id: z.string().optional(),
  tool: z.string().optional(),
  kind: z.string().optional(),
  started_at: z.string().optional(),
  finished_at: MaybeText,
  at: z.string().optional(),
  until: MaybeText,
  duration_ms: z.number().nullable().optional(),
  status: MaybeText,
  slow: z.boolean().optional(),
  retry_loop: z.boolean().optional(),
  waiting: z.boolean().optional(),
  role: z.string().optional(),
  amount_picos: Amount.nullish(),
  ref: MaybeText,
});
export type Diagnostic = z.infer<typeof DiagnosticSchema>;
const StepSchema = z.object({
  name: z.string(),
  exit_code: z.number().nullable(),
  result_class: MaybeText,
  elapsed_ms: z.number().nullable(),
});
const AttemptSchema = z.object({
  id: z.string(),
  number: z.number(),
  state: z.string(),
  created_at: MaybeText,
  started_at: MaybeText,
  finished_at: MaybeText,
  steps: z.array(StepSchema),
});
export const CandidateSchema = z.object({
  id: z.string(),
  branch: MaybeText,
  subject: MaybeText,
  state: z.string(),
  submitted_at: MaybeText,
  promoted_at: MaybeText,
  attempts: z.array(AttemptSchema),
  links: z.array(
    z.object({
      method: z.string(),
      beads: z.array(z.string()),
      thread: MaybeText,
      agent: MaybeText,
    }),
  ),
});
export type Candidate = z.infer<typeof CandidateSchema>;
const NativeRecord = z.record(z.string(), z.unknown());
const BeadsSchema = z.object({
  source: z.string(),
  refreshed: MaybeText,
  age_seconds: z.number().optional(),
  bead: NativeRecord,
  commands: z.record(z.string(), z.string()),
  comments: z.array(NativeRecord).default([]),
  dependents: z.array(NativeRecord).default([]),
  events: z.array(NativeRecord).default([]),
  error: MaybeText,
});
export type Beads = z.infer<typeof BeadsSchema>;
const EpicSchema = z.object({
  native_status: z.string(),
  scope: z.string(),
  classification: z.string(),
  groups: z.array(z.object({ category: z.string(), total: z.number(), completed: z.number() })),
  active: z.number(),
  in_ci: z.number(),
  blocked: z.number(),
  held: z.number(),
  owners: z.array(z.string()),
  amount_picos: Amount,
  unpriced: z.number(),
  incomplete: z.number(),
  missing_costs: z.number(),
  refreshed: MaybeText,
  collector: HealthSchema,
  members: z.array(z.object({
    bead: z.string(), title: z.string(), category: z.string(), direct_child: z.boolean(), current: z.boolean(),
    native_status: z.string(), state: z.string(), completed: z.boolean(), cancelled: z.boolean(),
    active: z.boolean(), owner: MaybeText, blockers: z.array(z.string()), held: z.boolean(),
    in_ci: z.boolean(), ci_failed: z.boolean(),
  })),
});
export type Epic = z.infer<typeof EpicSchema>;
export const DetailSchema = z.object({
  epic: EpicSchema.nullish(),
  card: CardSchema,
  amount_picos: Amount,
  timeline: z.array(PointSchema),
  intervals: z.array(IntervalSchema),
  context_requests: z.array(
    z
      .object({
        response: z.string(),
        observed_at: z.string(),
        usd: MaybeText,
        thread: z.string(),
      })
      .passthrough(),
  ),
  breakdown: z.record(z.string(), z.array(SliceSchema)),
  diagnostics: z.array(DiagnosticSchema),
  candidates: z.array(CandidateSchema),
  role_spans: z.array(
    z.object({
      thread: z.string(),
      agent: z.string(),
      role: z.string(),
      start: z.string(),
      inherited: z.number(),
      evidence: z.string(),
    }),
  ),
  subagents: z.array(SliceSchema),
  created_beads: z.array(z.object({ bead: z.string() })),
  contributors: z.array(
    z.object({
      thread: z.string(),
      amount_picos: Amount,
      coverage: z.number(),
    }),
  ),
  beads: BeadsSchema.optional(),
});
export type Detail = z.infer<typeof DetailSchema>;
export const RequestSchema = z
  .object({
    response: z.string(),
    observed_at: z.string(),
    thread: z.string().optional(),
    task: z.string().optional(),
    model: MaybeText,
    agent: MaybeText,
    role: z.string().optional(),
    share_picos: Amount.nullable(),
    flags: z.array(z.string()).or(z.string()).optional(),
  })
  .passthrough();
export type RequestRow = z.infer<typeof RequestSchema>;
export const RequestsSchema = z.object({
  requests: z.array(RequestSchema),
  next_cursor: MaybeText,
});
export type Requests = z.infer<typeof RequestsSchema>;
export const Envelope = z.object({
  code: z.string(),
  schema: z.number().optional(),
  ui_tree: MaybeText,
  detail: MaybeText,
});
export const RecordSchema = NativeRecord;
export function text(value: unknown): string {
  return typeof value === "string"
    ? value
    : typeof value === "number"
      ? String(value)
      : value == null
        ? "—"
        : JSON.stringify(value);
}
export function money(value: string | null | undefined, digits = 2): string {
  if (value == null) return "Unpriced";
  const amount = BigInt(value),
    scale = 10n ** BigInt(12 - digits),
    rounded = (amount + scale / 2n) / scale,
    factor = 10n ** BigInt(digits);
  return (
    "$" +
    (rounded / factor).toLocaleString("en-US") +
    (digits ? "." + (rounded % factor).toString().padStart(digits, "0") : "")
  );
}
export function ratio(amount: string, total: string): number {
  return BigInt(total) > 0n
    ? Number((BigInt(amount) * 1000000n) / BigInt(total)) / 1000000
    : 0;
}
export function when(value: string | null | undefined): string {
  return value
    ? new Date(value).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";
}
export function duration(value: number | null | undefined): string {
  return value == null
    ? "Duration unavailable"
    : value < 1000
      ? `${value} ms`
      : value < 60000
        ? `${(value / 1000).toFixed(1)} s`
        : `${(value / 60000).toFixed(1)} min`;
}
export function label(value: string): string {
  return value.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
}
export function cardPath(card: Pick<Card, "key" | "kind">): string {
  if (card.key.startsWith("bead:"))
    return "/bead/" + encodeURIComponent(card.key.slice(5));
  if (card.key.startsWith("session:"))
    return (
      "/session/" + card.key.slice(8) + (card.kind === "tail" ? "?tail=1" : "")
    );
  const [, kind, ...project] = card.key.split(":");
  return "/ledger/" + kind + "/" + encodeURIComponent(project.join(":"));
}
export function keyPath(key: string): string {
  return cardPath({ key, kind: key.startsWith("session:") ? "agent" : "bead" });
}
export function diagnosticKind(item: Diagnostic): string {
  return (
    item.kind ??
    (item.retry_loop
      ? "retry_loop"
      : item.slow
        ? "slow_tool"
        : item.status === "error"
          ? "tool_error"
          : item.waiting
            ? "intentional_wait"
            : "tool_call")
  );
}
export function diagnosticAt(item: Diagnostic): string {
  return item.at ?? item.started_at ?? "";
}

export function cardTitle(
  card: Pick<Card, "title" | "kind" | "primary_role">,
): string {
  if (card.kind === "bead") return card.title;
  const title = card.title
    .replace(/\[\$[\w:-]+\]\([^)]*\)/g, "")
    .replace(/<[^>]*>/g, "")
    .trim();
  return !title || title === card.primary_role
    ? label(card.primary_role) + " session"
    : title;
}
