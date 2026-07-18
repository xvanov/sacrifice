import { Platform } from 'react-native';
import * as Linking from 'expo-linking';
import * as WebBrowser from 'expo-web-browser';
import { getApiBaseUrl } from '../config';
import {
  getTokenSync,
  persistToken,
  removeToken as removeStoredToken,
  restoreToken as restoreStoredToken,
} from './tokenStorage';

const GOOGLE_CLIENT_ID =
  process.env.EXPO_PUBLIC_GOOGLE_CLIENT_ID ||
  '860560710677-clmfl8bimlal02fv0eag35ocvjo246eo.apps.googleusercontent.com';
const GITHUB_CLIENT_ID =
  process.env.EXPO_PUBLIC_GITHUB_CLIENT_ID ||
  'Ov23lipXWMn1MXu7X9Y0';

let cachedToken: string | null = null;

type SessionExpiredListener = () => void;
const sessionExpiredListeners = new Set<SessionExpiredListener>();

function emitSessionExpired(): void {
  sessionExpiredListeners.forEach((listener) => {
    try {
      listener();
    } catch {
      // Ignore listener failures so all subscribers still receive the event.
    }
  });

  if (typeof window !== 'undefined' && typeof window.dispatchEvent === 'function') {
    window.dispatchEvent(new Event('sacrifice-session-expired'));
  }
}

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
    return getApiBaseUrl();
  },
  getToken(): string | null {
    if (cachedToken) return cachedToken;
    cachedToken = getTokenSync();
    return cachedToken;
  },

  setToken(token: string): void {
    cachedToken = token;
    void persistToken(token);
  },

  removeToken(): void {
    cachedToken = null;
    void removeStoredToken();

    if (Platform.OS === 'web') {
      try {
        // User-scoped client state must not survive into the next login:
        // the chat draft/generation banner is keyed globally, so without
        // this a different account logging in on the same browser sees the
        // previous user's in-progress goal chat (seen 2026-07-17).
        localStorage.removeItem('sacrifice_chat_goal_create_session');
      } catch {
        // Ignore web storage cleanup failures.
      }
    }
  },

  async restoreToken(): Promise<void> {
    const token = await restoreStoredToken();
    if (token) {
      cachedToken = token;
    }
  },

  onSessionExpired(listener: SessionExpiredListener): () => void {
    sessionExpiredListeners.add(listener);
    return () => {
      sessionExpiredListeners.delete(listener);
    };
  },

  notifySessionExpired(): void {
    this.removeToken();
    emitSessionExpired();
  },

  async googleLogin(idToken: string) {
    const resp = await fetch(`${getApiBaseUrl()}/api/auth/google`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: idToken }),
    });
    if (!resp.ok) throw new Error(`Google login failed: ${resp.status}`);
    return resp.json() as Promise<{ access_token: string; user: any }>;
  },

  async emailRegister(email: string, password: string, displayName?: string): Promise<EmailAuthResult> {
    const resp = await fetch(`${getApiBaseUrl()}/api/auth/email/register`, {
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
    const resp = await fetch(`${getApiBaseUrl()}/api/auth/email/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    return parseEmailAuthResponse(resp);
  },

  async githubLogin(code: string) {
    const resp = await fetch(`${getApiBaseUrl()}/api/auth/github`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    });
    if (!resp.ok) throw new Error(`GitHub login failed: ${resp.status}`);
    return resp.json() as Promise<{ access_token: string; user: any }>;
  },

  async exchangeCode(code: string) {
    const resp = await fetch(`${getApiBaseUrl()}/api/auth/exchange`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    });
    if (!resp.ok) throw new Error(`Auth exchange failed: ${resp.status}`);
    return resp.json() as Promise<{ access_token: string; user: any }>;
  },

  async logout(token?: string | null): Promise<void> {
    if (!token) return;
    const resp = await fetch(`${getApiBaseUrl()}/api/auth/logout`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!resp.ok) throw new Error(`Logout failed: ${resp.status}`);
  },

  async fetchUser(token: string) {
    const resp = await fetch(`${getApiBaseUrl()}/api/auth/me`, {
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
    const loginUrl = `${getApiBaseUrl()}/api/auth/${provider}/login?redirect_uri=${encodeURIComponent(redirectUri)}`;
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
