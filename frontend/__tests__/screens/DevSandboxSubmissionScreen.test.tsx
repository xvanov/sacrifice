import React from 'react';
import { render, fireEvent, waitFor } from '@testing-library/react-native';
import DevSandboxSubmissionScreen from '../../screens/DevSandboxSubmissionScreen';

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
    currentScreen: { name: 'dev-sandbox-proof-submission', goalId: 'goal-1' },
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

const activeDevSandboxGoal = {
  id: 'goal-1',
  title: 'Build the API endpoint',
  description: 'Create a FastAPI endpoint for user profiles',
  goal_type: 'dev_sandbox',
  pledge_amount: 10000,
  currency: 'usd',
  deadline: new Date(Date.now() + 30 * 24 * 3600 * 1000).toISOString(), // always in the future
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
      language: 'python',
      env_vars: {},
      goal_description: 'Build a FastAPI endpoint that accepts POST requests with a user ID',
    },
  },
  created_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-05-15T00:00:00Z',
};

const expiredGoal = {
  ...activeDevSandboxGoal,
  id: 'goal-2',
  deadline: '2025-01-01T00:00:00Z',
};

beforeEach(() => {
  jest.useFakeTimers();
  mockNavigate.mockReset();
  mockGoBack.mockReset();
  mockFetch.mockReset();
  mockLocalStorage.clear();
});

