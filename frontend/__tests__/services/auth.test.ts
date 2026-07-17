import { auth } from '../../services/auth';

const mockFetch = jest.fn();
global.fetch = mockFetch as any;

const mockLocalStorage = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => { store[key] = value; },
    removeItem: (key: string) => { delete store[key]; },
    clear: () => { store = {}; },
  };
})();
Object.defineProperty(global, 'localStorage', { value: mockLocalStorage, writable: true });

const mockLocation = { hash: '', search: '', origin: 'http://localhost', pathname: '/', href: '' };
const mockWindow = { location: mockLocation, history: { replaceState: jest.fn() } };
(Object.keys(mockWindow) as (keyof typeof mockWindow)[]).forEach((key) => {
  (global as any)[key] = (mockWindow as any)[key];
});
Object.defineProperty(global, 'window', { value: mockWindow, writable: true });

jest.mock('react-native', () => {
  const RN = jest.requireActual('react-native');
  RN.Platform.OS = 'web';
  return RN;
});

beforeEach(() => {
  mockFetch.mockReset();
  mockLocalStorage.clear();
  auth.removeToken();
});

describe('auth service', () => {
  describe('token storage', () => {
    it('stores and retrieves access and refresh tokens together', () => {
      auth.setSession('test-token-123', 'refresh-token-123');
      expect(auth.getToken()).toBe('test-token-123');
      expect(auth.getRefreshToken()).toBe('refresh-token-123');
    });

    it('removes the stored session', () => {
      auth.setSession('test-token-123', 'refresh-token-123');
      auth.removeToken();
      expect(auth.getToken()).toBeNull();
      expect(auth.getRefreshToken()).toBeNull();
    });

    it('returns null when no token is stored', () => {
      expect(auth.getToken()).toBeNull();
      expect(auth.getRefreshToken()).toBeNull();
    });

    it('persists the full session across web reloads using localStorage', () => {
      auth.setSession('persisted-token', 'persisted-refresh-token');
      expect(auth.getToken()).toBe('persisted-token');
      expect(auth.getRefreshToken()).toBe('persisted-refresh-token');
      expect(mockLocalStorage.getItem('sacrifice_auth_token')).toBe('persisted-token');
      expect(mockLocalStorage.getItem('sacrifice_refresh_token')).toBe('persisted-refresh-token');
    });
  });

  describe('googleLogin', () => {
    it('posts id token to /api/auth/google and returns access and refresh tokens', async () => {
      const expected = {
        access_token: 'jwt-abc',
        refresh_token: 'refresh-abc',
        user: { id: '1', email: 'a@b.com', display_name: 'Alice', auth_provider: 'google' },
      };
      mockFetch.mockResolvedValueOnce({ ok: true, json: async () => expected });

      const result = await auth.googleLogin('google-id-token');
      expect(result).toEqual(expected);
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/auth/google'),
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ token: 'google-id-token' }),
        }),
      );
    });

    it('throws on non-ok response', async () => {
      mockFetch.mockResolvedValueOnce({ ok: false, status: 401 });
      await expect(auth.googleLogin('bad-token')).rejects.toThrow('Google login failed: 401');
    });
  });

  describe('githubLogin', () => {
    it('posts code to /api/auth/github and returns access and refresh tokens', async () => {
      const expected = {
        access_token: 'jwt-def',
        refresh_token: 'refresh-def',
        user: { id: '2', email: 'b@c.com', display_name: 'Bob', auth_provider: 'github' },
      };
      mockFetch.mockResolvedValueOnce({ ok: true, json: async () => expected });

      const result = await auth.githubLogin('github-code-123');
      expect(result).toEqual(expected);
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/auth/github'),
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ code: 'github-code-123' }),
        }),
      );
    });

    it('throws on non-ok response', async () => {
      mockFetch.mockResolvedValueOnce({ ok: false, status: 400 });
      await expect(auth.githubLogin('bad-code')).rejects.toThrow('GitHub login failed: 400');
    });
  });

  describe('refreshSession', () => {
    it('rotates the stored session when refresh succeeds', async () => {
      auth.setSession('expired-access', 'refresh-123');
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ access_token: 'new-access', refresh_token: 'new-refresh' }),
      });

      const result = await auth.refreshSession();

      expect(result).toBe('new-access');
      expect(auth.getToken()).toBe('new-access');
      expect(auth.getRefreshToken()).toBe('new-refresh');
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/auth/refresh'),
        expect.objectContaining({
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: 'refresh-123' }),
        }),
      );
    });

    it('clears the stored session when refresh fails', async () => {
      auth.setSession('expired-access', 'refresh-123');
      mockFetch.mockResolvedValueOnce({ ok: false, status: 401 });

      const result = await auth.refreshSession();

      expect(result).toBeNull();
      expect(auth.getToken()).toBeNull();
      expect(auth.getRefreshToken()).toBeNull();
    });
  });

  describe('fetchUser', () => {
    it('fetches user profile with bearer token', async () => {
      const user = { id: '1', email: 'a@b.com', display_name: 'Alice' };
      mockFetch.mockResolvedValueOnce({ ok: true, json: async () => user });

      const result = await auth.fetchUser('valid-jwt');
      expect(result).toEqual(user);
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/auth/me'),
        expect.objectContaining({
          headers: { Authorization: 'Bearer valid-jwt' },
        }),
      );
    });

    it('throws when fetch fails', async () => {
      mockFetch.mockResolvedValueOnce({ ok: false, status: 401 });
      await expect(auth.fetchUser('bad-jwt')).rejects.toThrow('Failed to fetch user');
    });
  });

  describe('handleRedirectCallback', () => {
    beforeEach(() => {
      mockLocation.hash = '';
      mockLocation.search = '';
      mockLocation.pathname = '/';
      mockLocation.href = 'http://localhost/';
      mockWindow.history.replaceState.mockClear();
    });

    it('extracts access and refresh tokens from backend redirect query params', () => {
      mockLocation.search = '?access_token=jwt-123&refresh_token=refresh-123';
      mockLocation.href = 'http://localhost/?access_token=jwt-123&refresh_token=refresh-123';
      const result = auth.handleRedirectCallback();
      expect(result).toEqual({ accessToken: 'jwt-123', refreshToken: 'refresh-123' });
      expect(mockWindow.history.replaceState).toHaveBeenCalled();
    });

    it('extracts id_token from URL hash fragment', () => {
      mockLocation.hash = 'id_token=google-id-token-123';
      const result = auth.handleRedirectCallback();
      expect(result).toEqual({ token: 'google-id-token-123' });
    });

    it('extracts code from URL query params', () => {
      mockLocation.search = '?code=github-code-456';
      mockLocation.href = 'http://localhost/?code=github-code-456';
      const result = auth.handleRedirectCallback();
      expect(result).toEqual({ code: 'github-code-456' });
    });

    it('returns null when no auth params are present', () => {
      expect(auth.handleRedirectCallback()).toBeNull();
    });
  });

  describe('OAuth URL generation', () => {
    it('getGoogleOAuthUrl includes client_id, response_type, redirect_uri', () => {
      const url = auth.getGoogleOAuthUrl('http://localhost:8082');
      expect(url).toContain('client_id=');
      expect(url).toContain('response_type=id_token');
      expect(url).toContain('redirect_uri=' + encodeURIComponent('http://localhost:8082'));
      const scopeEncoded = url.includes('scope=openid+email+profile') || url.includes('scope=openid%20email%20profile');
      expect(scopeEncoded).toBe(true);
    });

    it('getGithubOAuthUrl includes client_id, redirect_uri, scope', () => {
      const url = auth.getGithubOAuthUrl('http://localhost:8082');
      expect(url).toContain('client_id=');
      expect(url).toContain('redirect_uri=' + encodeURIComponent('http://localhost:8082'));
      expect(url).toContain('scope=' + encodeURIComponent('user:email'));
    });
  });
});
