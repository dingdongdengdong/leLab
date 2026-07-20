const DEFAULT_LOCALHOST = "http://localhost:8000";
const STORAGE_KEY = "lelab.apiBaseUrl";

type RuntimeLocation = Pick<Location, "origin" | "hostname" | "port" | "search">;
type RuntimeStorage = Pick<Storage, "getItem" | "setItem">;

const isLoopbackHost = (hostname: string): boolean =>
  hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";

const isLoopbackUrl = (url: string): boolean => {
  try {
    return isLoopbackHost(new URL(url).hostname);
  } catch {
    return false;
  }
};

export const resolveApiBaseUrl = (
  location: RuntimeLocation,
  storage: RuntimeStorage,
): string => {
  const fromQuery = new URLSearchParams(location.search).get("api");
  if (fromQuery) {
    try {
      const clean = new URL(fromQuery).toString().replace(/\/$/, "");
      storage.setItem(STORAGE_KEY, clean);
      return clean;
    } catch {
      console.warn("Invalid `api` query param, ignoring:", fromQuery);
    }
  }

  const stored = storage.getItem(STORAGE_KEY);
  const remotePage = !isLoopbackHost(location.hostname);
  if (stored && !(remotePage && isLoopbackUrl(stored))) return stored;

  // Vite serves the local development UI on a different port. Everywhere
  // else the API and WebSocket server share the page's origin, including
  // Tailscale/LAN addresses opened from another computer.
  if (isLoopbackHost(location.hostname) && location.port !== "8000") {
    return DEFAULT_LOCALHOST;
  }
  return location.origin;
};

export const resolveInitialApiBaseUrl = (): string => {
  if (typeof window === "undefined") return DEFAULT_LOCALHOST;
  return resolveApiBaseUrl(window.location, window.localStorage);
};
