// Thin API client over the FastAPI backend (proxied via /api)
// A random key identifies one browser-tab session. It leaves with the tab.

const TOKEN_KEY = "ss_owner_token";
const HAS_PAPERS_KEY = "ss_has_papers";
const END_NOTICE_KEY = "ss_last_session_end";
const BANNER_SEEN_KEY = "ss_privacy_banner_seen";

export type SessionEndNotice = "confirmed" | "requested" | "expired" | null;

export function sessionEndNotice(): SessionEndNotice {
  const value = sessionStorage.getItem(END_NOTICE_KEY);
  return value === "confirmed" || value === "requested" || value === "expired" ? value : null;
}

export function dismissSessionEndNotice(): void {
  sessionStorage.removeItem(END_NOTICE_KEY);
}

export function hasSessionPapers(): boolean {
  return sessionStorage.getItem(HAS_PAPERS_KEY) === "1";
}

export function privacyBannerSeen(): boolean {
  return sessionStorage.getItem(BANNER_SEEN_KEY) === "1";
}

export function dismissPrivacyBanner(): void {
  sessionStorage.setItem(BANNER_SEEN_KEY, "1");
}

export function markSessionHasData(hasPapers = true): void {
  if (hasPapers) sessionStorage.setItem(HAS_PAPERS_KEY, "1");
  else sessionStorage.removeItem(HAS_PAPERS_KEY);
  window.dispatchEvent(new CustomEvent("scholarscout:papers", { detail: hasPapers }));
}

function getToken(): string {
  let token = sessionStorage.getItem(TOKEN_KEY);
  if (!token) {
    // Migrate an existing library into this final tab session, then stop
    // retaining the key across visits.
    const old = localStorage.getItem(TOKEN_KEY);
    localStorage.removeItem(TOKEN_KEY);
    if (old?.startsWith("ss_")) token = old;
  }
  if (!token) {
    const bytes = new Uint8Array(32);
    crypto.getRandomValues(bytes);
    token = "ss_" + btoa(String.fromCharCode(...bytes))
      .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  sessionStorage.setItem(TOKEN_KEY, token);
  return token;
}

export function ownerToken(): string {
  return getToken();
}

const authHeaders = (token: string): HeadersInit => ({ "X-Owner-Token": token });

export function requestSessionIsCurrent(requestToken: string): boolean {
  return sessionStorage.getItem(TOKEN_KEY) === requestToken;
}

/** A late response from an old token must never end a newer browser session. */
export function notifySessionResponse(status: number, requestToken: string): void {
  if (status !== 410 || !requestSessionIsCurrent(requestToken)) return;
  if (sessionEndNotice() === "expired") return;
  sessionStorage.setItem(END_NOTICE_KEY, "expired");
  window.dispatchEvent(new CustomEvent("scholarscout:session-ended"));
}

/** Only the visitor's explicit restart action releases the ended session token. */
export function startNewSession(): void {
  sessionStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(HAS_PAPERS_KEY);
  sessionStorage.removeItem(BANNER_SEEN_KEY);
  sessionStorage.removeItem(END_NOTICE_KEY);
  localStorage.removeItem(TOKEN_KEY);
  window.dispatchEvent(new CustomEvent("scholarscout:papers", { detail: false }));
  window.dispatchEvent(new CustomEvent("scholarscout:session-ended"));
}

export async function endSession(): Promise<void> {
  const token = sessionStorage.getItem(TOKEN_KEY);
  if (token) {
    const response = await fetch("/api/me/purge", {
      method: "DELETE", headers: { "X-Owner-Token": token }, cache: "no-store",
    });
    notifySessionResponse(response.status, token);
    if (!response.ok) throw new Error("Could not erase this session. Please try again.");
    if (sessionStorage.getItem(TOKEN_KEY) !== token) return;
  }
  sessionStorage.setItem(END_NOTICE_KEY, "confirmed");
  sessionStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(HAS_PAPERS_KEY);
  sessionStorage.removeItem(BANNER_SEEN_KEY);
  localStorage.removeItem(TOKEN_KEY);
  window.dispatchEvent(new CustomEvent("scholarscout:session-ended"));
}

export function installExitCleanup(): void {
  // Browsers display their own confirmation text. This fires for a hard
  // refresh, closing the tab, or navigating away, but not SPA navigation.
  window.addEventListener("beforeunload", (event) => {
    if (!hasSessionPapers()) return;
    event.preventDefault();
    event.returnValue = "";
  });
  // pagehide fires for tab close, refresh and navigation away. The request is
  // best effort; the server's idle expiry is the reliable fallback.
  window.addEventListener("pagehide", () => {
    const token = sessionStorage.getItem(TOKEN_KEY);
    if (!token) return;
    if (hasSessionPapers()) sessionStorage.setItem(END_NOTICE_KEY, "requested");
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(HAS_PAPERS_KEY);
    sessionStorage.removeItem(BANNER_SEEN_KEY);
    localStorage.removeItem(TOKEN_KEY);
    void fetch("/api/me/purge", {
      method: "DELETE", headers: { "X-Owner-Token": token },
      keepalive: true, cache: "no-store",
    }).catch(() => {});
  });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) window.location.reload();
  });
}

