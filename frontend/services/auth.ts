import { Platform } from 'react-native';
import * as Linking from 'expo-linking';
import * as WebBrowser from 'expo-web-browser';

const TOKEN_KEY = 'sacrifice_auth_token';

/**
 * Resolve the backend base URL.
 *
 * - Native (Expo Go on a phone): use the build-time EXPO_PUBLIC_API_URL (the
 *   LAN IP) — the device can't reach `localhost`.
 * - Web (desktop browser): talk to the backend on the SAME host the page was
 *   served from, port 8000. This is what makes OAuth work without per-host
 *   config churn: the `oauth_state` cookie is set on the API host and the
 *   provider redirect returns to that same host, so opening the app at
 *   http://localhost:8090 keeps everything on `localhost` (and Google only
 *   permits `http://localhost`, not an IP, for non-HTTPS redirect URIs).
 */
function resolveApiBase(): string {
  if (Platform.OS !== 'web') {
    return process.env.EXPO_PUBLIC_API_URL || 'http://localhost:8000';
  }
  if (typeof window !== 'undefined' && window.location?.hostname) {
    const { protocol, host, hostname, port } = window.location;
    // On a standard port we're behind a reverse proxy (e.g. tailscale serve)
    // that mounts the backend on the same origin under /api — same-origin
    // keeps OAuth cookies and HTTPS intact. On Expo dev ports the backend
    // runs alongside on :8000.
    if (!port || port === '80' || port === '443') {
      return `${protocol}//${host}`;
    }
    return `${protocol}//${hostname}:8000`;
  }
  return process.env.EXPO_PUBLIC_API_URL || 'http://localhost:8000';
}

const GOOGLE_CLIENT_ID =
  process.env.EXPO_PUBLIC_GOOGLE_CLIENT_ID ||
  '860560710677-clmfl8bimlal02fv0eag35ocvjo246eo.apps.googleusercontent.com';
const GITHUB_CLIENT_ID =
  process.env.EXPO_PUBLIC_GITHUB_CLIENT_ID ||
  'Ov23lipXWMn1MXu7X9Y0';

let cachedToken: string | null = null;

export type EmailAuthProvider = 'email' | 'google' | 'github' | string;

export type EmailAuthResult =
  | { ok: true; access_token: string; user: any }
  | { ok: false; status: number; error: string; provider?: EmailAuthProvider };

async function parseEmailAuthResponse(resp: Response): Promise<EmailAuthResult> {
  let body: any = null;
  try {
    body = await resp.json();
  } catch {
    body = null;
  }
  if (resp.ok && body?.access_token) {
    return { ok: true, access_token: body.access_token, user: body.user };
  }
  return {
    ok: false,
    status: resp.status,
    error: body?.error || 'request_failed',
    provider: body?.provider,
  };
}

