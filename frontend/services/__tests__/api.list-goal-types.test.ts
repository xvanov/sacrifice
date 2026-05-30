import { api } from '../api';
import { auth } from '../auth';

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
      {
        name: 'github_repo',
        description: 'User pushes commits to a GitHub repository; the system verifies the commits exist and match the goal criteria.',
        sample_prompts: [
          'Push 3 commits to my project repo by Sunday',
        ],
        criteria_schema: {
          type: 'object',
          properties: {
            repo_url: { type: 'string' },
            min_commits: { type: 'integer' },
          },
          required: ['repo_url', 'min_commits'],
        },
      },
    ],
  };

  it('returns the full response payload contract-preserving from GET /api/goal-types', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => mockGoalTypesResponse,
    });

    const result = await api.listGoalTypes();

    expect(result.error).toBeUndefined();
    expect(result.data).toEqual(mockGoalTypesResponse);
  });

  it('targets GET /api/goal-types with correct URL and no body', async () => {
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

  it('returns error and clears token on 401 response', async () => {
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
  });
});