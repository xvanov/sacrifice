import React from 'react';
import { render, fireEvent, waitFor } from '@testing-library/react-native';
import GoalCreateScreen from '../../screens/GoalCreateScreen';

const mockNavigate = jest.fn();
const mockGoBack = jest.fn();

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
    currentScreen: { name: 'goal-create' },
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

beforeEach(() => {
  mockNavigate.mockReset();
  mockGoBack.mockReset();
  mockFetch.mockReset();
  mockLocalStorage.clear();
});

function fillInput(screen: any, testId: string, value: string) {
  fireEvent.changeText(screen.getByTestId(testId), value);
}

function submitForm(screen: any) {
  fireEvent.press(screen.getByTestId('submit-goal-button'));
}

function selectGoalType(screen: any, label: string) {
  fireEvent.press(screen.getByText(label));
}

describe('GoalCreateScreen', () => {
  it('renders all core form fields with proper testIDs', () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => [],
    });

    const { getByTestId } = render(<GoalCreateScreen />);

    expect(getByTestId('title-input')).toBeTruthy();
    expect(getByTestId('pledge-amount-input')).toBeTruthy();
    expect(getByTestId('charity-search-input')).toBeTruthy();
    expect(getByTestId('submit-goal-button')).toBeTruthy();
  });

  it('shows YouTube-specific fields when YouTube Video type is selected', () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => [],
    });

    const { getByTestId, queryByTestId } = render(<GoalCreateScreen />);

    expect(getByTestId('min-duration-input')).toBeTruthy();
    expect(getByTestId('video-description-input')).toBeTruthy();

    expect(queryByTestId('api-url-input')).toBeNull();
    expect(queryByTestId('sandbox-repo-url-input')).toBeNull();
  });

  it('shows API Endpoint-specific fields when API Endpoint type is selected', () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => [],
    });

    const { getByText, getByTestId, queryByTestId } = render(<GoalCreateScreen />);

    selectGoalType({ getByText }, 'API');

    expect(getByTestId('api-url-input')).toBeTruthy();
    expect(getByTestId('api-method-input')).toBeTruthy();
    expect(getByTestId('api-headers-input')).toBeTruthy();
    expect(getByTestId('api-expected-status-input')).toBeTruthy();
    expect(getByTestId('api-expected-body-input')).toBeTruthy();

    expect(queryByTestId('min-duration-input')).toBeNull();
    expect(queryByTestId('sandbox-repo-url-input')).toBeNull();
  });

  it('shows Dev Sandbox-specific fields when Dev Sandbox type is selected', () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => [],
    });

    const { getByText, getByTestId, queryByTestId } = render(<GoalCreateScreen />);

    selectGoalType({ getByText }, 'Sandbox');

    expect(getByTestId('sandbox-repo-url-input')).toBeTruthy();
    expect(getByTestId('sandbox-branch-input')).toBeTruthy();
    expect(getByTestId('sandbox-test-command-input')).toBeTruthy();
    expect(getByTestId('sandbox-goal-description-input')).toBeTruthy();

    expect(queryByTestId('min-duration-input')).toBeNull();
    expect(queryByTestId('api-url-input')).toBeNull();
  });

  it('preloads charities on mount', async () => {
    const charities = [
      { id: '1', name: 'Red Cross', stripe_connect_id: 'acct_1' },
      { id: '2', name: 'Save the Children', stripe_connect_id: 'acct_2' },
    ];

    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => charities,
    });

    render(<GoalCreateScreen />);

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/charities/search?q='),
        expect.anything(),
      );
    });
  });

  it('shows charity autocomplete results as the user types', async () => {
    const charities = [
      { id: '1', name: 'Red Cross', stripe_connect_id: 'acct_1' },
      { id: '2', name: 'Save the Children', stripe_connect_id: 'acct_2' },
    ];

    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => [],
    });

    const { getByTestId, findByText } = render(<GoalCreateScreen />);

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => charities,
    });

    fireEvent.changeText(getByTestId('charity-search-input'), 'Red');

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/charities/search?q=Red'),
        expect.anything(),
      );
    });

    const redCross = await findByText('Red Cross');
    expect(redCross).toBeTruthy();
    const saveChildren = await findByText('Save the Children');
    expect(saveChildren).toBeTruthy();
  });

  it('selected charity fills the input', async () => {
    const charities = [
      { id: '1', name: 'Red Cross', stripe_connect_id: 'acct_1' },
    ];

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });

    const { getByTestId, findByText } = render(<GoalCreateScreen />);

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => charities,
    });

    fireEvent.changeText(getByTestId('charity-search-input'), 'Red');

    const redCross = await findByText('Red Cross');
    fireEvent.press(redCross);

    expect(getByTestId('charity-search-input').props.value).toBe('Red Cross');
  });

  it('shows validation errors when required fields are empty on submit', () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => [],
    });

    const screen = render(<GoalCreateScreen />);

    submitForm(screen);

    expect(screen.queryByText('Title is required')).toBeTruthy();
    expect(screen.queryByText('Pledge amount is required')).toBeTruthy();
  });

  it('clears validation errors when fields are filled', () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => [],
    });

    const screen = render(<GoalCreateScreen />);

    submitForm(screen);
    expect(screen.queryByText('Title is required')).toBeTruthy();

    fillInput(screen, 'title-input', 'My Goal');
    expect(screen.queryByText('Title is required')).toBeNull();
  });

  it('navigates to goal detail on successful creation', async () => {
    const goalId = 'new-goal-uuid';
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: goalId }),
    });

    const screen = render(<GoalCreateScreen />);

    fillInput(screen, 'title-input', 'My Goal');
    fillInput(screen, 'pledge-amount-input', '50');
    fillInput(screen, 'min-duration-input', '300');
    fillInput(screen, 'video-description-input', 'A walkthrough');

    submitForm(screen);

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/goals'),
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('"title":"My Goal"'),
        }),
      );
    });

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith(
        expect.objectContaining({ name: 'goal-detail', goalId }),
      );
    });
  });

  it('converts pledge amount from dollars to cents on submit', async () => {
    const goalId = 'new-goal-uuid';
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: goalId }),
    });

    const screen = render(<GoalCreateScreen />);

    fillInput(screen, 'title-input', 'My Goal');
    fillInput(screen, 'pledge-amount-input', '50.00');
    fillInput(screen, 'min-duration-input', '300');
    fillInput(screen, 'video-description-input', 'A walkthrough');

    submitForm(screen);

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/goals'),
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('"pledge_amount":5000'),
        }),
      );
    });
  });

  it('shows error message when API submission fails', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 400,
      text: async () => JSON.stringify({
        detail: [{ loc: ['body', 'title'], msg: 'Title already exists' }],
      }),
    });

    const screen = render(<GoalCreateScreen />);

    fillInput(screen, 'title-input', 'My Goal');
    fillInput(screen, 'pledge-amount-input', '50');
    fillInput(screen, 'min-duration-input', '300');
    fillInput(screen, 'video-description-input', 'A walkthrough');

    submitForm(screen);

    const errorText = await screen.findByText(/error|failed|Title already exists/i);
    expect(errorText).toBeTruthy();

    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it('shows field-level error hints on API validation errors', async () => {
    const apiErrorResponse = {
      detail: [
        { loc: ['body', 'title'], msg: 'field required' },
        { loc: ['body', 'pledge_amount'], msg: 'must be positive' },
      ],
    };

    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 422,
      text: async () => JSON.stringify(apiErrorResponse),
    });

    const screen = render(<GoalCreateScreen />);

    fillInput(screen, 'title-input', 'My Goal');
    fillInput(screen, 'pledge-amount-input', '50');
    fillInput(screen, 'min-duration-input', '300');
    fillInput(screen, 'video-description-input', 'A walkthrough');

    submitForm(screen);

    await screen.findByText(/must be positive/i);
  });
});
