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

  it('returns the full response payload with each goal type preserving required fields', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => mockGoalTypesResponse,
    });

    const result = await api.listGoalTypes();

    expect(result.error).toBeUndefined();
    expect(result.data).toBeDefined();
    expect(result.data!.goal_types).toHaveLength(2);

    for (const gt of result.data!.goal_types) {
      expect(gt).toHaveProperty('name');
      expect(gt).toHaveProperty('description');
      expect(gt).toHaveProperty('sample_prompts');
      expect(gt).toHaveProperty('criteria_schema');
      expect(typeof gt.name).toBe('string');
      expect(typeof gt.description).toBe('string');
      expect(Array.isArray(gt.sample_prompts)).toBe(true);
      expect(typeof gt.criteria_schema).toBe('object');
    }
  });

  it('returns an empty goal_types array when the registry has no types', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ goal_types: [] }),
    });

    const result = await api.listGoalTypes();

    expect(result.error).toBeUndefined();
    expect(result.data).toEqual({ goal_types: [] });
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

  it('returns error but preserves token on non-401 server errors', async () => {
    auth.setToken('valid-jwt');
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: async () => 'Internal Server Error',
    });

    const result = await api.listGoalTypes();

    expect(result.data).toBeUndefined();
    expect(result.error).toBe('HTTP 500: Internal Server Error');
    expect(auth.getToken()).toBe('valid-jwt');
  });

  it('returns error when fetch itself rejects (network failure)', async () => {
    mockFetch.mockRejectedValueOnce(new Error('Network request failed'));

    const result = await api.listGoalTypes();

    expect(result.data).toBeUndefined();
    expect(result.error).toBe('Network request failed');
  });
});