import React from 'react';
import { fireEvent, render, waitFor, within } from '@testing-library/react-native';
import ChatGoalCreateScreen, {
  CHAT_GOAL_CREATE_SESSION_STORAGE_KEY,
} from '../../screens/ChatGoalCreateScreen';

const mockGoBack = jest.fn();

jest.mock('../../hooks/useNavigation', () => ({
  useNavigation: () => ({
    currentScreen: { name: 'chat-goal-create' },
    navigate: jest.fn(),
    goBack: mockGoBack,
  }),
}));

const mockFetch = jest.fn();
global.fetch = mockFetch as typeof fetch;

const mockLocalStorage = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => {
      store[key] = value;
    },
    removeItem: (key: string) => {
      delete store[key];
    },
    clear: () => {
      store = {};
    },
  };
})();
Object.defineProperty(global, 'localStorage', { value: mockLocalStorage, writable: true });

const greeting = "Tell me what you want to do, and I'll figure out how to track it.";

beforeEach(() => {
  mockGoBack.mockReset();
  mockFetch.mockReset();
  mockLocalStorage.clear();
});

function mockSessionCreated(sessionId = 'sess-new') {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 201,
    json: async () => ({
      session_id: sessionId,
      messages: [{ role: 'assistant', content: greeting, action: null }],
      status: 'active',
    }),
  });
}

function getFetchRequest(callIndex: number) {
  const [url, options] = mockFetch.mock.calls[callIndex] as [string, RequestInit | undefined];
  return { url, options: options ?? {} };
}

function getFetchJsonBody(callIndex: number) {
  const { options } = getFetchRequest(callIndex);
  return JSON.parse(String(options.body ?? '{}'));
}

// Seeds a session whose CREATE response already carries rich messages —
// the screen always starts a fresh server session ("+ New goal" never
// resumes), so structured-card rendering is driven through this response.
function mockSessionCreatedWithMessages() {
  const session = {
    session_id: 'sess-resume',
    messages: [
      { role: 'assistant', content: greeting, action: null },
      {
        role: 'assistant',
        content: 'Looks like this is a YouTube Video goal. I still need your deadline.',
        action: {
          type: 'match_proposed',
          goal_type: 'youtube_video',
          confidence: 0.87,
          missing_criteria: ['deadline'],
        },
      },
      {
        role: 'assistant',
        content: "I don't have a built-in way to verify that yet.",
        action: {
          type: 'no_match',
          suggested_action: 'generate_new_goal_type',
        },
      },
      {
        role: 'assistant',
        content: "What's your deadline?",
        action: {
          type: 'awaiting_input',
          field: 'deadline',
          prompt: "What's your deadline?",
        },
      },
      {
        role: 'assistant',
        content: 'Everything looks good — ready to create this goal.',
        action: {
          type: 'ready_to_create',
          goal_payload: {
            title: 'YouTube walkthrough',
            deadline: '2026-06-20T17:00:00Z',
            pledge_amount: 2000,
            goal_type: 'youtube_video',
          },
        },
      },
    ],
    draft_goal: { goal_type: 'youtube_video' },
  };

  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 201,
    json: async () => ({ ...session, status: 'active' }),
  });

  return session;
}