describe('DevSandboxSubmissionScreen', () => {
  describe('AC 1: All fields render with appropriate input types', () => {
    it('shows loading state while fetching goal', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeDevSandboxGoal,
      });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      expect(screen.getByTestId('dev-sandbox-loading')).toBeTruthy();
    });

    it('renders goal title and description at the top', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeDevSandboxGoal,
      });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);

      expect(await screen.findByText('Build the API endpoint')).toBeTruthy();
      expect(await screen.findByText(/Create a FastAPI endpoint/)).toBeTruthy();
    });

    it('renders repo_url field pre-filled from goal criteria', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeDevSandboxGoal,
      });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('repo-url-input');

      const input = screen.getByTestId('repo-url-input');
      expect(input.props.value).toBe('https://github.com/user/repo.git');
    });

    it('renders branch field pre-filled from goal criteria', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeDevSandboxGoal,
      });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('branch-input');

      const input = screen.getByTestId('branch-input');
      expect(input.props.value).toBe('main');
    });

    it('renders test_command field pre-filled from goal criteria', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeDevSandboxGoal,
      });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('test-command-input');

      const input = screen.getByTestId('test-command-input');
      expect(input.props.value).toBe('pytest tests/ -v');
    });

    it('renders language field pre-filled from goal criteria', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeDevSandboxGoal,
      });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('language-input');

      const input = screen.getByTestId('language-input');
      expect(input.props.value).toBe('python');
    });

    it('renders env_vars section with add key-value row button', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeDevSandboxGoal,
      });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('env-vars-section');

      expect(screen.getByTestId('add-env-var-button')).toBeTruthy();
    });

    it('renders submit button', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeDevSandboxGoal,
      });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('submit-proof-button');

      expect(screen.getByTestId('submit-proof-button')).toBeTruthy();
    });

    it('shows error state when goal fetch fails', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 500,
        text: async () => 'Server Error',
      });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);

      expect(await screen.findByText(/HTTP 500/)).toBeTruthy();
    });

    it('shows criteria details (goal description)', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeDevSandboxGoal,
      });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);

      expect(await screen.findByText(/Build a FastAPI endpoint that accepts/)).toBeTruthy();
    });
  });

  describe('AC 2: Progress shows each stage with a spinner and status text', () => {
    it('shows pending indicator after successful submission', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            goal_id: 'goal-1',
            submitted_at: '2026-06-01T00:00:00Z',
            verification_status: 'pending',
            verification_details: null,
          }),
        });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('repo-url-input');

      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('verification-pending')).toBeTruthy();
      });
    });

    it('shows submitting loading indicator while processing', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            goal_id: 'goal-1',
            submitted_at: '2026-06-01T00:00:00Z',
            verification_status: 'pending',
            verification_details: null,
          }),
        });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('repo-url-input');

      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('submission-loading')).toBeTruthy();
      });
    });

    it('shows API error when submission fails', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal })
        .mockResolvedValueOnce({
          ok: false,
          status: 422,
          text: async () => JSON.stringify({ detail: 'repo_url is required' }),
        });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('repo-url-input');

      fireEvent.press(screen.getByTestId('submit-proof-button'));

      expect(await screen.findByText(/repo_url is required/i)).toBeTruthy();
    });
  });

  describe('AC 3: Verified state with both checkmarks', () => {
    it('shows verified state with Tests Passed and Code Authentic green checkmarks', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            goal_id: 'goal-1',
            submitted_at: '2026-06-01T00:00:00Z',
            verification_status: 'pending',
            verification_details: null,
          }),
        });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('repo-url-input');
      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('submission-loading')).toBeTruthy();
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          submission_id: 'sub-1',
          verification_status: 'verified',
          verification_details: {
            tests_passed: true,
            exit_code: 0,
            authentic: true,
            llm_reasoning: 'The code genuinely implements the user profile endpoint',
            stage: 'test',
          },
        }),
      });

      jest.advanceTimersByTime(3000);

      await waitFor(() => {
        expect(screen.getByTestId('verification-verified')).toBeTruthy();
      });

      expect(screen.getByTestId('tests-passed-check')).toBeTruthy();
      expect(screen.getByTestId('code-authentic-check')).toBeTruthy();
      expect(screen.getByText(/Tests Passed/)).toBeTruthy();
      expect(screen.getByText(/Code Authentic/)).toBeTruthy();
    });
  });

  describe('AC 4: Failed state shows which stage failed with details', () => {
    it('shows clone stage failure with error details', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            verification_status: 'pending',
            verification_details: null,
          }),
        });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('repo-url-input');
      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('submission-loading')).toBeTruthy();
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          submission_id: 'sub-1',
          verification_status: 'failed',
          verification_details: {
            stage: 'clone',
            error: 'Failed to clone repo https://github.com/user/repo.git (branch: main): Repository not found',
          },
        }),
      });

      jest.advanceTimersByTime(3000);

      await waitFor(() => {
        expect(screen.getByTestId('verification-failed')).toBeTruthy();
      });

      expect(screen.getByTestId('failed-stage-clone')).toBeTruthy();
      expect(screen.getByText(/Clone Failed/)).toBeTruthy();
      expect(screen.getByText(/Repository not found/)).toBeTruthy();
    });

    it('shows install stage failure with details', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            verification_status: 'pending',
            verification_details: null,
          }),
        });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('repo-url-input');
      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('submission-loading')).toBeTruthy();
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          submission_id: 'sub-1',
          verification_status: 'failed',
          verification_details: {
            stage: 'install',
            error: 'Dependency installation failed',
            exit_code: 1,
            stderr: 'pip install failed',
          },
        }),
      });

      jest.advanceTimersByTime(3000);

      await waitFor(() => {
        expect(screen.getByTestId('verification-failed')).toBeTruthy();
      });

      expect(screen.getByTestId('failed-stage-install')).toBeTruthy();
      expect(screen.getByText(/Install Failed/)).toBeTruthy();
    });

    it('shows test stage failure with exit code and output', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            verification_status: 'pending',
            verification_details: null,
          }),
        });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('repo-url-input');
      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('submission-loading')).toBeTruthy();
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          submission_id: 'sub-1',
          verification_status: 'failed',
          verification_details: {
            stage: 'test',
            tests_passed: false,
            exit_code: 1,
            stdout: 'FAILED test_user_api.py::test_create_user - AssertionError',
            stderr: '1 failed, 0 passed',
          },
        }),
      });

      jest.advanceTimersByTime(3000);

      await waitFor(() => {
        expect(screen.getByTestId('verification-failed')).toBeTruthy();
      });

      expect(screen.getByTestId('failed-stage-test')).toBeTruthy();
      expect(screen.getByText(/Test Failed/)).toBeTruthy();
    });

    // A failure whose stage has no card rendered NOTHING but "Verdict: False",
    // hiding `error` — the only explanation the user gets, possibly right after
    // being charged. Every stage must explain itself.
    const renderFailureWithDetails = async (details: Record<string, unknown>) => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            verification_status: 'pending',
            verification_details: null,
          }),
        });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('repo-url-input');
      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('submission-loading')).toBeTruthy();
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          submission_id: 'sub-1',
          verification_status: 'failed',
          verification_details: details,
        }),
      });

      jest.advanceTimersByTime(3000);

      await waitFor(() => {
        expect(screen.getByTestId('verification-failed')).toBeTruthy();
      });

      return screen;
    };

    it('shows sandbox stage failure with the infrastructure error', async () => {
      const screen = await renderFailureWithDetails({
        stage: 'sandbox',
        error: 'Sandbox container died during the test command (exit 137, no output)',
      });

      expect(screen.getByTestId('failed-stage-sandbox')).toBeTruthy();
      expect(screen.getByText(/Sandbox Error/)).toBeTruthy();
      expect(screen.getByText(/exit 137, no output/)).toBeTruthy();
    });

    it('shows validation stage failure with the reason', async () => {
      const screen = await renderFailureWithDetails({
        stage: 'validation',
        error: 'test_command could not be parsed (No closing quotation)',
      });

      expect(screen.getByTestId('failed-stage-validation')).toBeTruthy();
      expect(screen.getByText(/Invalid Submission/)).toBeTruthy();
      expect(screen.getByText(/No closing quotation/)).toBeTruthy();
    });

    it('shows the error for an unrecognised stage instead of a blank panel', async () => {
      const screen = await renderFailureWithDetails({
        stage: 'unknown',
        error: 'Unexpected error: something went sideways',
      });

      expect(screen.getByTestId('failed-stage-other')).toBeTruthy();
      expect(screen.getByText(/something went sideways/)).toBeTruthy();
    });

    it('explains a failure that carries no stage at all', async () => {
      const screen = await renderFailureWithDetails({ error: 'No stage reported' });

      expect(screen.getByTestId('failed-stage-other')).toBeTruthy();
      expect(screen.getByText(/No stage reported/)).toBeTruthy();
    });
  });

  describe('AC 5: Test output is scrollable and searchable', () => {
    it('displays test stdout/stderr in the result view', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            verification_status: 'pending',
            verification_details: null,
          }),
        });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('repo-url-input');
      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('submission-loading')).toBeTruthy();
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          submission_id: 'sub-1',
          verification_status: 'failed',
          verification_details: {
            stage: 'test',
            tests_passed: false,
            exit_code: 1,
            stdout: 'FAILED test_user_api.py::test_create_user - AssertionError\nExpected 201, got 400',
            stderr: '1 failed, 0 passed',
          },
        }),
      });

      jest.advanceTimersByTime(3000);

      await waitFor(() => {
        expect(screen.getByTestId('verification-failed')).toBeTruthy();
      });

      expect(screen.getByTestId('test-output-section')).toBeTruthy();
      expect(screen.getByText(/FAILED test_user_api/)).toBeTruthy();
      expect(screen.getByText(/Expected 201, got 400/)).toBeTruthy();
    });

    it('renders test output in scrollable container', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            verification_status: 'pending',
            verification_details: null,
          }),
        });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('repo-url-input');
      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('submission-loading')).toBeTruthy();
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          submission_id: 'sub-1',
          verification_status: 'failed',
          verification_details: {
            stage: 'test',
            tests_passed: false,
            exit_code: 1,
            stdout: 'Test output with lots of content',
            stderr: 'Some errors',
          },
        }),
      });

      jest.advanceTimersByTime(3000);

      await waitFor(() => {
        expect(screen.getByTestId('verification-failed')).toBeTruthy();
      });

      const scrollView = screen.getByTestId('test-output-scroll');
      expect(scrollView.props.scrollEnabled).toBe(true);
    });
  });

  describe('AC 6: LLM reasoning displayed in readable format', () => {
    it('shows LLM reasoning when code authentic review passes', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            verification_status: 'pending',
            verification_details: null,
          }),
        });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('repo-url-input');
      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('submission-loading')).toBeTruthy();
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          submission_id: 'sub-1',
          verification_status: 'verified',
          verification_details: {
            tests_passed: true,
            exit_code: 0,
            authentic: true,
            llm_reasoning: 'The code contains a proper FastAPI endpoint with POST handler, request validation using Pydantic, and database integration. This is a genuine implementation.',
            stage: 'test',
          },
        }),
      });

      jest.advanceTimersByTime(3000);

      await waitFor(() => {
        expect(screen.getByTestId('verification-verified')).toBeTruthy();
      });

      expect(screen.getByTestId('llm-reasoning-section')).toBeTruthy();
      expect(screen.getByText(/The code contains a proper FastAPI endpoint/)).toBeTruthy();
    });

    it('shows LLM reasoning when code is judged not authentic', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            verification_status: 'pending',
            verification_details: null,
          }),
        });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('repo-url-input');
      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('submission-loading')).toBeTruthy();
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          submission_id: 'sub-1',
          verification_status: 'failed',
          verification_details: {
            stage: 'test',
            tests_passed: true,
            exit_code: 0,
            authentic: false,
            llm_reasoning: 'The code appears to hardcode test answers. The function always returns 200 regardless of input.',
          },
        }),
      });

      jest.advanceTimersByTime(3000);

      await waitFor(() => {
        expect(screen.getByTestId('verification-failed')).toBeTruthy();
      });

      expect(screen.getByTestId('llm-reasoning-section')).toBeTruthy();
      expect(screen.getByText(/hardcode test answers/)).toBeTruthy();
    });
  });

  describe('AC 7: User can retry submission if it fails', () => {
    it('shows retry button when verification fails', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            verification_status: 'pending',
            verification_details: null,
          }),
        });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('repo-url-input');
      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('submission-loading')).toBeTruthy();
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          submission_id: 'sub-1',
          verification_status: 'failed',
          verification_details: {
            stage: 'test',
            tests_passed: false,
            exit_code: 1,
            stdout: 'Test output here',
            stderr: '',
          },
        }),
      });

      jest.advanceTimersByTime(3000);

      await waitFor(() => {
        expect(screen.getByTestId('verification-failed')).toBeTruthy();
      });

      expect(screen.getByTestId('retry-button')).toBeTruthy();
    });

    it('retry button resets to form state and allows editing fields', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            verification_status: 'pending',
            verification_details: null,
          }),
        });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('repo-url-input');
      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('submission-loading')).toBeTruthy();
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          submission_id: 'sub-1',
          verification_status: 'failed',
          verification_details: {
            stage: 'test',
            tests_passed: false,
            exit_code: 1,
          },
        }),
      });

      jest.advanceTimersByTime(3000);

      await waitFor(() => {
        expect(screen.getByTestId('verification-failed')).toBeTruthy();
      });

      fireEvent.press(screen.getByTestId('retry-button'));

      await waitFor(() => {
        expect(screen.getByTestId('repo-url-input')).toBeTruthy();
        expect(screen.getByTestId('submit-proof-button')).toBeTruthy();
      });
    });

    it('retry button is not shown on verified state', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            verification_status: 'pending',
            verification_details: null,
          }),
        });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('repo-url-input');
      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('submission-loading')).toBeTruthy();
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          submission_id: 'sub-1',
          verification_status: 'verified',
          verification_details: {
            tests_passed: true,
            authentic: true,
            exit_code: 0,
          },
        }),
      });

      jest.advanceTimersByTime(3000);

      await waitFor(() => {
        expect(screen.getByTestId('verification-verified')).toBeTruthy();
      });

      expect(screen.queryByTestId('retry-button')).toBeNull();
    });
  });

  describe('Deadline enforcement', () => {
    it('shows deadline passed message when goal deadline is in the past', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => expiredGoal,
      });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-2" />);
      await screen.findByTestId('deadline-passed-message');

      expect(screen.getByTestId('deadline-passed-message')).toBeTruthy();
    });

    it('hides submission form when deadline has passed', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => expiredGoal,
      });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-2" />);
      await screen.findByTestId('deadline-passed-message');

      expect(screen.queryByTestId('repo-url-input')).toBeNull();
      expect(screen.queryByTestId('submit-proof-button')).toBeNull();
    });
  });

  describe('Navigation', () => {
    it('navigates back on back button press', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeDevSandboxGoal,
      });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('repo-url-input');

      fireEvent.press(screen.getByText('←'));

      expect(mockGoBack).toHaveBeenCalled();
    });
  });

  describe('Env vars: adding and removing rows', () => {
    it('allows adding env var key-value rows', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeDevSandboxGoal,
      });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('env-vars-section');

      fireEvent.press(screen.getByTestId('add-env-var-button'));

      expect(screen.getAllByTestId(/^env-var-row-/)).toHaveLength(1);
    });

    it('allows removing env var rows', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeDevSandboxGoal,
      });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('env-vars-section');

      fireEvent.press(screen.getByTestId('add-env-var-button'));
      expect(screen.getAllByTestId(/^env-var-row-/)).toHaveLength(1);

      const removeButton = screen.getByTestId('remove-env-var-0');
      fireEvent.press(removeButton);

      const rows = screen.queryAllByTestId(/^env-var-row-/);
      expect(rows.length).toBe(0);
    });
  });

  // The PAT is the only secret this screen handles. Every test here is about a
  // way it could escape, or about the public-repo path staying credential-free.
  describe('Private repositories: the access token field', () => {
    const submitOk = () => ({
      ok: true,
      json: async () => ({
        submission_id: 'sub-1',
        goal_id: 'goal-1',
        submitted_at: '2026-06-01T00:00:00Z',
        verification_status: 'pending',
        verification_details: null,
      }),
    });

    const submittedBody = () => JSON.parse(mockFetch.mock.calls[1][1].body);

    it('offers a token field, marked optional', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);

      const input = await screen.findByTestId('github-token-input');
      expect(input.props.value).toBe('');
      expect(screen.getByTestId('github-token-help')).toBeTruthy();
    });

    it('masks the token and keeps it out of every system-level store', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      const input = await screen.findByTestId('github-token-input');

      expect(input.props.secureTextEntry).toBe(true);
      expect(input.props.autoCorrect).toBe(false);
      expect(input.props.autoCapitalize).toBe('none');
      // No autofill, no password-manager capture, no keyboard learning.
      expect(input.props.autoComplete).toBe('off');
      expect(input.props.textContentType).toBe('none');
      expect(input.props.spellCheck).toBe(false);
    });

    it('states the minimum scope and that it is stored encrypted', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('github-token-help');

      // The scope is called out on its own, so the user can grant the minimum.
      expect(screen.getByText('repo')).toBeTruthy();
      expect(screen.getByText(/stored encrypted/)).toBeTruthy();
      expect(screen.getByText(/Leave this empty for a public repository/)).toBeTruthy();
    });

    it('never prefills the token from the loaded goal', async () => {
      // A stored credential must not be rendered back into an input even if a
      // response were to carry one.
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          ...activeDevSandboxGoal,
          criteria: {
            criteria_type: 'dev_sandbox',
            criteria_data: {
              ...activeDevSandboxGoal.criteria.criteria_data,
              github_token: 'fernet:leaked-ciphertext',
            },
          },
        }),
      });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      const input = await screen.findByTestId('github-token-input');

      expect(input.props.value).toBe('');
      expect(screen.queryByText(/fernet:/)).toBeNull();
    });

    it('sends the token when the user supplies one', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal })
        .mockResolvedValueOnce(submitOk());

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('github-token-input');

      fireEvent.changeText(screen.getByTestId('github-token-input'), 'ghp_secret123');
      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
      expect(submittedBody().github_token).toBe('ghp_secret123');
    });

    it('omits the field entirely for a public repo', async () => {
      // Not an empty string: the backend must see no credential at all, so the
      // public path behaves exactly as it did before tokens existed.
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal })
        .mockResolvedValueOnce(submitOk());

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('github-token-input');

      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
      expect('github_token' in submittedBody()).toBe(false);
    });

    it('trims surrounding whitespace rather than sending a broken token', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal })
        .mockResolvedValueOnce(submitOk());

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('github-token-input');

      fireEvent.changeText(screen.getByTestId('github-token-input'), '  ghp_padded  ');
      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
      expect(submittedBody().github_token).toBe('ghp_padded');
    });

    it('drops the token from state once it has been submitted', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal })
        .mockResolvedValueOnce(submitOk());

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('github-token-input');

      fireEvent.changeText(screen.getByTestId('github-token-input'), 'ghp_secret123');
      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
      // Back to the form (the failure path) must not re-expose it.
      expect(screen.queryByText(/ghp_secret123/)).toBeNull();
    });

    it('does not echo the token back in a failure panel', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal })
        .mockResolvedValueOnce({ ok: false, json: async () => ({ detail: 'bad token' }) });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('github-token-input');

      fireEvent.changeText(screen.getByTestId('github-token-input'), 'ghp_secret123');
      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
      expect(screen.queryByText(/ghp_secret123/)).toBeNull();
    });
  });

  // github_repo is verified entirely through the GitHub API — nothing is cloned
  // and no command runs — so the sandbox-only inputs are not merely unused
  // there, they misrepresent what the submission does.
  describe('github_repo goals share this screen but not its sandbox fields', () => {
    const githubGoal = {
      ...activeDevSandboxGoal,
      goal_type: 'github_repo',
      criteria: {
        criteria_type: 'github_repo',
        criteria_data: { repo_url: 'https://github.com/user/repo.git', branch: 'main' },
      },
    };

    it('still offers repo, branch and the optional token', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true, json: async () => githubGoal });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);

      expect(await screen.findByTestId('repo-url-input')).toBeTruthy();
      expect(screen.getByTestId('branch-input')).toBeTruthy();
      expect(screen.getByTestId('github-token-input')).toBeTruthy();
    });

    it('hides the test invocation, language and env-var inputs', async () => {
      mockFetch.mockResolvedValueOnce({ ok: true, json: async () => githubGoal });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('repo-url-input');

      expect(screen.queryByTestId('test-command-input')).toBeNull();
      expect(screen.queryByTestId('language-input')).toBeNull();
      expect(screen.queryByTestId('env-vars-section')).toBeNull();
    });

    it('does not send a test command it would ignore', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => githubGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            goal_id: 'goal-1',
            submitted_at: '2026-06-01T00:00:00Z',
            verification_status: 'pending',
            verification_details: null,
          }),
        });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('repo-url-input');

      fireEvent.changeText(screen.getByTestId('github-token-input'), 'ghp_secret123');
      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
      const body = JSON.parse(mockFetch.mock.calls[1][1].body);
      expect('test_command' in body).toBe(false);
      expect('language' in body).toBe(false);
      expect('env_vars' in body).toBe(false);
      expect(body.repo_url).toBe('https://github.com/user/repo.git');
      expect(body.github_token).toBe('ghp_secret123');
    });

    it('still sends the test command for a dev_sandbox goal', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeDevSandboxGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            goal_id: 'goal-1',
            submitted_at: '2026-06-01T00:00:00Z',
            verification_status: 'pending',
            verification_details: null,
          }),
        });

      const screen = render(<DevSandboxSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('test-command-input');

      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
      expect(JSON.parse(mockFetch.mock.calls[1][1].body).test_command).toBe(
        'pytest tests/ -v',
      );
    });
  });
});
