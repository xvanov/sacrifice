import { Platform } from 'react-native';

const TOKEN_KEY = 'sacrifice_auth_token';

const API_BASE = process.env.EXPO_PUBLIC_API_URL || 'http://localhost:8000';
const GOOGLE_CLIENT_ID =
  process.env.EXPO_PUBLIC_GOOGLE_CLIENT_ID ||
  '15183776752-8ajqt9odpa3sib1htf31v9p9tur1luc9.apps.googleusercontent.com';
const GITHUB_CLIENT_ID =
  process.env.EXPO_PUBLIC_GITHUB_CLIENT_ID ||
  'Ov23lipXWMn1MXu7X9Y0';

let cachedToken: string | null = null;

export const auth = {
  getToken(): string | null {
    if (cachedToken) return cachedToken;
    try {
      cachedToken = localStorage.getItem(TOKEN_KEY);
    } catch {
      cachedToken = null;
    }
    return cachedToken;
  },

  setToken(token: string): void {
    cachedToken = token;
    try {
      localStorage.setItem(TOKEN_KEY, token);
    } catch {
      console.error('Failed to persist auth token');
    }
    if (Platform.OS !== 'web') {
      this.persistTokenSecure(token);
    }
  },

  removeToken(): void {
    cachedToken = null;
    try {
      localStorage.removeItem(TOKEN_KEY);
    } catch {
      console.error('Failed to remove auth token');
    }
    if (Platform.OS !== 'web') {
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
        try {
          localStorage.setItem(TOKEN_KEY, token);
        } catch {
          // localStorage fallback
        }
      }
    } catch {
      // SecureStore not available, already have from localStorage
    }
  },

  async googleLogin(idToken: string) {
    const resp = await fetch(`${API_BASE}/api/auth/google`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: idToken }),
    });
    if (!resp.ok) throw new Error(`Google login failed: ${resp.status}`);
    return resp.json() as Promise<{ access_token: string; user: any }>;
  },

  async githubLogin(code: string) {
    const resp = await fetch(`${API_BASE}/api/auth/github`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    });
    if (!resp.ok) throw new Error(`GitHub login failed: ${resp.status}`);
    return resp.json() as Promise<{ access_token: string; user: any }>;
  },

  async fetchUser(token: string) {
    const resp = await fetch(`${API_BASE}/api/auth/me`, {
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

  handleRedirectCallback(): { token?: string; code?: string } | null {
    if (typeof window === 'undefined') return null;

    const hash = window.location.hash.replace(/^#/, '');
    const queryParams = new URLSearchParams(window.location.search);
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
};
