import { z } from "zod";
import { Envelope } from "./data";
export const servedTree = import.meta.env.BASE_URL.split("/")[2] ?? "";
export class NewVersion extends Error {}
export async function request<T>(
  path: string,
  schema: z.ZodType<T>,
  signal?: AbortSignal,
  etag?: string,
): Promise<{ data: T; etag: string | null } | null> {
  const response = await fetch(path, {
    signal,
    headers: etag ? { "If-None-Match": etag } : {},
  });
  if (response.status === 304) return null;
  const raw: unknown = await response.json();
  const envelope = Envelope.parse(raw);
  if (envelope.schema !== undefined && envelope.schema !== 1) {
    window.dispatchEvent(new Event("hive:new-schema"));
    throw new NewVersion("A new dashboard version is available");
  }
  if (envelope.ui_tree && servedTree && envelope.ui_tree !== servedTree)
    window.dispatchEvent(new Event("hive:new-version"));
  if (
    (!response.ok || envelope.code !== "Dashboard") &&
    envelope.code !== "excerpt_unavailable"
  )
    throw new Error(
      envelope.detail ?? "Observations are temporarily unavailable",
    );
  const validated = schema.safeParse(raw);
  if (!validated.success)
    throw new Error(
      "The observation format could not be read. Reload the dashboard or try again after the collector catches up.",
    );
  return { data: validated.data, etag: response.headers.get("ETag") };
}
