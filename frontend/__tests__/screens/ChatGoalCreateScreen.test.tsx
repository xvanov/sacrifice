import React from 'react';
import { fireEvent, render, waitFor, within } from '@testing-library/react-native';
import ChatGoalCreateScreen, {
  CHAT_GOAL_CREATE_SESSION_STORAGE_KEY,
} from '../../screens/ChatGoalCreateScreen';
import { typeLabel as statusBadgeTypeLabel, setDynamicTypeLabels } from '../../components/StatusBadge';

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

function mockGoalTypesResponse() {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => ({
      goal_types: [
        { name: 'youtube_video', description: 'Verify a YouTube video.', sample_prompts: [], criteria_schema: {} },
        { name: 'api_endpoint', description: 'Ping an API endpoint.', sample_prompts: [], criteria_schema: {} },
        { name: 'dev_sandbox', description: 'Run tests in a sandbox.', sample_prompts: [], criteria_schema: {} },
        { name: 'github_repo', description: 'Push commits to a repo.', sample_prompts: [], criteria_schema: {} },
        { name: 'geolocation', description: 'Visit a location.', sample_prompts: [], criteria_schema: {} },
      ],
    }),
  });
}

beforeEach(() => {
  mockGoBack.mockReset();
  mockFetch.mockReset();
  mockLocalStorage.clear();
  // Reset module-level status-badge labels between tests.
  setDynamicTypeLabels(null);
  // The first call on mount is always listGoalTypes() from useGoalTypeLabels.
  mockGoalTypesResponse();
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
      expect(mockFetch).toHaveBeenCalledTimes(2);
    });

    const sessionRequest = getFetchRequest(1);
    expect(sessionRequest.url).toContain('/api/chat/sessions');
    expect(sessionRequest.options.method).toBe('POST');

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
    expect(within(matchCard).getByText('Matched type: Verify a YouTube video')).toBeTruthy();

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
    // The first mock call (index 0) is listGoalTypes (via beforeEach).
    // The second mock call (index 1) is the session create from mockSessionCreatedWithMessages.
    // This is the third call: POST create-goal.
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 201,
      json: async () => ({ goal_id: 'goal-123', status: 'active' }),
    } as Response);

    const { findByTestId, findByText } = render(<ChatGoalCreateScreen />);

    const readyCard = await findByTestId('ready-to-create-card');
    fireEvent.press(within(readyCard).getByTestId('create-goal-confirm'));

    expect(await findByText(/goal is created and active/i)).toBeTruthy();
    const { url } = getFetchRequest(2);
    expect(url).toContain('/api/chat/sessions/sess-resume/create-goal');
    expect(getFetchJsonBody(2)).toEqual({
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
    // ...and a fresh session was created on the server (index 1; 0 is listGoalTypes).
    expect(getFetchRequest(1).url).toContain('/api/chat/sessions');
    expect(getFetchRequest(1).options.method).toBe('POST');

    fireEvent.changeText(await findByTestId('chat-input'), 'Friday at 5pm');
    fireEvent.press(await findByTestId('send-button'));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(3);
    });
    // The next turn posts to the FRESH session id, not the stale one.
    expect(getFetchRequest(2).url).toContain('/api/chat/sessions/sess-fresh/messages');
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
      expect(mockFetch).toHaveBeenCalledTimes(3);
    });

    const request = getFetchRequest(2);
    expect(request.url).toContain('/api/chat/sessions/sess-resume/request-new-goal-type');
    expect(getFetchJsonBody(2)).toEqual({
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

  it('fetches goal types from /api/goal-types on mount', async () => {
    mockSessionCreated();

    render(<ChatGoalCreateScreen />);

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(2);
    });
    const { url } = getFetchRequest(0);
    expect(url).toContain('/api/goal-types');
  });

  it('renders backend-registered goal types from /api/goal-types in the picker UI', async () => {
    // beforeEach already provides goal types (youtube_video, api_endpoint, dev_sandbox, github_repo, geolocation)
    // The session messages include a match_proposed for youtube_video. The dynamic labels from
    // /api/goal-types should be applied to the StatusBadge module.
    mockSessionCreatedWithMessages();

    const { findByTestId } = render(<ChatGoalCreateScreen />);

    // First verify the match_proposed card renders (uses typeLabel from StatusBadge).
    // After the fetch and effect, setDynamicTypeLabels sets _dynamicLabels.
    // We verify the label via the module-level typeLabel function directly.
    await findByTestId('match-proposed-card-youtube_video');

    // The component's useEffect calls setDynamicTypeLabels after goalTypeLabels loads.
    // waitFor polls until the module's typeLabel reflects the dynamic labels.
    await waitFor(() => {
      expect(statusBadgeTypeLabel('youtube_video')).toBe('Verify a YouTube video');
    }, { timeout: 3000 });
  });

  it('renders a goal type that was only in /api/goal-types without hardcoded source lists', async () => {
    // This simulates a backend-registered goal type that is NOT in
    // the old hardcoded constants — proving the frontend no longer
    // relies on a hardcoded list.
    mockFetch.mockReset(); // clear beforeEach goal types
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        goal_types: [
          { name: 'strava_run', description: 'Verify a Strava running activity.', sample_prompts: [], criteria_schema: {} },
        ],
      }),
    });
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 201,
      json: async () => ({
        session_id: 'sess-backend-only',
        messages: [
          { role: 'assistant', content: greeting, action: null },
          {
            role: 'assistant',
            content: 'Looks like this is a Strava run goal.',
            action: {
              type: 'match_proposed',
              goal_type: 'strava_run',
              confidence: 0.8,
              missing_criteria: ['deadline'],
            },
          },
        ],
        status: 'active',
      }),
    });

    const { findByTestId } = render(<ChatGoalCreateScreen />);

    await findByTestId('match-proposed-card-strava_run');

    // Prove the component wired up setDynamicTypeLabels from the hook's labels.
    // The /api/goal-types response was mocked with only strava_run, and the
    // component should have called setDynamicTypeLabels with buildLabels() output.
    await waitFor(() => {
      expect(statusBadgeTypeLabel('strava_run')).toBe('Verify a Strava running activity');
    }, { timeout: 3000 });
  });

  it('survives a goal-types fetch failure without crashing', async () => {
    // Clear the before-each goal types and make listGoalTypes fail
    mockFetch.mockReset();
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: async () => 'Internal Server Error',
    });
    mockSessionCreated('sess-recovery');

    const { findByTestId, findByText } = render(<ChatGoalCreateScreen />);

    // Screen should still render the chat greeting — goal types are non-critical.
    expect(await findByTestId('chat-message-list')).toBeTruthy();
    expect(await findByText(greeting)).toBeTruthy();
    // The label fallback still works because FALLBACK_TYPE_LABELS is used when the fetch fails.
  });
});
