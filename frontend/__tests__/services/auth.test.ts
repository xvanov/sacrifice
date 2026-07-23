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
  mockWindow.history.replaceState.mockReset();
});

describe('auth service', () => {
  describe('token storage', () => {
    it('stores and retrieves a token', () => {
      auth.setToken('test-token-123');
      expect(auth.getToken()).toBe('test-token-123');
    });

    it('removes a token', () => {
      auth.setToken('test-token-123');
      auth.removeToken();
      expect(auth.getToken()).toBeNull();
    });

    it('returns null when no token is stored', () => {
      expect(auth.getToken()).toBeNull();
    });

    it('persists token across sessions using localStorage on web', () => {
      auth.setToken('persisted-token');
      const cachedToken = auth.getToken();
      expect(cachedToken).toBe('persisted-token');
      expect(mockLocalStorage.getItem('sacrifice_auth_token')).toBe('persisted-token');
    });
  });

  describe('googleLogin', () => {
    it('posts id token to /api/auth/google and returns access_token and user', async () => {
      const expected = { access_token: 'jwt-abc', user: { id: '1', email: 'a@b.com', display_name: 'Alice', auth_provider: 'google' } };
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
    it('posts code to /api/auth/github and returns access_token and user', async () => {
      const expected = { access_token: 'jwt-def', user: { id: '2', email: 'b@c.com', display_name: 'Bob', auth_provider: 'github' } };
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

  describe('exchangeCode', () => {
    it('posts auth code to /api/auth/exchange and returns access_token and user', async () => {
      const expected = { access_token: 'jwt-exchange', user: { id: '3', email: 'c@d.com', display_name: 'Casey' } };
      mockFetch.mockResolvedValueOnce({ ok: true, json: async () => expected });

      const result = await auth.exchangeCode('auth-code-123');
      expect(result).toEqual(expected);
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/auth/exchange'),
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ code: 'auth-code-123' }),
        }),
      );
    });

    it('throws on non-ok response', async () => {
      mockFetch.mockResolvedValueOnce({ ok: false, status: 401 });
      await expect(auth.exchangeCode('bad-auth-code')).rejects.toThrow('Auth exchange failed: 401');
    });
  });

  describe('logout', () => {
    it('posts current bearer token to /api/auth/logout', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true });

      await auth.logout('jwt-logout');
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/auth/logout'),
        expect.objectContaining({
          method: 'POST',
          headers: { Authorization: 'Bearer jwt-logout' },
        }),
      );
    });
  });

  describe('handleRedirectCallback', () => {
    beforeEach(() => {
      mockLocation.hash = '';
      mockLocation.search = '';
      mockLocation.pathname = '/';
      mockLocation.href = 'http://localhost/';
    });

    it('extracts id_token from URL hash fragment', () => {
      mockLocation.hash = 'id_token=google-id-token-123';
      const result = auth.handleRedirectCallback();
      expect(result).toEqual({ token: 'google-id-token-123' });
    });

    it('extracts auth_code from URL query params', () => {
      mockLocation.search = '?auth_code=exchange-code-456';
      mockLocation.href = 'http://localhost/?auth_code=exchange-code-456';
      const result = auth.handleRedirectCallback();
      expect(result).toEqual({ authCode: 'exchange-code-456' });
      expect(mockWindow.history.replaceState).toHaveBeenCalledWith({}, '', 'http://localhost/');
    });

    it('extracts access_token from URL query params', () => {
      mockLocation.search = '?access_token=direct-token-789';
      mockLocation.href = 'http://localhost/?access_token=direct-token-789';
      const result = auth.handleRedirectCallback();
      expect(result).toEqual({ accessToken: 'direct-token-789' });
      expect(mockWindow.history.replaceState).toHaveBeenCalledWith({}, '', 'http://localhost/');
    });

    it('extracts error and optional provider from URL query params', () => {
      mockLocation.search = '?error=access_denied&provider=google';
      mockLocation.href = 'http://localhost/?error=access_denied&provider=google';
      const result = auth.handleRedirectCallback();
      expect(result).toEqual({ error: 'access_denied', provider: 'google' });
      expect(mockWindow.history.replaceState).toHaveBeenCalledWith({}, '', 'http://localhost/');
    });

    it('extracts error without provider when provider param is absent', () => {
      mockLocation.search = '?error=server_error';
      mockLocation.href = 'http://localhost/?error=server_error';
      const result = auth.handleRedirectCallback();
      expect(result).toEqual({ error: 'server_error', provider: undefined });
      expect(mockWindow.history.replaceState).toHaveBeenCalledWith({}, '', 'http://localhost/');
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