export const auth = {
  getApiBase(): string {
    return resolveApiBase();
  },
  getToken(): string | null {
    if (cachedToken) return cachedToken;
    if (Platform.OS === 'web') {
      try {
        cachedToken = localStorage.getItem(TOKEN_KEY);
      } catch {
        cachedToken = null;
      }
    }
    return cachedToken;
  },

  setToken(token: string): void {
    cachedToken = token;
    if (Platform.OS === 'web') {
      try {
        localStorage.setItem(TOKEN_KEY, token);
      } catch {
        console.error('Failed to persist auth token');
      }
    } else {
      this.persistTokenSecure(token);
    }
  },

  removeToken(): void {
    cachedToken = null;
    if (Platform.OS === 'web') {
      try {
        localStorage.removeItem(TOKEN_KEY);
        // User-scoped client state must not survive into the next login:
        // the chat draft/generation banner is keyed globally, so without
        // this a different account logging in on the same browser sees the
        // previous user's in-progress goal chat (seen 2026-07-17).
        localStorage.removeItem('sacrifice_chat_goal_create_session');
      } catch {
        console.error('Failed to remove auth token');
      }
    } else {
      this.removeTokenSecure();
    }
  },

  async persistTokenSecure(token: string): Promise<void> {
    try {
      const SecureStore = require('expo-secure-store');
      await SecureStore.setItemAsync(TOKEN_KEY, token);
    } catch {
      // SecureStore not available
    }
  },

  async removeTokenSecure(): Promise<void> {
    try {
      const SecureStore = require('expo-secure-store');
      await SecureStore.deleteItemAsync(TOKEN_KEY);
    } catch {
      // SecureStore not available
    }
  },

  async restoreToken(): Promise<void> {
    if (Platform.OS === 'web') return;
    try {
      const SecureStore = require('expo-secure-store');
      const token = await SecureStore.getItemAsync(TOKEN_KEY);
      if (token) {
        cachedToken = token;
      }
    } catch {
      // SecureStore not available
    }
  },

  async googleLogin(idToken: string) {
    const resp = await fetch(`${resolveApiBase()}/api/auth/google`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: idToken }),
    });
    if (!resp.ok) throw new Error(`Google login failed: ${resp.status}`);
    return resp.json() as Promise<{ access_token: string; user: any }>;
  },

  async emailRegister(email: string, password: string, displayName?: string): Promise<EmailAuthResult> {
    const resp = await fetch(`${resolveApiBase()}/api/auth/email/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email,
        password,
        ...(displayName ? { display_name: displayName } : {}),
      }),
    });
    return parseEmailAuthResponse(resp);
  },

  async emailLogin(email: string, password: string): Promise<EmailAuthResult> {
    const resp = await fetch(`${resolveApiBase()}/api/auth/email/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    return parseEmailAuthResponse(resp);
  },

  async githubLogin(code: string) {
    const resp = await fetch(`${resolveApiBase()}/api/auth/github`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    });
    if (!resp.ok) throw new Error(`GitHub login failed: ${resp.status}`);
    return resp.json() as Promise<{ access_token: string; user: any }>;
  },

  async exchangeCode(code: string) {
    const resp = await fetch(`${resolveApiBase()}/api/auth/exchange`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    });
    if (!resp.ok) throw new Error(`Auth exchange failed: ${resp.status}`);
    return resp.json() as Promise<{ access_token: string; user: any }>;
  },

  async logout(token?: string | null): Promise<void> {
    if (!token) return;
    const resp = await fetch(`${resolveApiBase()}/api/auth/logout`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!resp.ok) throw new Error(`Logout failed: ${resp.status}`);
  },

  async fetchUser(token: string) {
    const resp = await fetch(`${resolveApiBase()}/api/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!resp.ok) throw new Error('Failed to fetch user');
    return resp.json();
  },

  getGoogleOAuthUrl(redirectUri: string): string {
    const params = new URLSearchParams({
      client_id: GOOGLE_CLIENT_ID,
      response_type: 'id_token',
      redirect_uri: redirectUri,
      scope: 'openid email profile',
      nonce: Math.random().toString(36).substring(2),
    });
    return `https://accounts.google.com/o/oauth2/v2/auth?${params}`;
  },

  getGithubOAuthUrl(redirectUri: string): string {
    const params = new URLSearchParams({
      client_id: GITHUB_CLIENT_ID,
      redirect_uri: redirectUri,
      scope: 'user:email',
    });
    return `https://github.com/login/oauth/authorize?${params}`;
  },

  handleRedirectCallback(): {
    token?: string;
    code?: string;
    authCode?: string;
    accessToken?: string;
    error?: string;
    provider?: EmailAuthProvider;
  } | null {
    if (Platform.OS !== 'web') return null;
    if (typeof window === 'undefined' || !window.location) return null;

    const queryParams = new URLSearchParams(window.location.search);
    const authCode = queryParams.get('auth_code');
    if (authCode) {
      const url = new URL(window.location.href);
      url.searchParams.delete('auth_code');
      window.history.replaceState({}, '', url.toString());
      return { authCode };
    }

    const accessToken = queryParams.get('access_token');
    if (accessToken) {
      const url = new URL(window.location.href);
      url.searchParams.delete('access_token');
      window.history.replaceState({}, '', url.toString());
      return { accessToken };
    }

    const errorParam = queryParams.get('error');
    if (errorParam) {
      const provider = queryParams.get('provider') || undefined;
      const url = new URL(window.location.href);
      url.searchParams.delete('error');
      url.searchParams.delete('provider');
      window.history.replaceState({}, '', url.toString());
      return { error: errorParam, provider };
    }

    const hash = window.location.hash.replace(/^#/, '');
    const code = queryParams.get('code');

    if (hash) {
      const hashParams = new URLSearchParams(hash);
      const idToken = hashParams.get('id_token');
      if (idToken) {
        window.location.hash = '';
        return { token: idToken };
      }
    }

    if (code) {
      const url = new URL(window.location.href);
      url.searchParams.delete('code');
      window.history.replaceState({}, '', url.toString());
      return { code };
    }

    return null;
  },

  async nativeOAuthLogin(provider: 'google' | 'github'): Promise<{ access_token: string; user: any } | null> {
    const redirectUri = Linking.createURL('auth/callback');
    const loginUrl = `${resolveApiBase()}/api/auth/${provider}/login?redirect_uri=${encodeURIComponent(redirectUri)}`;
    const result = await WebBrowser.openAuthSessionAsync(loginUrl, redirectUri);
    if (result.type !== 'success' || !result.url) return null;

    const callbackUrl = new URL(result.url);
    const authCode = callbackUrl.searchParams.get('auth_code');
    if (authCode) {
      return this.exchangeCode(authCode);
    }

    const accessToken = callbackUrl.searchParams.get('access_token');
    if (!accessToken) return null;
    const userData = await this.fetchUser(accessToken);
    return { access_token: accessToken, user: userData };
  },
};
