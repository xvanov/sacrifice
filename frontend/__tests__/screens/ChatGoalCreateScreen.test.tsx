import React from 'react';
import { render, fireEvent, waitFor, within } from '@testing-library/react-native';
import ChatGoalCreateScreen from '../../screens/ChatGoalCreateScreen';

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
    currentScreen: { name: 'chat-goal-create' },
    navigate: mockNavigate,
    goBack: mockGoBack,
  }),
}));

const mockFetch = jest.fn();
global.fetch = mockFetch as any;

beforeEach(() => {
  mockNavigate.mockReset();
  mockGoBack.mockReset();
  mockFetch.mockReset();
});

// Helper: respond to createSession with a standard greeting
function mockSessionCreated(sessionId = 'ses-123') {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 201,
    json: async () => ({
      session_id: sessionId,
      messages: [
        {
          role: 'assistant',
          content: 'Tell me what you want to do, and I\'ll figure out how to track it.',
          action: null,
        },
      ],
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


describe('ChatGoalCreateScreen', () => {
  it('renders loading state while session is being created', () => {
    mockFetch.mockResolvedValueOnce(new Promise(() => {})); // never resolves
    const { getByText } = render(<ChatGoalCreateScreen />);
    expect(getByText('Starting chat...')).toBeTruthy();
  });

  it('shows assistant greeting after session created', async () => {
    mockSessionCreated();
    const { findByText } = render(<ChatGoalCreateScreen />);

    expect(await findByText(
      "Tell me what you want to do, and I'll figure out how to track it."
    )).toBeTruthy();
  });

  it('retries chat session creation after startup failure', async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: false,
        status: 500,
        text: async () => 'session boot failed',
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 201,
        json: async () => ({
          session_id: 'ses-retry',
          messages: [
            {
              role: 'assistant',
              content: 'Tell me what you want to do, and I\'ll figure out how to track it.',
              action: null,
            },
          ],
          status: 'active',
        }),
      });

    const { findByText, getByText } = render(<ChatGoalCreateScreen />);

    expect(await findByText('Failed to start chat')).toBeTruthy();
    fireEvent.press(getByText('Retry'));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(2);
    });

    expect(await findByText(
      "Tell me what you want to do, and I'll figure out how to track it."
    )).toBeTruthy();
  });


  it('shows chat input and send button', async () => {
    mockSessionCreated();
    const { findByTestId } = render(<ChatGoalCreateScreen />);

    const input = await findByTestId('chat-input');
    const sendBtn = await findByTestId('send-button');

    expect(input).toBeTruthy();
    expect(sendBtn).toBeTruthy();
  });

  it('send button is disabled when input is empty', async () => {
    mockSessionCreated();
    const { findByTestId } = render(<ChatGoalCreateScreen />);

    const sendBtn = await findByTestId('send-button');
    // When input is empty, button should be disabled
    expect(sendBtn.props.accessibilityState.disabled).toBe(true);
  });

  it('send button is enabled when text is entered', async () => {
    mockSessionCreated();
    const { findByTestId } = render(<ChatGoalCreateScreen />);

    const input = await findByTestId('chat-input');
    fireEvent.changeText(input, 'Hello');

    const sendBtn = await findByTestId('send-button');
    expect(sendBtn.props.accessibilityState.disabled).toBe(false);
  });

  it('sends message, posts the correct body, and hydrates returned chat', async () => {
    mockSessionCreated();
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        messages: [
          {
            role: 'assistant',
            content: 'Tell me what you want to do, and I\'ll figure out how to track it.',
            action: null,
          },
          { role: 'user', content: 'I want to upload a YouTube walkthrough', action: null },
          {
            role: 'assistant',
            content: 'Looks like this is a youtube_video goal.',
            action: {
              type: 'match_proposed',
              goal_type: 'youtube_video',
              confidence: 0.87,
              missing_criteria: ['deadline', 'min_duration_seconds'],
            },
          },
        ],
        draft_goal: { goal_type: 'youtube_video', title: 'YouTube walkthrough' },
      }),
    });

    const { findByTestId, findByText } = render(<ChatGoalCreateScreen />);

    const input = await findByTestId('chat-input');
    fireEvent.changeText(input, 'I want to upload a YouTube walkthrough');
    fireEvent.press(await findByTestId('send-button'));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(2);
    });

    const request = getFetchRequest(1);
    expect(request.url).toContain('/api/chat/sessions/ses-123/messages');
    expect(request.options.method).toBe('POST');
    expect(getFetchJsonBody(1)).toEqual({
      content: 'I want to upload a YouTube walkthrough',
    });

    expect(await findByText('I want to upload a YouTube walkthrough')).toBeTruthy();
    expect(await findByText('Looks like this is a youtube_video goal.')).toBeTruthy();
    expect(await findByText('Use this goal type: youtube_video')).toBeTruthy();
  });

  it('shows match_proposed action card with goal type info and continues after Use this', async () => {
    mockSessionCreated();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          messages: [
            { role: 'assistant', content: 'Greeting', action: null },
            { role: 'user', content: 'I want to upload a YouTube walkthrough', action: null },
            {
              role: 'assistant',
              content: 'Looks like this is a youtube_video goal.',
              action: {
                type: 'match_proposed',
                goal_type: 'youtube_video',
                confidence: 0.87,
                missing_criteria: ['deadline'],
              },
            },
          ],
          draft_goal: { goal_type: 'youtube_video' },
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          messages: [
            { role: 'assistant', content: 'Greeting', action: null },
            { role: 'user', content: 'I want to upload a YouTube walkthrough', action: null },
            {
              role: 'assistant',
              content: 'Looks like this is a youtube_video goal.',
              action: {
                type: 'match_proposed',
                goal_type: 'youtube_video',
                confidence: 0.87,
                missing_criteria: ['deadline'],
              },
            },
            { role: 'user', content: 'Use this goal type: youtube_video', action: null },
            {
              role: 'assistant',
              content: "What's your deadline?",
              action: {
                type: 'awaiting_input',
                field: 'deadline',
                prompt: "What's your deadline?",
              },
            },
          ],
          draft_goal: { goal_type: 'youtube_video' },
        }),
      });

    const { findByTestId, findByText } = render(<ChatGoalCreateScreen />);

    const input = await findByTestId('chat-input');
    fireEvent.changeText(input, 'I want to upload a YouTube walkthrough');
    fireEvent.press(await findByTestId('send-button'));

    const useThisButton = await findByTestId('use-this-goal-type');
    expect(await findByText('Use this goal type: youtube_video')).toBeTruthy();
    fireEvent.press(useThisButton);

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(3);
    });

    expect(getFetchJsonBody(2)).toEqual({ content: 'Use this goal type: youtube_video' });
    const awaitingCard = await findByTestId('awaiting-input-deadline');
    expect(within(awaitingCard).getByText("What's your deadline?")).toBeTruthy();
  });

  it('shows no_match action card with build button', async () => {
    mockSessionCreated();
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        messages: [
          { role: 'assistant', content: 'Greeting', action: null },
          { role: 'user', content: 'Track that I drank 8 glasses of water', action: null },
          {
            role: 'assistant',
            content: "I don't have a built-in way to verify that yet.",
            action: {
              type: 'no_match',
              suggested_action: 'generate_new_goal_type',
            },
          },
        ],
        draft_goal: null,
      }),
    });

    const { findByTestId, findByText } = render(<ChatGoalCreateScreen />);

    const input = await findByTestId('chat-input');
    fireEvent.changeText(input, 'Track that I drank 8 glasses of water');
    fireEvent.press(await findByTestId('send-button'));

    expect(await findByText("I don't have a built-in way to verify that yet")).toBeTruthy();
    expect(await findByTestId('yes-build-it')).toBeTruthy();
  });

  it('pressing use-this posts a follow-up turn and renders awaiting_input', async () => {
    mockSessionCreated();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          messages: [
            {
              role: 'assistant',
              content: 'Tell me what you want to do, and I\'ll figure out how to track it.',
              action: null,
            },
            { role: 'user', content: 'I want to upload a YouTube walkthrough', action: null },
            {
              role: 'assistant',
              content: 'Looks like this is a youtube_video goal.',
              action: {
                type: 'match_proposed',
                goal_type: 'youtube_video',
                confidence: 0.87,
                missing_criteria: ['deadline'],
              },
            },
          ],
          draft_goal: { goal_type: 'youtube_video' },
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          messages: [
            {
              role: 'assistant',
              content: 'Tell me what you want to do, and I\'ll figure out how to track it.',
              action: null,
            },
            { role: 'user', content: 'I want to upload a YouTube walkthrough', action: null },
            {
              role: 'assistant',
              content: 'Looks like this is a youtube_video goal.',
              action: {
                type: 'match_proposed',
                goal_type: 'youtube_video',
                confidence: 0.87,
                missing_criteria: ['deadline'],
              },
            },
            { role: 'user', content: 'Use this goal type', action: null },
            {
              role: 'assistant',
              content: "What's your deadline?",
              action: {
                type: 'awaiting_input',
                field: 'deadline',
                prompt: "What's your deadline?",
              },
            },
          ],
          draft_goal: { goal_type: 'youtube_video' },
        }),
      });

    const { findByTestId, findByText } = render(<ChatGoalCreateScreen />);

    const input = await findByTestId('chat-input');
    fireEvent.changeText(input, 'I want to upload a YouTube walkthrough');
    fireEvent.press(await findByTestId('send-button'));

    const useThisButton = await findByTestId('use-this-goal-type');
    fireEvent.press(useThisButton);

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(3);
    });

    const followUpRequest = getFetchRequest(2);
    expect(followUpRequest.url).toContain('/api/chat/sessions/ses-123/messages');
    expect(followUpRequest.options.method).toBe('POST');
    expect(getFetchJsonBody(2)).toEqual({ content: 'Use this goal type: youtube_video' });

    expect(await findByText('Use this goal type: youtube_video')).toBeTruthy();
    const awaitingCard = await findByTestId('awaiting-input-deadline');
    expect(await findByText('Awaiting input: deadline')).toBeTruthy();
    expect(within(awaitingCard).getByText("What's your deadline?")).toBeTruthy();
  });


  it('pressing yes-build-it calls request-new-goal-type with prompt summary and shows stub response', async () => {
    mockSessionCreated();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          messages: [
            {
              role: 'assistant',
              content: 'Tell me what you want to do, and I\'ll figure out how to track it.',
              action: null,
            },
            { role: 'user', content: 'Water goal', action: null },
            {
              role: 'assistant',
              content: "I don't have a built-in way to verify that yet.",
              action: { type: 'no_match', suggested_action: 'generate_new_goal_type' },
            },
          ],
          draft_goal: null,
        }),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 501,
        text: async () => '{"detail":"Goal-type generation is delivered in D010"}',
      });

    const { findByTestId, findByText } = render(<ChatGoalCreateScreen />);

    const input = await findByTestId('chat-input');
    fireEvent.changeText(input, 'Water goal');
    fireEvent.press(await findByTestId('send-button'));

    const buildBtn = await findByTestId('yes-build-it');
    fireEvent.press(buildBtn);

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(3);
    });

    const request = getFetchRequest(2);
    expect(request.url).toContain('/api/chat/sessions/ses-123/request-new-goal-type');
    expect(request.options.method).toBe('POST');
    expect(getFetchJsonBody(2)).toEqual({
      prompt_summary: 'Water goal',
      goal_payload_draft: {},
      chat_history: [
        {
          role: 'assistant',
          content: 'Tell me what you want to do, and I\'ll figure out how to track it.',
        },
        { role: 'user', content: 'Water goal' },
        {
          role: 'assistant',
          content: "I don't have a built-in way to verify that yet.",
        },
      ],
    });

    expect(await findByText("Goal-type generation isn't enabled yet — coming in D010.")).toBeTruthy();
  });

  it('hydrates backend-returned messages and shows retry button on 502 failure', async () => {
    mockSessionCreated();
    const retryBody = {
      messages: [
        { role: 'assistant', content: 'Persisted server greeting', action: null },
        { role: 'user', content: 'Something', action: null },
        {
          role: 'assistant',
          content: "I'm having trouble understanding right now — try again?",
          action: null,
        },
      ],
      draft_goal: null,
    };
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 502,
      text: async () => JSON.stringify(retryBody),
      json: async () => retryBody,
    });

    const { findByTestId, findByText, queryByText } = render(<ChatGoalCreateScreen />);

    const input = await findByTestId('chat-input');
    fireEvent.changeText(input, 'Something');
    fireEvent.press(await findByTestId('send-button'));

    expect(await findByText('Persisted server greeting')).toBeTruthy();
    expect(await findByText("I'm having trouble understanding right now — try again?")).toBeTruthy();
    expect(await findByTestId('retry-button')).toBeTruthy();
    expect(queryByText("Tell me what you want to do, and I'll figure out how to track it.")).toBeNull();
  });

  it('has back button that navigates home', async () => {
    mockSessionCreated();
    const { findByTestId } = render(<ChatGoalCreateScreen />);

    const backBtn = await findByTestId('back-to-home');
    fireEvent.press(backBtn);

    expect(mockGoBack).toHaveBeenCalled();
  });
});