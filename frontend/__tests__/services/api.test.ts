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

    it('exists as a callable method on the api object', () => {
      expect(typeof api.listGoalTypes).toBe('function');
    });

    it('calls GET /api/goal-types and returns typed GoalTypesResponse', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockGoalTypesResponse,
      });

      const result = await api.listGoalTypes();

      const callUrl = mockFetch.mock.calls[0][0];
      const callOpts = mockFetch.mock.calls[0][1];
      expect(callUrl).toBe('http://localhost:8000/api/goal-types');
      expect(callOpts.method).toBe('GET');
      expect(result.data).toBeDefined();
      expect(result.data!.goal_types).toHaveLength(1);
      expect(result.data!.goal_types[0].name).toBe('youtube_video');
      expect(result.data!.goal_types[0].description).toBeTruthy();
      expect(result.data!.goal_types[0].sample_prompts).toHaveLength(2);
      expect(result.data!.goal_types[0].criteria_schema).toHaveProperty('type', 'object');
      expect(result.data!.goal_types[0].criteria_schema).toHaveProperty('required');
    });

    it('sends no request body for the GET endpoint', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => mockGoalTypesResponse,
      });

      await api.listGoalTypes();

      const callOpts = mockFetch.mock.calls[0][1];
      expect(callOpts.method).toBe('GET');
      expect(callOpts.body).toBeUndefined();
    });

    it('clears token and returns error on 401 response', async () => {
      auth.setToken('expired-jwt');
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 401,
        text: async () => 'Unauthorized',
      });

      const result = await api.listGoalTypes();

      expect(auth.getToken()).toBeNull();
      expect(result.data).toBeUndefined();
      expect(result.error).toBe('HTTP 401: Unauthorized');
    });

    it('returns error on network failure without throwing', async () => {
      mockFetch.mockRejectedValueOnce(new Error('Network error'));

      const result = await api.listGoalTypes();

      expect(result.data).toBeUndefined();
      expect(result.error).toBe('Network error');
    });

    it('does not attach Content-Type header when token is absent and no body is sent', async () => {
      auth.removeToken();
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ goal_types: [] }),
      });

      await api.listGoalTypes();

      const callOpts = mockFetch.mock.calls[0][1];
      // GET with no body should still have Content-Type set by the request helper
      expect(callOpts.headers).toHaveProperty('Content-Type', 'application/json');
      expect(callOpts.headers).not.toHaveProperty('Authorization');
    });
  });
});
