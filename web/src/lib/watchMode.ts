/** Pick an integration mode for POST /watch from allowed modes (prefer hybrid → webhook → polling). */
export function resolveWatchMode(
  modes: Array<"webhook" | "oauth_polling" | "hybrid"> | undefined,
): "webhook" | "oauth_polling" | "hybrid" | null {
  if (!modes?.length) return null;
  if (modes.includes("hybrid")) return "hybrid";
  if (modes.includes("webhook")) return "webhook";
  if (modes.includes("oauth_polling")) return "oauth_polling";
  return null;
}
