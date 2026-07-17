import React from 'react';
import { render, fireEvent, waitFor, act } from '@testing-library/react-native';
import HomeScreen from '../../screens/HomeScreen';

const mockNavigate = jest.fn();
const mockLogout = jest.fn();

jest.mock('../../components/NotificationBell', () => ({
  NotificationBell: () => null,
}));

jest.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 'user-1', display_name: 'Test User', email: 'test@test.com', avatar_url: null },
    isLoading: false,
    isAuthenticated: true,
    loginWithGoogle: jest.fn(),
    loginWithGithub: jest.fn(),
    logout: mockLogout,
  }),
}));

jest.mock('../../hooks/useNavigation', () => ({
  useNavigation: () => ({
    currentScreen: { name: 'home' },
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

const sampleGoals = [
  {
    id: 'goal-1',
    title: 'Ship the MVP',
    description: 'Get the first version out',
    goal_type: 'dev_sandbox',
    pledge_amount: 5000,
    currency: 'usd',
    deadline: '2026-06-01T00:00:00Z',
    timezone: 'UTC',
    recurrence: 'none',
    status: 'active',
    charity_id: null,
    criteria: null,
    created_at: '2026-05-01T00:00:00Z',
    updated_at: '2026-05-01T00:00:00Z',
  },
  {
    id: 'goal-2',
    title: 'Record intro video',
    description: 'A walkthrough of the app',
    goal_type: 'youtube_video',
    pledge_amount: 2500,
    currency: 'usd',
    deadline: '2026-05-15T00:00:00Z',
    timezone: 'UTC',
    recurrence: 'none',
    status: 'verified',
    charity_id: null,
    criteria: null,
    created_at: '2026-04-15T00:00:00Z',
    updated_at: '2026-05-10T00:00:00Z',
  },
  {
    id: 'goal-3',
    title: 'Build landing page',
    description: 'A landing page for the app',
    goal_type: 'api_endpoint',
    pledge_amount: 10000,
    currency: 'usd',
    deadline: '2026-04-01T00:00:00Z',
    timezone: 'UTC',
    recurrence: 'none',
    status: 'failed',
    charity_id: 'acct_1',
    criteria: null,
    created_at: '2026-03-01T00:00:00Z',
    updated_at: '2026-04-02T00:00:00Z',
  },
];

beforeEach(() => {
  mockNavigate.mockReset();
  mockLogout.mockReset();
  mockFetch.mockReset();
  mockLocalStorage.clear();
});

describe('HomeScreen - Goal List', () => {
  it('renders loading skeleton while fetching goals', () => {
    mockFetch.mockResolvedValueOnce(new Promise(() => {}));
    const { getByTestId } = render(<HomeScreen />);
    expect(getByTestId('goals-loading')).toBeTruthy();
  });

  it('renders empty state when no goals exist', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });

    const { findByText } = render(<HomeScreen />);

    expect(await findByText('No goals yet')).toBeTruthy();
  });

  it('loads and displays goals for the authenticated user', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => sampleGoals,
    });

    const { findByText } = render(<HomeScreen />);

    expect(await findByText('Ship the MVP')).toBeTruthy();
    expect(await findByText('Record intro video')).toBeTruthy();
    expect(await findByText('Build landing page')).toBeTruthy();
  });

  it('shows pledge amounts formatted in dollars', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => sampleGoals,
    });

    const { findByText } = render(<HomeScreen />);

    expect(await findByText('$50.00')).toBeTruthy();
    expect(await findByText('$25.00')).toBeTruthy();
    expect(await findByText('$100.00')).toBeTruthy();
  });

  it('shows goal type labels correctly', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => sampleGoals,
    });

    const { findByText } = render(<HomeScreen />);

    expect(await findByText('Sandbox')).toBeTruthy();
    expect(await findByText('YouTube')).toBeTruthy();
    expect(await findByText('API')).toBeTruthy();
  });

  it('navigates to goal detail on tap', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => sampleGoals,
    });

    const { findByText } = render(<HomeScreen />);

    const goalItem = await findByText('Ship the MVP');
    fireEvent.press(goalItem);

    expect(mockNavigate).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'goal-detail', goalId: 'goal-1' }),
    );
  });

  it('tapping goal detail navigates with correct id', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => sampleGoals,
    });

    const { findByText } = render(<HomeScreen />);

    const goalItem = await findByText('Build landing page');
    fireEvent.press(goalItem);

    expect(mockNavigate).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'goal-detail', goalId: 'goal-3' }),
    );
  });

  it('filter tabs render and default to All', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => sampleGoals,
    });

    const { findByTestId } = render(<HomeScreen />);

    const allTab = await findByTestId('filter-tab-All');
    expect(allTab).toBeTruthy();
  });

  it('filter tabs render Active, Verified, Failed', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => sampleGoals,
    });

    const { findByTestId } = render(<HomeScreen />);

    expect(await findByTestId('filter-tab-All')).toBeTruthy();
    expect(await findByTestId('filter-tab-Active')).toBeTruthy();
    expect(await findByTestId('filter-tab-Verified')).toBeTruthy();
    expect(await findByTestId('filter-tab-Failed')).toBeTruthy();
  });

  it('filter tab Active filters goals to only active ones', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => sampleGoals,
    });

    const { findByTestId, findAllByText } = render(<HomeScreen />);

    const activeTab = await findByTestId('filter-tab-Active');
    fireEvent.press(activeTab);

    const activeBadges = await findAllByText('Active');
    expect(activeBadges.length).toBeGreaterThanOrEqual(1);
  });

  it('filter tab Verified filters goals to only verified ones', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => sampleGoals,
    });

    const { findByTestId, queryByTestId } = render(<HomeScreen />);

    const verifiedTab = await findByTestId('filter-tab-Verified');
    fireEvent.press(verifiedTab);

    expect(await findByTestId('status-badge-verified')).toBeTruthy();
    expect(queryByTestId('status-badge-active')).toBeNull();
    expect(queryByTestId('status-badge-failed')).toBeNull();
  });

  it('filter tab Failed filters goals to only failed ones', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => sampleGoals,
    });

    const { findByTestId, queryByTestId } = render(<HomeScreen />);

    const failedTab = await findByTestId('filter-tab-Failed');
    fireEvent.press(failedTab);

    expect(await findByTestId('status-badge-failed')).toBeTruthy();
    expect(queryByTestId('status-badge-active')).toBeNull();
    expect(queryByTestId('status-badge-verified')).toBeNull();
  });

  it('pull-to-refresh reloads goals from the API', async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [sampleGoals[0]],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => sampleGoals,
      });

    const { findByText, findByTestId } = render(<HomeScreen />);

    expect(await findByText('Ship the MVP')).toBeTruthy();

    const flatList = await findByTestId('goals-list');
    const refreshControl = flatList.props.refreshControl;
    await act(async () => {
      refreshControl.props.onRefresh();
    });

    expect(await findByText('Record intro video')).toBeTruthy();
    expect(await findByText('Build landing page')).toBeTruthy();
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it('shows status badges with correct labels', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => sampleGoals,
    });

    const { findAllByText } = render(<HomeScreen />);

    const activeElements = await findAllByText('Active');
    expect(activeElements.length).toBeGreaterThanOrEqual(1);
    const verifiedElements = await findAllByText('Verified');
    expect(verifiedElements.length).toBeGreaterThanOrEqual(1);
    const failedElements = await findAllByText('Failed');
    expect(failedElements.length).toBeGreaterThanOrEqual(1);
  });

  it('shows deadline dates on goal cards', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => sampleGoals,
    });

    const { findAllByText } = render(<HomeScreen />);

    const elements = await findAllByText(/2026/);
    expect(elements.length).toBeGreaterThanOrEqual(1);
  });

  it('shows the empty-state create goal button', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });

    const { findByTestId, findByText } = render(<HomeScreen />);

    expect(await findByText('No goals yet')).toBeTruthy();
    expect(await findByTestId('create-goal-button')).toBeTruthy();
  });

  it('routes the populated home create shortcut to chat goal creation', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => sampleGoals,
    });

    const { findByTestId } = render(<HomeScreen />);

    const shortcut = await findByTestId('home-create-goal-shortcut');
    fireEvent.press(shortcut);

    expect(mockNavigate).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'chat-goal-create' }),
    );
  });

  it('handles API fetch failure gracefully', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: async () => 'Server Error',
    });

    const { findByText } = render(<HomeScreen />);

    expect(await findByText("Couldn't load your goals")).toBeTruthy();
  });
});
