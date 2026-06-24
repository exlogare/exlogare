import { useEffect, useState } from "react";

export const GITHUB_REPO_URL = "https://github.com/exlogare/exlogare";
export const GITHUB_RELEASES_URL = `${GITHUB_REPO_URL}/releases/latest`;

const GITHUB_RELEASES_API = "https://api.github.com/repos/exlogare/exlogare/releases/latest";
const GITHUB_TAGS_API = "https://api.github.com/repos/exlogare/exlogare/tags?per_page=1";
const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000;

const GITHUB_HEADERS: HeadersInit = {
  Accept: "application/vnd.github+json",
  "User-Agent": "Exlogare-Community-Edition",
};

function parseVersion(raw: string): number[] {
  return raw
    .replace(/^v/i, "")
    .split("-")[0]
    .split(".")
    .map((part) => parseInt(part, 10) || 0);
}

export function isNewerVersion(latest: string, current: string): boolean {
  const a = parseVersion(latest);
  const b = parseVersion(current);
  const len = Math.max(a.length, b.length);
  for (let i = 0; i < len; i += 1) {
    const av = a[i] ?? 0;
    const bv = b[i] ?? 0;
    if (av > bv) return true;
    if (av < bv) return false;
  }
  return false;
}

async function fetchLatestGitHubVersion(): Promise<string | null> {
  const releaseRes = await fetch(GITHUB_RELEASES_API, { headers: GITHUB_HEADERS });
  if (releaseRes.ok) {
    const data = (await releaseRes.json()) as { tag_name?: string };
    if (data.tag_name) return data.tag_name.replace(/^v/i, "");
  }

  const tagsRes = await fetch(GITHUB_TAGS_API, { headers: GITHUB_HEADERS });
  if (!tagsRes.ok) return null;
  const tags = (await tagsRes.json()) as Array<{ name?: string }>;
  const tag = tags[0]?.name;
  return tag ? tag.replace(/^v/i, "") : null;
}

async function fetchVersionInfo(): Promise<{
  version: string | null;
  updateCheckEnabled: boolean;
}> {
  const res = await fetch("/api/public/version", {
    headers: { Accept: "application/json" },
  });
  if (!res.ok) return { version: null, updateCheckEnabled: true };
  const data = (await res.json()) as {
    version?: string;
    update_check_enabled?: boolean;
  };
  return {
    version: data.version?.replace(/^v/i, "") ?? null,
    updateCheckEnabled: data.update_check_enabled !== false,
  };
}

export type AppUpdateState = {
  currentVersion: string | null;
  latestVersion: string | null;
  updateAvailable: boolean;
  loading: boolean;
  releasesUrl: string;
};

export function useAppUpdate(): AppUpdateState {
  const [currentVersion, setCurrentVersion] = useState<string | null>(null);
  const [latestVersion, setLatestVersion] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    const checkLatest = async () => {
      const latest = await fetchLatestGitHubVersion();
      if (!cancelled) setLatestVersion(latest);
    };

    const check = async () => {
      try {
        const info = await fetchVersionInfo();
        if (cancelled) return;
        setCurrentVersion(info.version);
        if (info.updateCheckEnabled) {
          await checkLatest();
          if (cancelled) return;
          if (timer === undefined) {
            timer = window.setInterval(() => {
              void checkLatest();
            }, CHECK_INTERVAL_MS);
          }
        } else {
          setLatestVersion(null);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void check();

    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, []);

  const updateAvailable =
    currentVersion != null &&
    latestVersion != null &&
    isNewerVersion(latestVersion, currentVersion);

  return {
    currentVersion,
    latestVersion,
    updateAvailable,
    loading,
    releasesUrl: GITHUB_RELEASES_URL,
  };
}
