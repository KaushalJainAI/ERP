import { API_URL, refreshAccessToken } from "@/api/client";
import { getActiveCompanyId } from "@/lib/company";

/** Cache written by the service worker (public/sw.js) when a file is shared to the app. */
export const SHARED_CACHE = "shared-files";

/**
 * POST a multipart form with the same auth, company and token-refresh handling as a chat
 * turn (see AssistantPanel). The Content-Type is left to the browser, so the boundary is set.
 * `build` runs once per attempt, so a retry after a refresh sends a fresh form.
 */
export async function postMultipart(path: string, build: () => FormData): Promise<Response> {
  const send = (token: string | null) => {
    const companyId = getActiveCompanyId();
    return fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(companyId ? { "X-Company-ID": String(companyId) } : {}),
      },
      body: build(),
    });
  };
  let response = await send(localStorage.getItem("access_token"));
  if (response.status === 401) {
    const freshToken = await refreshAccessToken().catch(() => null);
    if (freshToken) response = await send(freshToken);
  }
  return response;
}

const indexOf = (url: string): number => {
  const match = /\/shared\/(\d+)$/.exec(new URL(url).pathname);
  return match ? Number(match[1]) : -1;
};

/**
 * The files shared to the app and still waiting, in the order they were shared. Returns []
 * where the Cache API is unavailable. Files stay in the cache until clearSharedFiles().
 */
export async function readSharedFiles(): Promise<File[]> {
  if (typeof caches === "undefined") return [];
  const cache = await caches.open(SHARED_CACHE);
  const keys = (await cache.keys())
    .filter((request) => indexOf(request.url) >= 0)
    .sort((a, b) => indexOf(a.url) - indexOf(b.url));
  const files: File[] = [];
  for (const key of keys) {
    const response = await cache.match(key);
    if (!response) continue;
    const blob = await response.blob();
    const rawName = response.headers.get("X-File-Name") || "shared-file";
    let name = rawName;
    try {
      name = decodeURIComponent(rawName);
    } catch {
      /* keep the raw name */
    }
    const type = blob.type || response.headers.get("Content-Type") || "";
    files.push(new File([blob], name, { type }));
  }
  return files;
}

/** Empty the shared-files cache once its files are read and handled. */
export async function clearSharedFiles(): Promise<void> {
  if (typeof caches === "undefined") return;
  const cache = await caches.open(SHARED_CACHE);
  for (const key of await cache.keys()) {
    await cache.delete(key);
  }
}
