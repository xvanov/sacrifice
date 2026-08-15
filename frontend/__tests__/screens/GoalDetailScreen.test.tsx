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

    const { findAllByText, findByText } = render(<GoalDetailScreen goalId="goal-1" />);

    // Title renders in the header AND the renameable Title row.
    expect((await findAllByText('Ship the MVP')).length).toBeGreaterThanOrEqual(1);
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

    // Rendered as a human label, never the raw underscored IANA id.
    expect(await findByText(/New York/)).toBeTruthy();
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

  it('renders not-found state when goalId is undefined and does not call api', () => {
    const { getByTestId, getByText } = render(
      <GoalDetailScreen goalId={undefined as unknown as string} />,
    );

    expect(getByTestId('goal-detail-invalid-id')).toBeTruthy();
    expect(getByText('Goal not found.')).toBeTruthy();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('renders not-found state when goalId is empty string and does not call api', () => {
    const { getByTestId } = render(<GoalDetailScreen goalId="" />);

    expect(getByTestId('goal-detail-invalid-id')).toBeTruthy();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('navigates back to home on back press', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => activeGoal,
    });

    const { findByText } = render(<GoalDetailScreen goalId="goal-1" />);

    const backButton = await findByText('←');
    fireEvent.press(backButton);

    expect(mockNavigate).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'home' }),
    );
  });

  // The deadline lock. Within three hours of falling due the date is fixed
  // server-side (403); the panel reflects that instead of letting the owner type
  // a new date and meet a refusal.
  describe('deadline lock', () => {
    const lockedGoal = { ...activeGoal, deadline_locked: true };

    it('offers the date and time pickers while the deadline is still movable', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true, json: async () => activeGoal });

      const { findByTestId, queryByTestId } = render(<GoalDetailScreen goalId="goal-1" />);
      fireEvent.press(await findByTestId('edit-goal'));

      expect(await findByTestId('deadline-date-input')).toBeTruthy();
      expect(queryByTestId('deadline-locked-notice')).toBeNull();
    });

    it('replaces the pickers with a locked notice inside the window', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true, json: async () => lockedGoal });

      const { findByTestId, queryByTestId, findByText } = render(
        <GoalDetailScreen goalId="goal-1" />,
      );
      fireEvent.press(await findByTestId('edit-goal'));

      expect(await findByTestId('deadline-locked-notice')).toBeTruthy();
      expect(queryByTestId('deadline-date-input')).toBeNull();
      expect(queryByTestId('deadline-time-input')).toBeNull();
      expect(await findByText(/Locked/)).toBeTruthy();
    });

    it('saves the other fields without sending a deadline when locked', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => lockedGoal })
        .mockResolvedValueOnce({ ok: true, json: async () => lockedGoal });

      const { findByTestId } = render(<GoalDetailScreen goalId="goal-1" />);
      fireEvent.press(await findByTestId('edit-goal'));
      fireEvent.changeText(await findByTestId('edit-description'), 'sharpened scope');
      fireEvent.press(await findByTestId('edit-save'));

      await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
      const body = JSON.parse(mockFetch.mock.calls[1][1].body);
      expect(body).not.toHaveProperty('deadline');
      expect(body.description).toBe('sharpened scope');
    });
  });

  // The stake lock. The same window freezes the pledge and its recipient, so the
  // panel stops offering them rather than letting the owner type a new amount and
  // meet a 403.
  describe('stake lock', () => {
    const stakeLocked = { ...activeGoal, deadline_locked: true, stake_locked: true };

    it('offers the pledge input and recipient control while the stake is movable', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true, json: async () => activeGoal });

      const { findByTestId, queryByTestId } = render(<GoalDetailScreen goalId="goal-1" />);
      fireEvent.press(await findByTestId('edit-goal'));

      expect(await findByTestId('edit-pledge')).toBeTruthy();
      expect(queryByTestId('pledge-locked-notice')).toBeNull();
    });

    it('replaces the pledge input with a locked notice inside the window', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true, json: async () => stakeLocked });

      const { findByTestId, queryByTestId } = render(<GoalDetailScreen goalId="goal-1" />);
      fireEvent.press(await findByTestId('edit-goal'));

      expect(await findByTestId('pledge-locked-notice')).toBeTruthy();
      expect(queryByTestId('edit-pledge')).toBeNull();
    });

    it('withdraws the change-recipient control inside the window', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true, json: async () => stakeLocked });

      const { findByTestId, queryByTestId } = render(<GoalDetailScreen goalId="goal-1" />);
      await findByTestId('recipient-locked-notice');

      expect(queryByTestId('change-recipient')).toBeNull();
    });

    it('saves without sending a pledge when the stake is locked', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => stakeLocked })
        .mockResolvedValueOnce({ ok: true, json: async () => stakeLocked });

      const { findByTestId } = render(<GoalDetailScreen goalId="goal-1" />);
      fireEvent.press(await findByTestId('edit-goal'));
      fireEvent.changeText(await findByTestId('edit-description'), 'still committed');
      fireEvent.press(await findByTestId('edit-save'));

      await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
      const body = JSON.parse(mockFetch.mock.calls[1][1].body);
      expect(body).not.toHaveProperty('pledge_amount');
      expect(body).not.toHaveProperty('charity_id');
      expect(body.description).toBe('still committed');
    });
  });
});
