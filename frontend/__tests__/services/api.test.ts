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
  auth.removeToken();
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
      await api.get('/api/goals');
      const callOpts = mockFetch.mock.calls[0][1];
      expect(callOpts.headers).not.toHaveProperty('Authorization');
    });

    it('refreshes once and retries the original request after a 401', async () => {
      auth.setSession('expired-jwt', 'refresh-jwt');
      mockFetch
        .mockResolvedValueOnce({ ok: false, status: 401, text: async () => 'Unauthorized' })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ access_token: 'fresh-jwt', refresh_token: 'fresh-refresh-jwt' }),
        })
        .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ id: '1' }) });

      const result = await api.get('/api/protected');

      expect(result.data).toEqual({ id: '1' });
      expect(auth.getToken()).toBe('fresh-jwt');
      expect(auth.getRefreshToken()).toBe('fresh-refresh-jwt');
      expect(mockFetch).toHaveBeenCalledTimes(3);
      expect(mockFetch.mock.calls[1][0]).toContain('/api/auth/refresh');
      expect(mockFetch.mock.calls[2][1].headers).toMatchObject({
        Authorization: 'Bearer fresh-jwt',
      });
    });

    it('clears tokens and returns an error when refresh cannot recover a 401', async () => {
      auth.setSession('expired-jwt', 'refresh-jwt');
      mockFetch
        .mockResolvedValueOnce({ ok: false, status: 401, text: async () => 'Unauthorized' })
        .mockResolvedValueOnce({ ok: false, status: 401, text: async () => 'Unauthorized' });

      const result = await api.get('/api/protected');

      expect(result.error).toContain('401');
      expect(auth.getToken()).toBeNull();
      expect(auth.getRefreshToken()).toBeNull();
      expect(mockFetch).toHaveBeenCalledTimes(2);
    });

    it('does not clear token on non-401 errors', async () => {
      auth.setToken('valid-jwt');
      mockFetch.mockResolvedValueOnce({ ok: false, status: 500, text: async () => 'Server Error' });
      await api.get('/api/goals');
      expect(auth.getToken()).toBe('valid-jwt');
    });
  });

  describe('listGoalTypes', () => {
    const mockGoalTypesResponse = {
      goal_types: [
        {
          name: 'youtube_video',
          description: 'User uploads a video to YouTube; the system fetches the transcript and an LLM judges whether the content matches the goal description.',
          sample_prompts: [
            'Post a YouTube walkthrough of my project by Friday',
            'Record a 5-minute video explaining my refactor',
          ],
          criteria_schema: {
            type: 'object',
            properties: {
              min_duration_seconds: { type: 'integer' },
              video_description: { type: 'string' },
            },
            required: ['min_duration_seconds', 'video_description'],
          },
        },
      ],
    };

    it('makes a GET request to /api/goal-types with no request body', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ goal_types: [] }),
      });

      await api.listGoalTypes();

      expect(mockFetch).toHaveBeenCalledTimes(1);
      const callUrl = mockFetch.mock.calls[0][0];
      const callOpts = mockFetch.mock.calls[0][1];
      expect(callUrl).toBe('http://localhost:8000/api/goal-types');
      expect(callOpts.method).toBe('GET');
      expect(callOpts.body).toBeUndefined();
    });

    it('returns the full response payload matching the api_spec.md contract', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockGoalTypesResponse,
      });

      const result = await api.listGoalTypes();

      expect(result.error).toBeUndefined();
      expect(result.data).toEqual(mockGoalTypesResponse);
      expect(mockFetch).toHaveBeenCalledTimes(1);
      const callUrl = mockFetch.mock.calls[0][0];
      const callOpts = mockFetch.mock.calls[0][1];
      expect(callUrl).toBe('http://localhost:8000/api/goal-types');
      expect(callOpts.method).toBe('GET');
      expect(callOpts.body).toBeUndefined();
    });

    it('clears token and returns error on 401 unauthenticated response', async () => {
      auth.setToken('expired-jwt');
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 401,
        text: async () => 'Unauthorized',
      });

      const result = await api.listGoalTypes();

      expect(result.data).toBeUndefined();
      expect(result.error).toBe('HTTP 401: Unauthorized');
      expect(auth.getToken()).toBeNull();
      expect(mockFetch).toHaveBeenCalledTimes(1);
      const callUrl = mockFetch.mock.calls[0][0];
      const callOpts = mockFetch.mock.calls[0][1];
      expect(callUrl).toBe('http://localhost:8000/api/goal-types');
      expect(callOpts.method).toBe('GET');
      expect(callOpts.body).toBeUndefined();
    });
  });
});
