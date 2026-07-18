import React from 'react';
import { fireEvent, render, waitFor, within } from '@testing-library/react-native';
import GoalCreateScreen from '../../screens/GoalCreateScreen';
import { typeLabel, setDynamicTypeLabels } from '../../components/StatusBadge';

const mockGoBack = jest.fn();
const mockNavigate = jest.fn();

jest.mock('../../hooks/useNavigation', () => ({
  useNavigation: () => ({
    currentScreen: { name: 'goal-create' },
    navigate: mockNavigate,
    goBack: mockGoBack,
  }),
}));

const mockFetch = jest.fn();
global.fetch = mockFetch as typeof fetch;

const BUILT_IN_GOAL_TYPES = [
  { name: 'youtube_video', description: 'Verify a YouTube video.', sample_prompts: [], criteria_schema: {} },
  { name: 'api_endpoint', description: 'Ping an API endpoint.', sample_prompts: [], criteria_schema: {} },
  { name: 'dev_sandbox', description: 'Run tests in a sandbox.', sample_prompts: [], criteria_schema: {} },
  { name: 'github_repo', description: 'Push commits to a repo.', sample_prompts: [], criteria_schema: {} },
];

function mockGoalTypesResponse(types = BUILT_IN_GOAL_TYPES) {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => ({ goal_types: types }),
  });
}

function mockCreateGoalResponse() {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 201,
    json: async () => ({ id: 'goal-new', status: 'active' }),
  });
}

beforeEach(() => {
  mockGoBack.mockReset();
  mockNavigate.mockReset();
  mockFetch.mockReset();
  setDynamicTypeLabels(null);
});

