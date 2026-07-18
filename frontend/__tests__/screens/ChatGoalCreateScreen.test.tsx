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

const mockGoalTypesPayload = {
  goal_types: [
    {
      name: 'youtube_video',
      description: 'User uploads a video to YouTube; the system fetches the transcript and an LLM judges whether the content matches the goal description.',
      sample_prompts: ['Post a YouTube walkthrough of my project by Friday'],
      criteria_schema: {
        type: 'object',
        properties: { min_duration_seconds: { type: 'integer' }, video_description: { type: 'string' } },
        required: ['min_duration_seconds', 'video_description'],
      },
    },
    {
      name: 'api_endpoint',
      description: 'User deploys an API endpoint; the system pings it and verifies it returns 200.',
      sample_prompts: ['Deploy a health-check endpoint by Monday'],
      criteria_schema: {
        type: 'object',
        properties: { endpoint_url: { type: 'string' } },
        required: ['endpoint_url'],
      },
    },
    {
      name: 'dev_sandbox',
      description: 'User completes coding tasks in a sandbox environment.',
      sample_prompts: ['Build a working REST API in my sandbox'],
      criteria_schema: {
        type: 'object',
        properties: { repo_url: { type: 'string' }, commit_hash: { type: 'string' } },
        required: ['repo_url', 'commit_hash'],
      },
    },
    {
      name: 'github_repo',
      description: 'User opens a PR on a GitHub repository.',
      sample_prompts: ['Open a PR that adds tests to my project'],
      criteria_schema: {
        type: 'object',
        properties: { repo_url: { type: 'string' }, pr_number: { type: 'integer' } },
        required: ['repo_url', 'pr_number'],
      },
    },
  ],
};

beforeEach(() => {
  mockGoBack.mockReset();
  mockFetch.mockReset();
  mockLocalStorage.clear();
});

function mockGoalTypesResponse() {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => mockGoalTypesPayload,
  });
}

function mockSessionCreated(sessionId = 'sess-new') {
  mockGoalTypesResponse();
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
  mockGoalTypesResponse();
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

    // goalTypes=0, session=1
    const request = getFetchRequest(1);
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
    expect(within(matchCard).getByText('Matched type: Youtube Video')).toBeTruthy();

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

  it('renders registry-backed label and description in match_proposed cards', async () => {
    mockSessionCreatedWithMessages();

    const { findByTestId } = render(<ChatGoalCreateScreen />);

    const matchCard = await findByTestId('match-proposed-card-youtube_video');
    // Registry data from mockGoalTypesPayload drives the display label and
    // description instead of a hardcoded TYPE_LABELS map.
    expect(within(matchCard).getByText('Matched type: Youtube Video')).toBeTruthy();
    expect(
      within(matchCard).getByText(
        'User uploads a video to YouTube; the system fetches the transcript and an LLM judges whether the content matches the goal description.',
      ),
    ).toBeTruthy();
  });

  it('renders match_proposed card with humanized fallback when registry data is still loading', async () => {
    // Leave goal types in-flight so the loading path is exercised.
    // First mock slot: an unresolved promise for listGoalTypes.
    mockFetch.mockImplementationOnce(() => new Promise(() => {}));
    // Second mock slot: session creation resolves normally.
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 201,
      json: async () => ({
        session_id: 'sess-b',
        messages: [
          { role: 'assistant', content: greeting, action: null },
          {
            role: 'assistant',
            content: 'Looks like this is an API Endpoint goal.',
            action: {
              type: 'match_proposed',
              goal_type: 'api_endpoint',
              confidence: 0.8,
              missing_criteria: [],
            },
          },
        ],
        status: 'active',
      }),
    });

    const { findByTestId } = render(<ChatGoalCreateScreen />);

    const matchCard = await findByTestId('match-proposed-card-api_endpoint');
    // Fallback humanized label from the raw name, not from hardcoded TYPE_LABELS.
    expect(within(matchCard).getByText('Matched type: Api Endpoint')).toBeTruthy();
    // Description is not present while registry is loading.
    expect(within(matchCard).getByText('Loading type details…')).toBeTruthy();
  });

  it('shows an error banner when goal-type registry fetch fails', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: async () => 'Server Error',
    });
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 201,
      json: async () => ({
        session_id: 'sess-c',
        messages: [{ role: 'assistant', content: greeting, action: null }],
        status: 'active',
      }),
    });

    const { findByTestId } = render(<ChatGoalCreateScreen />);

    const banner = await findByTestId('goal-types-error-banner');
    expect(banner).toBeTruthy();
    expect(within(banner).getByText("Couldn't load goal-type details. Some labels may be missing.")).toBeTruthy();
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
    // goalTypes=0, session=1, create-goal=2
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
    // ...and a fresh session was created on the server.
    // goalTypes=0, session=1
    expect(getFetchRequest(1).url).toContain('/api/chat/sessions');
    expect(getFetchRequest(1).options.method).toBe('POST');

    fireEvent.changeText(await findByTestId('chat-input'), 'Friday at 5pm');
    fireEvent.press(await findByTestId('send-button'));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(3);
    });
    // The next turn posts to the FRESH session id, not the stale one.
    // goalTypes=0, session=1, user-message=2
    expect(getFetchRequest(2).url).toContain('/api/chat/sessions/sess-fresh/messages');
    expect(await findByText('Thanks — noted.')).toBeTruthy();
  });

  it('surfaces the stubbed build-goal-type response honestly in chat', async () => {
    mockGoalTypesResponse();
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

    // goalTypes=0, session=1, request-new-goal-type=2
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
});
