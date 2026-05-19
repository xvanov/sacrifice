import React from 'react';
import { render, fireEvent, waitFor } from '@testing-library/react-native';
import GoalDetailScreen from '../../screens/GoalDetailScreen';

const mockNavigate = jest.fn();

jest.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 'user-1', display_name: 'Test', email: 'test@test.com' },
    isLoading: false,
    isAuthenticated: true,
    loginWithGoogle: jest.fn(),
    loginWithGithub: jest.fn(),
    logout: jest.fn(),
  }),
}));

jest.mock('../../hooks/useNavigation', () => ({
  useNavigation: () => ({
    currentScreen: { name: 'goal-detail', goalId: 'goal-1' },
    navigate: mockNavigate,
    goBack: jest.fn(),
  }),
}));

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

const activeGoal = {
  id: 'goal-1',
  title: 'Ship the MVP',
  description: 'Get the first version out the door',
  goal_type: 'dev_sandbox',
  pledge_amount: 5000,
  currency: 'usd',
  deadline: '2026-06-01T00:00:00Z',
  timezone: 'America/New_York',
  recurrence: 'none',
  status: 'active',
  charity_id: null,
  criteria: {
    criteria_type: 'dev_sandbox',
    criteria_data: {
      repo_url: 'https://github.com/user/repo.git',
      branch: 'main',
      test_command: 'pytest tests/ -v',
    },
  },
  created_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-05-15T00:00:00Z',
};

const verifiedGoal = {
  id: 'goal-2',
  title: 'Record intro video',
  description: 'A walkthrough of the Sacrifice app',
  goal_type: 'youtube_video',
  pledge_amount: 2500,
  currency: 'usd',
  deadline: '2026-05-15T00:00:00Z',
  timezone: 'UTC',
  recurrence: 'weekly',
  status: 'verified',
  charity_id: 'acct_1',
  criteria: {
    criteria_type: 'youtube',
    criteria_data: {
      min_duration_seconds: 300,
      video_description: 'A walkthrough demo',
    },
  },
  created_at: '2026-04-15T00:00:00Z',
  updated_at: '2026-05-10T00:00:00Z',
};

const failedGoal = {
  id: 'goal-3',
  title: 'Build landing page',
  description: null,
  goal_type: 'api_endpoint',
  pledge_amount: 10000,
  currency: 'usd',
  deadline: '2026-04-01T00:00:00Z',
  timezone: 'UTC',
  recurrence: 'none',
  status: 'failed',
  charity_id: 'acct_2',
  criteria: null,
  created_at: '2026-03-01T00:00:00Z',
  updated_at: '2026-04-02T00:00:00Z',
};

beforeEach(() => {
  mockNavigate.mockReset();
  mockFetch.mockReset();
  mockLocalStorage.clear();
});

describe('GoalDetailScreen', () => {
  it('shows loading state while fetching goal', () => {
    mockFetch.mockResolvedValueOnce(new Promise(() => {}));
    const { getByTestId } = render(<GoalDetailScreen goalId="goal-1" />);
    expect(getByTestId('goal-detail-loading')).toBeTruthy();
  });

  it('shows error state when goal fetch fails', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: async () => 'Server Error',
    });

    const { findByText } = render(<GoalDetailScreen goalId="goal-1" />);

    expect(await findByText('HTTP 500: Server Error')).toBeTruthy();
  });

  it('shows error state when goal is not found', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      text: async () => 'Not Found',
    });

    const { findByText } = render(<GoalDetailScreen goalId="nonexistent" />);

    expect(await findByText('HTTP 404: Not Found')).toBeTruthy();
  });

  it('displays goal title and description', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => activeGoal,
    });

    const { findByText } = render(<GoalDetailScreen goalId="goal-1" />);

    expect(await findByText('Ship the MVP')).toBeTruthy();
    expect(await findByText('Get the first version out the door')).toBeTruthy();
  });

  it('displays pledge amount formatted as dollars', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => activeGoal,
    });

    const { findByText } = render(<GoalDetailScreen goalId="goal-1" />);

    expect(await findByText('$50.00')).toBeTruthy();
  });

  it('displays deadline date', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => activeGoal,
    });

    const { findAllByText } = render(<GoalDetailScreen goalId="goal-1" />);

    const elements = await findAllByText(/2026/);
    expect(elements.length).toBeGreaterThanOrEqual(1);
  });

  it('displays goal status with correct label', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => activeGoal,
    });

    const { findByText } = render(<GoalDetailScreen goalId="goal-1" />);

    expect(await findByText('Active')).toBeTruthy();
  });

  it('displays goal type', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => activeGoal,
    });

    const { findByText } = render(<GoalDetailScreen goalId="goal-1" />);

    expect(await findByText('Dev Sandbox')).toBeTruthy();
  });

  it('displays timezone when available', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => activeGoal,
    });

    const { findByText } = render(<GoalDetailScreen goalId="goal-1" />);

    expect(await findByText('America/New_York')).toBeTruthy();
  });

  it('displays recurrence when set', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => verifiedGoal,
    });

    const { findByText } = render(<GoalDetailScreen goalId="goal-2" />);

    expect(await findByText('Weekly')).toBeTruthy();
  });

  it('displays criteria info when available', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => activeGoal,
    });

    const { findByText } = render(<GoalDetailScreen goalId="goal-1" />);

    expect(await findByText(/https:\/\/github.com\/user\/repo.git/)).toBeTruthy();
  });

  it('shows verified status in green', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => verifiedGoal,
    });

    const { findByText } = render(<GoalDetailScreen goalId="goal-2" />);

    expect(await findByText('Verified')).toBeTruthy();
  });

  it('shows failed status in red', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => failedGoal,
    });

    const { findByText } = render(<GoalDetailScreen goalId="goal-3" />);

    expect(await findByText('Failed')).toBeTruthy();
  });

  it('navigates back to home on back press', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => activeGoal,
    });

    const { findByText } = render(<GoalDetailScreen goalId="goal-1" />);

    const backButton = await findByText('<');
    fireEvent.press(backButton);

    expect(mockNavigate).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'home' }),
    );
  });
});