async function handle<T>(res: Response, requestToken: string): Promise<T> {
  notifySessionResponse(res.status, requestToken);
  if (res.status === 410) {
    throw new Error("Session ended. Previous research is unavailable. Idle cleanup removes abandoned data.");
  }
  if (res.status === 401) {
    throw new Error("This ScholarScout instance is private - open it in your own browser.");
  }
  if (!res.ok) {
    let detail = "Something went wrong. Please try again.";
    try {
      const data = await res.json();
      if (data?.detail) detail = data.detail;
    } catch { /* keep default */ }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  get: <T>(path: string) => {
    const token = getToken();
    return fetch(`/api${path}`, { headers: authHeaders(token) }).then((r) => handle<T>(r, token)).then((data) => {
      if (sessionStorage.getItem(TOKEN_KEY) !== token) return data;
      if (path === "/papers" && Array.isArray(data)) markSessionHasData(data.length > 0);
      else if (/^\/papers\/[^/]+$/.test(path) && data && typeof data === "object" && "id" in data) {
        markSessionHasData(true);
      }
      return data;
    });
  },
  post: <T>(path: string, body?: unknown, signal?: AbortSignal) => {
    const token = getToken();
    return fetch(`/api${path}`, {
      method: "POST",
      headers: body ? { "Content-Type": "application/json", ...authHeaders(token) } : authHeaders(token),
      body: body ? JSON.stringify(body) : undefined,
      signal,
    }).then((r) => handle<T>(r, token)).then((data) => {
      if (sessionStorage.getItem(TOKEN_KEY) !== token) return data;
      if (path === "/papers/demo" || path.startsWith("/papers/ingest")) markSessionHasData(true);
      return data;
    });
  },
  del: <T>(path: string) => {
    const token = getToken();
    return fetch(`/api${path}`, { method: "DELETE", headers: authHeaders(token) }).then((r) => handle<T>(r, token));
  },
};

/** Download/open an authenticated file: links can't carry the private key,
 * so we fetch as a blob (with the key header) and open/download that. */
export async function fetchAuthFile(path: string, downloadName?: string, page?: number | null): Promise<void> {
  const token = getToken();
  // Open during the click so the browser does not block a tab after the fetch.
  const viewer = downloadName ? null : window.open("about:blank", "_blank");
  if (!downloadName && !viewer) throw new Error("Allow pop-up tabs for ScholarScout to open the source PDF.");
  if (viewer) viewer.opener = null;
  try {
    const res = await fetch(`/api${path}`, { headers: authHeaders(token) });
    notifySessionResponse(res.status, token);
    if (res.status === 410) throw new Error("Session ended. Start a new session to upload research again.");
    if (!res.ok) throw new Error((res.status === 401) ? "Private - open from your own browser." : "Couldn't load the file.");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    if (downloadName) {
      const a = document.createElement("a");
      a.href = url;
      a.download = downloadName;
      a.click();
    } else if (viewer) {
      viewer.location.href = `${url}${page && page > 0 ? `#page=${Math.floor(page)}` : ""}`;
    }
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (reason) {
    viewer?.close();
    throw reason;
  }
}

export function friendlyError(e: unknown): string {
  if (e instanceof Error) return e.message;
  return "Something unexpected happened. Please try again.";
}
