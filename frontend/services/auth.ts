import { Platform } from 'react-native';
import * as Linking from 'expo-linking';
import * as WebBrowser from 'expo-web-browser';

const TOKEN_KEY = 'sacrifice_auth_token';
const REFRESH_TOKEN_KEY = 'sacrifice_refresh_token';

/**
 * Resolve the backend base URL.
 *
 * - Native (Expo Go on a phone): use the build-time EXPO_PUBLIC_API_URL (the
 *   LAN IP) — the device can't reach `localhost`.
 * - Web (desktop browser): talk to the backend on the SAME host the page was
 *   served from, port 8000. This keeps OAuth on one host so the backend's
 *   state cookie and callback remain aligned.
 */
function resolveApiBase(): string {
  if (Platform.OS !== 'web') {
    return process.env.EXPO_PUBLIC_API_URL || 'http://localhost:8000';
  }
  if (typeof window !== 'undefined' && window.location?.hostname) {
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  return process.env.EXPO_PUBLIC_API_URL || 'http://localhost:8000';
}

const GOOGLE_CLIENT_ID =
  process.env.EXPO_PUBLIC_GOOGLE_CLIENT_ID ||
  '15183776752-8ajqt9odpa3sib1htf31v9p9tur1luc9.apps.googleusercontent.com';
const GITHUB_CLIENT_ID =
  process.env.EXPO_PUBLIC_GITHUB_CLIENT_ID ||
  'Ov23lipXWMn1MXu7X9Y0';

let cachedToken: string | null = null;
let cachedRefreshToken: string | null = null;

export type EmailAuthProvider = 'email' | 'google' | 'github' | string;
export type AuthSuccess = { ok: true; access_token: string; refresh_token: string; user: any };
export type EmailAuthResult =
  | AuthSuccess
  | { ok: false; status: number; error: string; provider?: EmailAuthProvider };

async function parseEmailAuthResponse(resp: Response): Promise<EmailAuthResult> {
  let body: any = null;
  try {
    body = await resp.json();
  } catch {
    body = null;
  }
  if (resp.ok && body?.access_token && body?.refresh_token) {
    return {
      ok: true,
      access_token: body.access_token,
      refresh_token: body.refresh_token,
      user: body.user,
    };
  }
  return {
    ok: false,
    status: resp.status,
    error: body?.error || 'request_failed',
    provider: body?.provider,
  };
}

async function getSecureStore() {
  try {
    return require('expo-secure-store');
  } catch {
    return null;
  }
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

  getRefreshToken(): string | null {
    if (cachedRefreshToken) return cachedRefreshToken;
    if (Platform.OS === 'web') {
      try {
        cachedRefreshToken = localStorage.getItem(REFRESH_TOKEN_KEY);
      } catch {
        cachedRefreshToken = null;
      }
    }
    return cachedRefreshToken;
  },

  setSession(token: string, refreshToken: string | null): void {
    cachedToken = token;
    cachedRefreshToken = refreshToken;
    if (Platform.OS === 'web') {
      try {
        localStorage.setItem(TOKEN_KEY, token);
        if (refreshToken) {
          localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
        } else {
          localStorage.removeItem(REFRESH_TOKEN_KEY);
        }
      } catch {
        console.error('Failed to persist auth session');
      }
      return;
    }
    void this.persistSessionSecure(token, refreshToken);
  },

  setToken(token: string): void {
    this.setSession(token, this.getRefreshToken());
  },

  removeToken(): void {
    cachedToken = null;
    cachedRefreshToken = null;
    if (Platform.OS === 'web') {
      try {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(REFRESH_TOKEN_KEY);
      } catch {
        console.error('Failed to remove auth session');
      }
      return;
    }
    void this.removeTokenSecure();
  },

  async persistSessionSecure(token: string, refreshToken: string | null): Promise<void> {
    const SecureStore = await getSecureStore();
    if (!SecureStore) return;
    await SecureStore.setItemAsync(TOKEN_KEY, token);
    if (refreshToken) {
      await SecureStore.setItemAsync(REFRESH_TOKEN_KEY, refreshToken);
    } else {
      await SecureStore.deleteItemAsync(REFRESH_TOKEN_KEY);
    }
  },

  async persistTokenSecure(token: string): Promise<void> {
    await this.persistSessionSecure(token, this.getRefreshToken());
  },

  async removeTokenSecure(): Promise<void> {
    const SecureStore = await getSecureStore();
    if (!SecureStore) return;
    await SecureStore.deleteItemAsync(TOKEN_KEY);
    await SecureStore.deleteItemAsync(REFRESH_TOKEN_KEY);
  },

  async restoreToken(): Promise<void> {
    if (Platform.OS === 'web') return;
    const SecureStore = await getSecureStore();
    if (!SecureStore) return;
    const [token, refreshToken] = await Promise.all([
      SecureStore.getItemAsync(TOKEN_KEY),
      SecureStore.getItemAsync(REFRESH_TOKEN_KEY),
    ]);
    cachedToken = token;
    cachedRefreshToken = refreshToken;
  },

  async refreshSession(): Promise<string | null> {
    const refreshToken = this.getRefreshToken();
    if (!refreshToken) return null;
    const resp = await fetch(`${resolveApiBase()}/api/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!resp.ok) {
      this.removeToken();
      return null;
    }
    const body = await resp.json();
    if (!body?.access_token || !body?.refresh_token) {
      this.removeToken();
      return null;
    }
    this.setSession(body.access_token, body.refresh_token);
    return body.access_token;
  },

  async googleLogin(idToken: string) {
    const resp = await fetch(`${resolveApiBase()}/api/auth/google`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: idToken }),
    });
    if (!resp.ok) throw new Error(`Google login failed: ${resp.status}`);
    return resp.json() as Promise<{ access_token: string; refresh_token: string; user: any }>;
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
    return resp.json() as Promise<{ access_token: string; refresh_token: string; user: any }>;
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
    accessToken?: string;
    refreshToken?: string;
    error?: string;
    provider?: EmailAuthProvider;
  } | null {
    if (Platform.OS !== 'web') return null;
    if (typeof window === 'undefined' || !window.location) return null;

    const queryParams = new URLSearchParams(window.location.search);
    const accessToken = queryParams.get('access_token');
    const refreshToken = queryParams.get('refresh_token');
    if (accessToken) {
      const url = new URL(window.location.href);
      url.searchParams.delete('access_token');
      url.searchParams.delete('refresh_token');
      window.history.replaceState({}, '', url.toString());
      return { accessToken, refreshToken: refreshToken || undefined };
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

  async nativeOAuthLogin(provider: 'google' | 'github'): Promise<{ access_token: string; refresh_token: string; user: any } | null> {
    const redirectUri = Linking.createURL('auth/callback');
    const loginUrl = `${resolveApiBase()}/api/auth/${provider}/login?redirect_uri=${encodeURIComponent(redirectUri)}`;
    const result = await WebBrowser.openAuthSessionAsync(loginUrl, redirectUri);
    if (result.type !== 'success' || !result.url) return null;
    const callbackUrl = new URL(result.url);
    const accessToken = callbackUrl.searchParams.get('access_token');
    const refreshToken = callbackUrl.searchParams.get('refresh_token');
    if (!accessToken || !refreshToken) return null;
    const userData = await this.fetchUser(accessToken);
    return { access_token: accessToken, refresh_token: refreshToken, user: userData };
  },
};
