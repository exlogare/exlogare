const DOCS_ORIGIN = "https://exlogare.net";

const DOC_PATHS: Record<string, string> = {
  "": "/docs",
  api: "/docs/api-read",
  "api-read": "/docs/api-read",
  webhooks: "/docs/webhooks",
  "self-hosting": "/docs/self-hosting",
  oauth: "/docs/oauth",
};

function docsLocale(lang?: string): string {
  return lang?.toLowerCase().startsWith("ru") ? "/ru" : "";
}

export function docsUrl(path: string, lang?: string): string {
  const segment = DOC_PATHS[path] ?? DOC_PATHS[""];
  return `${DOCS_ORIGIN}${docsLocale(lang)}${segment}`;
}

export function legalLink(_kind: string, _lang?: string): string {
  return `${DOCS_ORIGIN}${docsLocale(_lang)}/docs`;
}