describe('ChatGoalCreateScreen', () => {
  it('creates a new session, renders the greeting, and caches it locally', async () => {
    mockSessionCreated('sess-123');

    const { findByTestId, findByText } = render(<ChatGoalCreateScreen />);

    expect(await findByTestId('chat-message-list')).toBeTruthy();
    expect(await findByText(greeting)).toBeTruthy();

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(1);
    });

    const request = getFetchRequest(0);
    expect(request.url).toContain('/api/chat/sessions');
    expect(request.options.method).toBe('POST');

    expect(JSON.parse(mockLocalStorage.getItem(CHAT_GOAL_CREATE_SESSION_STORAGE_KEY) ?? '{}')).toEqual({
      session_id: 'sess-123',
      messages: [{ role: 'assistant', content: greeting, action: null }],
      draft_goal: null,
    });
  });

  it('disables send for empty or whitespace-only input', async () => {
    mockSessionCreated();

    const { findByTestId } = render(<ChatGoalCreateScreen />);

    const input = await findByTestId('chat-input');
    expect((await findByTestId('send-button')).props.accessibilityState.disabled).toBe(true);

    fireEvent.changeText(input, '   ');
    expect((await findByTestId('send-button')).props.accessibilityState.disabled).toBe(true);

    fireEvent.changeText(input, 'Ship the walkthrough');
    expect((await findByTestId('send-button')).props.accessibilityState.disabled).toBe(false);
  });

  it('renders structured assistant affordance cards from session messages', async () => {
    mockSessionCreatedWithMessages();

    const { findByTestId, findByText } = render(<ChatGoalCreateScreen />);

    expect(await findByText(greeting)).toBeTruthy();

    const matchCard = await findByTestId('match-proposed-card-youtube_video');
    expect(within(matchCard).getByText('Use this goal type')).toBeTruthy();
    expect(within(matchCard).getByText('Matched type: YouTube Video')).toBeTruthy();

    const buildCard = await findByTestId('build-new-goal-type-card');
    expect(within(buildCard).getByText('Build a new goal type')).toBeTruthy();
    expect(within(buildCard).getByText('Yes, build it')).toBeTruthy();

    const awaitingCard = await findByTestId('awaiting-input-deadline');
    expect(within(awaitingCard).getByText('Awaiting input')).toBeTruthy();
    expect(within(awaitingCard).getByText("What's your deadline?")).toBeTruthy();

    const readyCard = await findByTestId('ready-to-create-card');
    expect(within(readyCard).getByText('Ready to create')).toBeTruthy();
    expect(within(readyCard).getByText('title: YouTube walkthrough')).toBeTruthy();
    expect(within(readyCard).getByText('Create goal')).toBeTruthy();
  });

  it('ready_to_create confirm calls create-goal with the action payload and reports success', async () => {
    mockSessionCreatedWithMessages();
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 201,
      json: async () => ({ goal_id: 'goal-123', status: 'active' }),
    } as Response);

    const { findByTestId, findByText } = render(<ChatGoalCreateScreen />);

    const readyCard = await findByTestId('ready-to-create-card');
    fireEvent.press(within(readyCard).getByTestId('create-goal-confirm'));

    expect(await findByText(/goal is created and active/i)).toBeTruthy();
    const { url } = getFetchRequest(1);
    expect(url).toContain('/api/chat/sessions/sess-resume/create-goal');
    expect(getFetchJsonBody(1)).toEqual({
      goal_payload: {
        title: 'YouTube walkthrough',
        deadline: '2026-06-20T17:00:00Z',
        pledge_amount: 2000,
        goal_type: 'youtube_video',
      },
    });
  });

  it('ignores stale stored sessions and always starts fresh', async () => {
    // A leftover session in storage (possibly referencing a server row that
    // no longer exists) must NOT be resumed: "+ New goal" always creates a
    // fresh session, so dead sessions can't loop "Session not found" errors.
    mockLocalStorage.setItem(
      CHAT_GOAL_CREATE_SESSION_STORAGE_KEY,
      JSON.stringify({
        session_id: 'sess-stale-dead',
        messages: [
          { role: 'assistant', content: greeting, action: null },
          { role: 'user', content: 'wake up on time', action: null },
        ],
        draft_goal: { goal_type: 'youtube_video' },
        generating: true,
      }),
    );
    mockSessionCreated('sess-fresh');
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        messages: [
          { role: 'assistant', content: greeting, action: null },
          { role: 'user', content: 'Friday at 5pm', action: null },
          { role: 'assistant', content: 'Thanks — noted.', action: null },
        ],
        draft_goal: null,
      }),
    });

    const { findByTestId, findByText, queryByText } = render(<ChatGoalCreateScreen />);

    expect(await findByText(greeting)).toBeTruthy();
    // The stale conversation is not shown...
    expect(queryByText('wake up on time')).toBeNull();
    // ...and a fresh session was created on the server.
    expect(getFetchRequest(0).url).toContain('/api/chat/sessions');
    expect(getFetchRequest(0).options.method).toBe('POST');

    fireEvent.changeText(await findByTestId('chat-input'), 'Friday at 5pm');
    fireEvent.press(await findByTestId('send-button'));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(2);
    });
    // The next turn posts to the FRESH session id, not the stale one.
    expect(getFetchRequest(1).url).toContain('/api/chat/sessions/sess-fresh/messages');
    expect(await findByText('Thanks — noted.')).toBeTruthy();
  });

  it('surfaces the stubbed build-goal-type response honestly in chat', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 201,
      json: async () => ({
        session_id: 'sess-resume',
        messages: [
          { role: 'assistant', content: greeting, action: null },
          { role: 'user', content: 'Track my water intake', action: null },
          {
            role: 'assistant',
            content: "I don't have a built-in way to verify that yet.",
            action: {
              type: 'no_match',
              suggested_action: 'generate_new_goal_type',
            },
          },
        ],
        status: 'active',
      }),
    });
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 501,
      text: async () => '{"detail":"Goal-type generation is delivered in D010"}',
    });

    const { findByTestId, findByText } = render(<ChatGoalCreateScreen />);

    fireEvent.press(await findByTestId('yes-build-it'));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(2);
    });

    const request = getFetchRequest(1);
    expect(request.url).toContain('/api/chat/sessions/sess-resume/request-new-goal-type');
    expect(getFetchJsonBody(1)).toEqual({
      prompt_summary: 'Track my water intake',
      goal_payload_draft: {},
      chat_history: [
        { role: 'assistant', content: greeting },
        { role: 'user', content: 'Track my water intake' },
        { role: 'assistant', content: "I don't have a built-in way to verify that yet." },
      ],
    });
    expect(await findByText("Goal-type generation isn't enabled yet — coming in D010.")).toBeTruthy();
  });

  it('goes back to the previous screen', async () => {
    mockSessionCreated();

    const { findByTestId } = render(<ChatGoalCreateScreen />);

    fireEvent.press(await findByTestId('back-to-home'));

    expect(mockGoBack).toHaveBeenCalledTimes(1);
  });
});
