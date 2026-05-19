import React from 'react';
import { render, fireEvent, waitFor } from '@testing-library/react-native';
import NotificationListScreen from '../../screens/NotificationListScreen';

const mockNavigate = jest.fn();
const mockGoBack = jest.fn();

jest.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 'user-1', display_name: 'Test User', email: 'test@test.com', avatar_url: null },
    isLoading: false,
    isAuthenticated: true,
  }),
}));

jest.mock('../../hooks/useNavigation', () => ({
  useNavigation: () => ({
    currentScreen: { name: 'notifications' },
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

const sampleNotifications = [
  {
    id: 'notif-1',
    user_id: 'user-1',
    goal_id: 'goal-1',
    type: 'goal_created',
    title: 'Goal Created: Ship the MVP',
    body: 'Your goal has been created.',
    read: false,
    created_at: '2026-05-18T12:00:00Z',
  },
  {
    id: 'notif-2',
    user_id: 'user-1',
    goal_id: 'goal-2',
    type: 'goal_completed',
    title: 'Goal Completed: Record intro video',
    body: 'Your goal has been completed successfully!',
    read: false,
    created_at: '2026-05-18T11:00:00Z',
  },
  {
    id: 'notif-3',
    user_id: 'user-1',
    goal_id: 'goal-3',
    type: 'proof_received',
    title: 'Proof Received: Build landing page',
    body: 'Your proof submission has been received.',
    read: true,
    created_at: '2026-05-18T10:00:00Z',
  },
];

beforeEach(() => {
  mockFetch.mockClear();
  mockNavigate.mockClear();
  mockGoBack.mockClear();
  mockLocalStorage.clear();
  mockLocalStorage.setItem('auth_token', 'test-token');
});

describe('NotificationListScreen', () => {
  it('shows loading state initially', () => {
    mockFetch.mockImplementation(() => new Promise(() => {}));
    const { getByTestId } = render(<NotificationListScreen />);
    expect(getByTestId('notifications-loading')).toBeTruthy();
  });

  it('shows empty state when no notifications', async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes('/api/notifications/unread-count')) {
        return { ok: true, json: async () => ({ unread_count: 0 }) };
      }
      return { ok: true, json: async () => [] };
    });
    const { findByText } = render(<NotificationListScreen />);
    expect(await findByText(/no notifications/i)).toBeTruthy();
  });

  it('renders list of notifications', async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes('/api/notifications/unread-count')) {
        return { ok: true, json: async () => ({ unread_count: 2 }) };
      }
      return { ok: true, json: async () => sampleNotifications };
    });
    const { findByText } = render(<NotificationListScreen />);
    expect(await findByText('Goal Created: Ship the MVP')).toBeTruthy();
    expect(await findByText('Goal Completed: Record intro video')).toBeTruthy();
    expect(await findByText('Proof Received: Build landing page')).toBeTruthy();
  });

  it('shows unread badge next to unread notifications', async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes('/api/notifications/unread-count')) {
        return { ok: true, json: async () => ({ unread_count: 2 }) };
      }
      return { ok: true, json: async () => sampleNotifications };
    });
    const { findAllByTestId } = render(<NotificationListScreen />);
    const unreadBadges = await findAllByTestId('unread-badge');
    expect(unreadBadges.length).toBe(2);
  });

  it('tapping a notification navigates to goal detail', async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes('/api/notifications/unread-count')) {
        return { ok: true, json: async () => ({ unread_count: 0 }) };
      }
      return { ok: true, json: async () => sampleNotifications };
    });
    const { findByTestId } = render(<NotificationListScreen />);
    const notifItem = await findByTestId('notification-notif-1');
    fireEvent.press(notifItem);
    expect(mockNavigate).toHaveBeenCalledWith({ name: 'goal-detail', goalId: 'goal-1' });
  });

  it('mark all as read button calls the API', async () => {
    let markAllCalled = false;
    mockFetch.mockImplementation(async (url: string, opts?: RequestInit) => {
      if (url.includes('/api/notifications/read-all')) {
        markAllCalled = true;
        return { ok: true, json: async () => ({ status: 'ok' }) };
      }
      if (url.includes('/api/notifications/unread-count')) {
        return { ok: true, json: async () => ({ unread_count: 2 }) };
      }
      return { ok: true, json: async () => sampleNotifications };
    });
    const { findByText } = render(<NotificationListScreen />);
    const markAllBtn = await findByText(/mark all read/i);
    fireEvent.press(markAllBtn);
    expect(markAllCalled).toBe(true);
  });
});

describe('NotificationBell component', () => {
  it('renders bell icon with unread count', async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes('/api/notifications/unread-count')) {
        return { ok: true, json: async () => ({ unread_count: 3 }) };
      }
      return { ok: true, json: async () => [] };
    });
    const { NotificationBell } = require('../../components/NotificationBell');
    const { findByText, findByTestId } = render(<NotificationBell />);
    expect(await findByTestId('notification-bell')).toBeTruthy();
    expect(await findByText('3')).toBeTruthy();
  });

  it('does not show badge when count is 0', async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes('/api/notifications/unread-count')) {
        return { ok: true, json: async () => ({ unread_count: 0 }) };
      }
      return { ok: true, json: async () => [] };
    });
    const { NotificationBell } = require('../../components/NotificationBell');
    const { findByTestId, queryByTestId } = render(<NotificationBell />);
    expect(await findByTestId('notification-bell')).toBeTruthy();
    expect(queryByTestId('unread-badge')).toBeNull();
  });

  it('tapping bell navigates to notifications screen', async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes('/api/notifications/unread-count')) {
        return { ok: true, json: async () => ({ unread_count: 0 }) };
      }
      return { ok: true, json: async () => [] };
    });
    const { NotificationBell } = require('../../components/NotificationBell');
    const { findByTestId } = render(<NotificationBell />);
    const bell = await findByTestId('notification-bell');
    fireEvent.press(bell);
    expect(mockNavigate).toHaveBeenCalledWith({ name: 'notifications' });
  });
});
