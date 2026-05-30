import { api } from '../../services/api';
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

beforeEach(() => {
  mockFetch.mockReset();
  mockLocalStorage.clear();
});

describe('api service', () => {
  describe('health', () => {
    it('calls GET /api/health and returns status', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ status: 'ok' }) });
      const result = await api.health();
      expect(result.data).toEqual({ status: 'ok' });
    });
  });

  describe('authenticated requests', () => {
    it('attaches Bearer token from auth service when token exists', async () => {
      auth.setToken('test-jwt');
      mockFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ id: '1' }) });
      await api.get('/api/goals');
      const callUrl = mockFetch.mock.calls[0][0];
      const callOpts = mockFetch.mock.calls[0][1];
      expect(callUrl).toContain('/api/goals');
      expect(callOpts.headers).toMatchObject({
        'Content-Type': 'application/json',
        Authorization: 'Bearer test-jwt',
      });
    });

    it('does not attach token when none is stored', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ id: '1' }) });
      auth.removeToken();
      await api.get('/api/goals');
      const callOpts = mockFetch.mock.calls[0][1];
      expect(callOpts.headers).not.toHaveProperty('Authorization');
    });

    it('clears token and returns error on 401 response', async () => {
      auth.setToken('expired-jwt');
      mockFetch.mockResolvedValueOnce({ ok: false, status: 401, text: async () => 'Unauthorized' });
      const result = await api.get('/api/protected');
      expect(result.error).toContain('401');
      expect(auth.getToken()).toBeNull();
    });

    it('does not clear token on non-401 errors', async () => {
      auth.setToken('valid-jwt');
      mockFetch.mockResolvedValueOnce({ ok: false, status: 500, text: async () => 'Server Error' });
      await api.get('/api/goals');
      expect(auth.getToken()).toBe('valid-jwt');
    });
  });

  });