describe('GoalCreateScreen', () => {
  it('fetches goal types from /api/goal-types on mount', async () => {
    mockGoalTypesResponse();
    mockCreateGoalResponse();

    render(<GoalCreateScreen />);

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(1);
    });

    const [url] = mockFetch.mock.calls[0] as [string, RequestInit | undefined];
    expect(url).toContain('/api/goal-types');
  });

  it('renders goal-type options from /api/goal-types in the picker', async () => {
    mockGoalTypesResponse();
    mockCreateGoalResponse();

    const { findByTestId } = render(<GoalCreateScreen />);

    const youtubeBtn = await findByTestId('goal-type-youtube_video');
    expect(within(youtubeBtn).getByText('Verify a YouTube video.')).toBeTruthy();

    expect(await findByTestId('goal-type-api_endpoint')).toBeTruthy();
    expect(await findByTestId('goal-type-dev_sandbox')).toBeTruthy();
    expect(await findByTestId('goal-type-github_repo')).toBeTruthy();
  });

  it('renders a backend-only goal type without changes to frontend source lists', async () => {
    mockGoalTypesResponse([
      { name: 'strava_run', description: 'Verify a Strava running activity.', sample_prompts: [], criteria_schema: {} },
    ]);
    mockCreateGoalResponse();

    const { findByTestId } = render(<GoalCreateScreen />);

    const stravaBtn = await findByTestId('goal-type-strava_run');
    // Verify the user-visible label — description shown as the button text.
    expect(within(stravaBtn).getByText('Verify a Strava running activity.')).toBeTruthy();
    // Also verify the dynamic label is applied via StatusBadge module.
    await waitFor(() => {
      expect(typeLabel('strava_run')).toBe('Verify a Strava running activity.');
    });
  });

  it('shows loading state while fetching goal types', async () => {
    // Don't resolve the fetch immediately — loading state should show.
    let resolveFetch: (v: Response) => void;
    const pending = new Promise<Response>((resolve) => { resolveFetch = resolve; });
    mockFetch.mockReturnValueOnce(pending);

    const { findByText } = render(<GoalCreateScreen />);

    expect(await findByText('Loading goal types...')).toBeTruthy();
  });

  it('survives goal-types fetch failure without crashing', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: async () => 'Internal Server Error',
    });
    mockCreateGoalResponse();

    const { findByTestId, findByText } = render(<GoalCreateScreen />);

    // Should render the screen even though types failed.
    expect(await findByTestId('goal-create-screen')).toBeTruthy();
    // Shows the empty state message for types.
    expect(await findByText('No goal types available.')).toBeTruthy();
    // Create button still renders.
    expect(await findByTestId('create-goal-button')).toBeTruthy();
  });

  it('preserves create-flow behavior for the four built-in goal types', async () => {
    mockGoalTypesResponse();
    mockCreateGoalResponse();

    const { findByTestId } = render(<GoalCreateScreen />);

    // Select youtube_video type.
    fireEvent.press(await findByTestId('goal-type-youtube_video'));

    // Fill in required fields.
    fireEvent.changeText(await findByTestId('title-input'), 'Test Goal');
    fireEvent.changeText(await findByTestId('pledge-input'), '10');
    // Deadline defaults to 7 days from now — we leave it as-is.

    // YouTube conditional criteria appears.
    const durationInput = await findByTestId('youtube-duration-input');
    expect(durationInput).toBeTruthy();
    fireEvent.changeText(durationInput, '60');

    // Verify the API endpoint criteria does NOT appear.
    const screen = await findByTestId('goal-create-screen');
    expect(() => within(screen).getByTestId('api-url-input')).toThrow();

    // Submit the form.
    fireEvent.press(await findByTestId('create-goal-button'));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(2);
    });

    const [, createOptions] = mockFetch.mock.calls[1] as [string, RequestInit];
    const body = JSON.parse(String(createOptions.body ?? '{}'));
    expect(body.goal_type).toBe('youtube_video');
    expect(body.title).toBe('Test Goal');
    expect(body.pledge_amount).toBe(1000); // 10 dollars in cents
    expect(body.criteria.criteria_type).toBe('youtube_video');
    expect(body.criteria.criteria_data.min_duration_seconds).toBe(60);
  });

  it('submits with a backend-provided goal type flowing through the submission path', async () => {
    mockGoalTypesResponse([
      { name: 'geolocation', description: 'Visit a location.', sample_prompts: [], criteria_schema: {} },
    ]);
    mockCreateGoalResponse();

    const { findByTestId } = render(<GoalCreateScreen />);

    fireEvent.press(await findByTestId('goal-type-geolocation'));
    fireEvent.changeText(await findByTestId('title-input'), 'Go to the park');
    fireEvent.changeText(await findByTestId('pledge-input'), '5');

    fireEvent.press(await findByTestId('create-goal-button'));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(2);
    });

    const [, createOptions] = mockFetch.mock.calls[1] as [string, RequestInit];
    const body = JSON.parse(String(createOptions.body ?? '{}'));
    expect(body.goal_type).toBe('geolocation');
    expect(body.title).toBe('Go to the park');
    expect(body.pledge_amount).toBe(500);
  });

  it('validates required fields before submission', async () => {
    mockGoalTypesResponse();

    const { findByTestId, findByText } = render(<GoalCreateScreen />);

    // Wait for goal types to load first.
    await findByTestId('goal-type-youtube_video');

    // Try submitting with nothing filled in.
    fireEvent.press(await findByTestId('create-goal-button'));

    // Validation errors should appear.
    expect(await findByText('Title is required')).toBeTruthy();
    expect(await findByText('Select a goal type')).toBeTruthy();
    expect(await findByText('Pledge must be a positive number')).toBeTruthy();
  });

  it('renders charity search and allows selecting a result', async () => {
    mockGoalTypesResponse();
    // Charity search response.
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => [{ id: 'char-1', name: 'Red Cross', description: 'Humanitarian aid' }],
    });
    mockCreateGoalResponse();

    const { findByTestId, findByText } = render(<GoalCreateScreen />);

    const charityInput = await findByTestId('charity-search-input');
    fireEvent.changeText(charityInput, 'Red');

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(2);
    });

    const charityResult = await findByTestId('charity-result-char-1');
    fireEvent.press(charityResult);

    // Selected charity is displayed.
    expect(await findByText('Red Cross')).toBeTruthy();
  });
});