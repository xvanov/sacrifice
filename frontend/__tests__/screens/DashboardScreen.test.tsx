import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';
import DashboardScreen from '../../screens/DashboardScreen';

const mockNavigate = jest.fn();
const mockGoBack = jest.fn();

jest.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 'user-1', display_name: 'Test User', email: 'test@test.com', avatar_url: null },
    isLoading: false,
    isAuthenticated: true,
    loginWithGoogle: jest.fn(),
    loginWithGithub: jest.fn(),
    logout: jest.fn(),
  }),
}));

jest.mock('../../hooks/useNavigation', () => ({
  useNavigation: () => ({
    currentScreen: { name: 'dashboard' },
    navigate: mockNavigate,
    goBack: mockGoBack,
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

const sampleStats = {
  total_goals: 5,
  completed_count: 3,
  failed_count: 1,
  success_rate: 75.0,
  total_pledged: 25000,
  total_donated: 5000,
  total_saved: 15000,
};

const sampleHistory = [
  {
    id: 'goal-1',
    title: 'Ship the MVP',
    status: 'verified',
    goal_type: 'dev_sandbox',
    pledge_amount: 10000,
    deadline: '2026-06-01T00:00:00Z',
    created_at: '2026-05-01T00:00:00Z',
  },
  {
    id: 'goal-2',
    title: 'Record intro video',
    status: 'failed',
    goal_type: 'youtube_video',
    pledge_amount: 5000,
    deadline: '2026-05-15T00:00:00Z',
    created_at: '2026-04-15T00:00:00Z',
  },
  {
    id: 'goal-3',
    title: 'Build landing page',
    status: 'active',
    goal_type: 'api_endpoint',
    pledge_amount: 10000,
    deadline: '2026-07-01T00:00:00Z',
    created_at: '2026-03-01T00:00:00Z',
  },
];

beforeEach(() => {
  mockNavigate.mockReset();
  mockGoBack.mockReset();
  mockFetch.mockReset();
  mockLocalStorage.clear();
});

describe('DashboardScreen - Stats', () => {
  it('shows loading state initially', () => {
    mockFetch.mockResolvedValue(new Promise(() => {}));
    const { getByTestId } = render(<DashboardScreen />);
    expect(getByTestId('loading-indicator')).toBeTruthy();
  });

  it('renders stat cards with correct values', async () => {
    mockFetch
      .mockResolvedValueOnce({ ok: true, json: async () => sampleStats })
      .mockResolvedValueOnce({ ok: true, json: async () => sampleHistory });

    const { findByTestId, findByText } = render(<DashboardScreen />);

    expect(await findByTestId('stat-total-goals')).toBeTruthy();
    expect(await findByText('5')).toBeTruthy();
    expect(await findByText('75%')).toBeTruthy();
    expect(await findByText('Total Donated')).toBeTruthy();
    expect(await findByText('Total Saved')).toBeTruthy();
    expect(await findByTestId('stat-total-donated')).toBeTruthy();
    expect(await findByTestId('stat-total-saved')).toBeTruthy();
  });

  it('shows stat card labels correctly', async () => {
    mockFetch
      .mockResolvedValueOnce({ ok: true, json: async () => sampleStats })
      .mockResolvedValueOnce({ ok: true, json: async () => sampleHistory });

    const { findByText } = render(<DashboardScreen />);

    expect(await findByText('Total Goals')).toBeTruthy();
    expect(await findByText('Success Rate')).toBeTruthy();
    expect(await findByText('Total Donated')).toBeTruthy();
    expect(await findByText('Total Saved')).toBeTruthy();
  });

  it('handles API fetch failure for stats', async () => {
    mockFetch
      .mockResolvedValueOnce({ ok: false, status: 500, text: async () => 'Server Error' })
      .mockResolvedValueOnce({ ok: true, json: async () => sampleHistory });

    const { findByTestId } = render(<DashboardScreen />);

    expect(await findByTestId('dashboard-error')).toBeTruthy();
  });
});

describe('DashboardScreen - History', () => {
  it('renders history list with goals', async () => {
    mockFetch
      .mockResolvedValueOnce({ ok: true, json: async () => sampleStats })
      .mockResolvedValueOnce({ ok: true, json: async () => sampleHistory });

    const { findByText, findByTestId } = render(<DashboardScreen />);

    expect(await findByText('History')).toBeTruthy();
    expect(await findByText('Ship the MVP')).toBeTruthy();
    expect(await findByText('Record intro video')).toBeTruthy();
    expect(await findByText('Build landing page')).toBeTruthy();
    expect(await findByTestId('history-list')).toBeTruthy();
  });

  it('shows empty state when no history exists', async () => {
    mockFetch
      .mockResolvedValueOnce({ ok: true, json: async () => sampleStats })
      .mockResolvedValueOnce({ ok: true, json: async () => [] });

    const { findByText } = render(<DashboardScreen />);

    expect(await findByText('No history yet')).toBeTruthy();
  });

  it('renders history items with status and amounts', async () => {
    mockFetch
      .mockResolvedValueOnce({ ok: true, json: async () => sampleStats })
      .mockResolvedValueOnce({ ok: true, json: async () => sampleHistory });

    const { findAllByText, findByText } = render(<DashboardScreen />);

    expect(await findByText('Verified')).toBeTruthy();
    expect(await findByText('Failed')).toBeTruthy();
    expect(await findByText('Active')).toBeTruthy();
    const hundredDollarElements = await findAllByText('$100.00');
    expect(hundredDollarElements.length).toBeGreaterThanOrEqual(1);
    const fiftyDollarElements = await findAllByText('$50.00');
    expect(fiftyDollarElements.length).toBeGreaterThanOrEqual(1);
  });

  it('navigates to goal detail on history item press', async () => {
    mockFetch
      .mockResolvedValueOnce({ ok: true, json: async () => sampleStats })
      .mockResolvedValueOnce({ ok: true, json: async () => sampleHistory });

    const { findByTestId } = render(<DashboardScreen />);

    const goalItem = await findByTestId('history-item-goal-1');
    fireEvent.press(goalItem);

    expect(mockNavigate).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'goal-detail', goalId: 'goal-1' }),
    );
  });

  it('history item press navigates with correct goal id', async () => {
    mockFetch
      .mockResolvedValueOnce({ ok: true, json: async () => sampleStats })
      .mockResolvedValueOnce({ ok: true, json: async () => sampleHistory });

    const { findByTestId } = render(<DashboardScreen />);

    const goalItem = await findByTestId('history-item-goal-3');
    fireEvent.press(goalItem);

    expect(mockNavigate).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'goal-detail', goalId: 'goal-3' }),
    );
  });

  it('back button navigates to home', async () => {
    mockFetch
      .mockResolvedValueOnce({ ok: true, json: async () => sampleStats })
      .mockResolvedValueOnce({ ok: true, json: async () => sampleHistory });

    const { findByText } = render(<DashboardScreen />);

    const backButton = await findByText('←');
    fireEvent.press(backButton);

    expect(mockNavigate).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'home' }),
    );
  });
});
