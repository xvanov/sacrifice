import { Platform } from 'react-native';

/**
 * Strip a trailing slash so callers can safely append `/api/...` paths.
 */
function stripTrailingSlash(url: string): string {
  return url.endsWith('/') ? url.slice(0, -1) : url;
}

/**
 * Canonical API base URL resolution.
 *
 * All fetch/auth/upload call sites MUST route through this module (directly
 * or via the auth service) so the backend URL is resolved from a single place.
 *
 * - Native (Expo Go on a phone): use the build-time EXPO_PUBLIC_API_URL (the
 *   LAN IP or tunnel URL) — the device can't reach `localhost`.
 * - Web (desktop browser): talk to the backend on the SAME host the page was
 *   served from, port 8000. This keeps OAuth working without per-host config
 *   churn: the `oauth_state` cookie is set on the API host and the provider
 *   redirect returns to that same host.
 * - Falls back to http://localhost:8000 when nothing else is set.
 */
export function getApiBaseUrl(): string {
  let url: string;
  if (Platform.OS !== 'web') {
    url = process.env.EXPO_PUBLIC_API_URL || 'http://localhost:8000';
    return stripTrailingSlash(url);
  }
  if (typeof window !== 'undefined' && window.location?.hostname) {
    const { protocol, host, hostname, port } = window.location;
    // On a standard port we're behind a reverse proxy (e.g. tailscale serve)
    // that mounts the backend on the same origin under /api.
    if (!port || port === '80' || port === '443') {
      return `${protocol}//${host}`;
    }
    return `${protocol}//${hostname}:8000`;
  }
  url = process.env.EXPO_PUBLIC_API_URL || 'http://localhost:8000';
  return stripTrailingSlash(url);
}
const UX_AUDIT_SCENARIO_QUERY_PARAM = 'uxAuditScenario';

export const CAMERA_PERMISSION_DENIED_AUDIT_SCENARIO = 'camera-permission-denied';

export function getUxAuditScenario(): string | null {
  if (process.env.EXPO_PUBLIC_UX_AUDIT_TARGET !== '1') {
    return null;
  }

  if (typeof window === 'undefined' || !window.location?.search) {
    return null;
  }

  const queryParams = new URLSearchParams(window.location.search);
  const scenario = queryParams.get(UX_AUDIT_SCENARIO_QUERY_PARAM);

  if (!scenario) {
    return null;
  }

  const normalizedScenario = scenario.trim();
  return normalizedScenario.length > 0 ? normalizedScenario : null;
}

export function isCameraPermissionDeniedAuditScenarioActive(): boolean {
  return getUxAuditScenario() === CAMERA_PERMISSION_DENIED_AUDIT_SCENARIO;
}

