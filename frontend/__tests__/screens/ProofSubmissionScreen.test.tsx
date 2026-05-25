import React from 'react';
import { render, fireEvent, waitFor } from '@testing-library/react-native';
import ProofSubmissionScreen from '../../screens/ProofSubmissionScreen';

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
    currentScreen: { name: 'proof-submission', goalId: 'goal-1' },
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

const activeYouTubeGoal = {
  id: 'goal-1',
  title: 'Record setup video',
  description: 'Record a walkthrough of setting up the dev environment',
  goal_type: 'youtube_video',
  pledge_amount: 5000,
  currency: 'usd',
  deadline: '2026-06-15T00:00:00Z',
  timezone: 'America/New_York',
  recurrence: 'none',
  status: 'active',
  charity_id: null,
  criteria: {
    criteria_type: 'youtube',
    criteria_data: {
      min_duration_seconds: 300,
      video_description: 'A walkthrough of the dev environment setup',
    },
  },
  created_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-05-15T00:00:00Z',
};

const expiredGoal = {
  ...activeYouTubeGoal,
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

describe('ProofSubmissionScreen', () => {
  describe('Shows goal description and deadline', () => {
    it('fetches and displays goal description and deadline at the top', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeYouTubeGoal,
      });

      const screen = render(<ProofSubmissionScreen goalId="goal-1" />);

      expect(await screen.findByText('Record setup video')).toBeTruthy();
      expect(
        await screen.findByText(/Record a walkthrough of setting up the dev environment/),
      ).toBeTruthy();
      expect(await screen.findByText(/Deadline:/)).toBeTruthy();
    });

    it('shows error state when goal fetch fails', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 500,
        text: async () => 'Server Error',
      });

      const screen = render(<ProofSubmissionScreen goalId="goal-1" />);

      expect(await screen.findByText(/HTTP 500/)).toBeTruthy();
    });
  });

  describe('YouTube URL client-side validation', () => {
    it('shows error for invalid YouTube URL', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeYouTubeGoal,
      });

      const screen = render(<ProofSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('youtube-url-input');

      fireEvent.changeText(screen.getByTestId('youtube-url-input'), 'not-a-url');
      fireEvent.press(screen.getByTestId('submit-proof-button'));

      expect(screen.getByTestId('youtube-url-input-error')).toBeTruthy();
    });

    it('shows error for non-YouTube URL', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeYouTubeGoal,
      });

      const screen = render(<ProofSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('youtube-url-input');

      fireEvent.changeText(
        screen.getByTestId('youtube-url-input'),
        'https://vimeo.com/123456',
      );
      fireEvent.press(screen.getByTestId('submit-proof-button'));

      expect(screen.getByTestId('youtube-url-input-error')).toBeTruthy();
    });

    it('accepts valid youtube.com URL', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeYouTubeGoal,
      });

      const screen = render(<ProofSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('youtube-url-input');

      fireEvent.changeText(
        screen.getByTestId('youtube-url-input'),
        'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
      );

      expect(screen.queryByTestId('youtube-url-input-error')).toBeNull();
    });

    it('accepts valid youtu.be URL', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeYouTubeGoal,
      });

      const screen = render(<ProofSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('youtube-url-input');

      fireEvent.changeText(
        screen.getByTestId('youtube-url-input'),
        'https://youtu.be/dQw4w9WgXcQ',
      );

      expect(screen.queryByTestId('youtube-url-input-error')).toBeNull();
    });

    it('clears URL validation error when user corrects the input', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeYouTubeGoal,
      });

      const screen = render(<ProofSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('youtube-url-input');

      const input = screen.getByTestId('youtube-url-input');

      fireEvent.changeText(input, 'not-a-url');
      fireEvent.press(screen.getByTestId('submit-proof-button'));
      expect(screen.getByTestId('youtube-url-input-error')).toBeTruthy();

      fireEvent.changeText(input, 'https://youtu.be/dQw4w9WgXcQ');
      expect(screen.queryByTestId('youtube-url-input-error')).toBeNull();
    });
  });

  describe('Loading/polling state after submission', () => {
    it('shows loading state after submitting valid URL', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeYouTubeGoal })
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

      const screen = render(<ProofSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('youtube-url-input');

      fireEvent.changeText(
        screen.getByTestId('youtube-url-input'),
        'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
      );
      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('submission-loading')).toBeTruthy();
      });
    });

    it('polls verification status and shows status updates', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeYouTubeGoal })
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

      const screen = render(<ProofSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('youtube-url-input');

      fireEvent.changeText(
        screen.getByTestId('youtube-url-input'),
        'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
      );
      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('verification-pending')).toBeTruthy();
      });
    });

    it('shows error if submit-proof API call fails', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeYouTubeGoal })
        .mockResolvedValueOnce({
          ok: false,
          status: 422,
          text: async () => JSON.stringify({ detail: 'Invalid YouTube URL' }),
        });

      const screen = render(<ProofSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('youtube-url-input');

      fireEvent.changeText(
        screen.getByTestId('youtube-url-input'),
        'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
      );
      fireEvent.press(screen.getByTestId('submit-proof-button'));

      expect(await screen.findByText(/Invalid YouTube URL/i)).toBeTruthy();
    });
  });

  describe('Verified state', () => {
    it('shows verified success state with duration and llm details', async () => {
      const verificationDetails = {
        duration_passed: true,
        duration_seconds: 420,
        min_duration_seconds: 300,
        llm_judgment_passed: true,
        llm_reasoning: 'The transcript covers the setup process as described',
      };

      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeYouTubeGoal })
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

      const screen = render(<ProofSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('youtube-url-input');

      fireEvent.changeText(
        screen.getByTestId('youtube-url-input'),
        'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
      );
      fireEvent.press(screen.getByTestId('submit-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('submission-loading')).toBeTruthy();
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          submission_id: 'sub-1',
          verification_status: 'verified',
          verification_details: verificationDetails,
        }),
      });

      jest.advanceTimersByTime(3000);

      await waitFor(() => {
        expect(screen.getByTestId('verification-verified')).toBeTruthy();
      });

      expect(screen.getByText(/Duration: Passed/)).toBeTruthy();
      expect(screen.getByText(/Content: Passed/)).toBeTruthy();
      expect(
        screen.queryByText(/The transcript covers the setup process/),
      ).toBeTruthy();
    });

    it('shows verdict true for verified status', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeYouTubeGoal })
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

      const screen = render(<ProofSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('youtube-url-input');

      fireEvent.changeText(
        screen.getByTestId('youtube-url-input'),
        'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
      );
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
            duration_passed: true,
            duration_seconds: 420,
            min_duration_seconds: 300,
            llm_judgment_passed: true,
            llm_reasoning: 'Covers everything',
          },
        }),
      });

      jest.advanceTimersByTime(3000);

      await waitFor(() => {
        expect(screen.getByTestId('verification-verified')).toBeTruthy();
      });

      expect(screen.getByText(/Verdict: True/)).toBeTruthy();
    });
  });

  describe('Failed state shows which criteria failed', () => {
    it('shows duration failed with explanation when video is too short', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeYouTubeGoal })
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

      const screen = render(<ProofSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('youtube-url-input');

      fireEvent.changeText(
        screen.getByTestId('youtube-url-input'),
        'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
      );
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
            duration_passed: false,
            duration_seconds: 60,
            min_duration_seconds: 300,
            llm_judgment_passed: false,
            failure_reason: 'Video duration (60s) is less than minimum required (300s)',
          },
        }),
      });

      jest.advanceTimersByTime(3000);

      await waitFor(() => {
        expect(screen.getByTestId('verification-failed')).toBeTruthy();
      });

      expect(screen.getByText(/Duration: Failed/)).toBeTruthy();
      expect(
        screen.queryByText(/Video duration.*is less than minimum/),
      ).toBeTruthy();
    });

    it('shows content mismatch failure when LLM judges content does not match', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeYouTubeGoal })
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

      const screen = render(<ProofSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('youtube-url-input');

      fireEvent.changeText(
        screen.getByTestId('youtube-url-input'),
        'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
      );
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
            duration_passed: true,
            duration_seconds: 600,
            min_duration_seconds: 300,
            llm_judgment_passed: false,
            llm_reasoning: 'The video is about cooking, not dev setup',
            failure_reason: 'Video content does not match the goal description',
          },
        }),
      });

      jest.advanceTimersByTime(3000);

      await waitFor(() => {
        expect(screen.getByTestId('verification-failed')).toBeTruthy();
      });

      expect(screen.getByText(/Duration: Passed/)).toBeTruthy();
      expect(screen.getByText(/Content: Failed/)).toBeTruthy();
      expect(
        screen.queryByText(/Video content does not match/),
      ).toBeTruthy();
    });
  });

  describe('Cannot resubmit after deadline passed', () => {
    it('shows deadline passed message when goal deadline is in the past', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => expiredGoal,
      });

      const screen = render(<ProofSubmissionScreen goalId="goal-2" />);
      await screen.findByTestId('deadline-passed-message');

      expect(screen.getByTestId('deadline-passed-message')).toBeTruthy();
    });

    it('hides submission form when deadline has passed', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => expiredGoal,
      });

      const screen = render(<ProofSubmissionScreen goalId="goal-2" />);
      await screen.findByTestId('deadline-passed-message');

      expect(screen.queryByTestId('youtube-url-input')).toBeNull();
      expect(screen.queryByTestId('submit-proof-button')).toBeNull();
    });

    it('allows submission when deadline is in the future', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeYouTubeGoal,
      });

      const screen = render(<ProofSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('youtube-url-input');

      expect(screen.getByTestId('youtube-url-input').props.editable).not.toBe(false);
      expect(screen.getByTestId('submit-proof-button').props.disabled).not.toBe(true);
    });
  });

  describe('Navigation', () => {
    it('navigates back on back button press', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeYouTubeGoal,
      });

      const screen = render(<ProofSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('youtube-url-input');

      fireEvent.press(screen.getByText('←'));

      expect(mockGoBack).toHaveBeenCalled();
    });
  });
});
