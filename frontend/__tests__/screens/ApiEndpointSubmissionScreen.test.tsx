import React from 'react';
import { render, fireEvent, waitFor } from '@testing-library/react-native';
import ApiEndpointSubmissionScreen from '../../screens/ApiEndpointSubmissionScreen';

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
    currentScreen: { name: 'api-endpoint-proof-submission', goalId: 'goal-1' },
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

jest.mock('react-native', () => {
  const RN = jest.requireActual('react-native');
  RN.Platform.OS = 'web';
  return RN;
});

const activeApiGoal = {
  id: 'goal-1',
  title: 'API health check',
  description: 'Verify the health endpoint returns 200',
  goal_type: 'api_endpoint',
  pledge_amount: 5000,
  currency: 'usd',
  deadline: new Date(Date.now() + 30 * 24 * 3600 * 1000).toISOString(), // always in the future
  timezone: 'America/New_York',
  recurrence: 'none',
  status: 'active',
  charity_id: null,
  criteria: {
    criteria_type: 'api_endpoint',
    criteria_data: {
      url: 'https://api.example.com/health',
      method: 'GET',
      headers: { Authorization: 'Bearer test-token' },
      expected_status: 200,
      expected_body_schema: { type: 'object', properties: { status: { type: 'string' } } },
    },
  },
  created_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-05-15T00:00:00Z',
};

const expiredGoal = {
  ...activeApiGoal,
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

function getByTestIdSafe(screen: any, id: string) {
  try {
    return screen.getByTestId(id);
  } catch {
    return null;
  }
}

describe('ApiEndpointSubmissionScreen', () => {
  describe('AC 1: All configuration fields render and accept input', () => {
    it('renders URL input pre-filled from goal criteria', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeApiGoal,
      });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);

      const urlInput = await screen.findByTestId('endpoint-url-input');
      expect(urlInput).toBeTruthy();
      expect(urlInput.props.value).toBe('https://api.example.com/health');
    });

    it('renders method input and accepts new value', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeApiGoal,
      });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);

      const methodInput = await screen.findByTestId('endpoint-method-input');
      expect(methodInput).toBeTruthy();
      fireEvent.changeText(methodInput, 'POST');
      expect(methodInput.props.value).toBe('POST');
    });

    it('renders headers section with key-value rows', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeApiGoal,
      });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);

      expect(await screen.findByTestId('headers-section')).toBeTruthy();
    });

    it('renders expected status input pre-filled from criteria', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeApiGoal,
      });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);

      const statusInput = await screen.findByTestId('expected-status-input');
      expect(statusInput).toBeTruthy();
      expect(statusInput.props.value).toBe('200');
    });

    it('renders expected body schema input pre-filled from criteria', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeApiGoal,
      });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);

      const schemaInput = await screen.findByTestId('expected-body-schema-input');
      expect(schemaInput).toBeTruthy();
      expect(schemaInput.props.value).toContain('object');
    });
  });

  describe('AC 2: Headers support adding and removing key-value rows', () => {
    it('shows add header button', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeApiGoal,
      });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);

      expect(await screen.findByTestId('add-header-button')).toBeTruthy();
    });

    it('adds a new header row when add button is pressed', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeApiGoal,
      });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('headers-section');

      fireEvent.press(screen.getByTestId('add-header-button'));

      const headerRows = screen.getAllByTestId(/^header-row-/);
      const headerKeyInputs = screen.getAllByTestId(/^header-key-input-/);
      const headerValueInputs = screen.getAllByTestId(/^header-value-input-/);

      expect(headerRows.length).toBeGreaterThan(1);
      expect(headerKeyInputs.length).toBeGreaterThan(0);
      expect(headerValueInputs.length).toBeGreaterThan(0);
    });

    it('removes a header row when remove button is pressed', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeApiGoal,
      });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('headers-section');

      const initialCount = screen.queryAllByTestId(/^header-row-/).length;

      if (initialCount > 0) {
        const removeButtons = screen.queryAllByTestId(/^remove-header-/);
        fireEvent.press(removeButtons[0]);
      }

      const remainingCount = screen.queryAllByTestId(/^header-row-/).length;
      expect(remainingCount).toBe(Math.max(0, initialCount - 1));
    });
  });

  describe('AC 3: Expected body schema accepts JSON', () => {
    it('accepts a valid JSON object in the schema field', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeApiGoal,
      });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);

      const schemaInput = await screen.findByTestId('expected-body-schema-input');
      const validJson = JSON.stringify({ type: 'object', properties: { status: { type: 'string' } } }, null, 2);
      fireEvent.changeText(schemaInput, validJson);
      expect(schemaInput.props.value).toBe(validJson);
    });

    it('shows error for invalid JSON in schema field', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeApiGoal,
      });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);

      const schemaInput = await screen.findByTestId('expected-body-schema-input');
      fireEvent.changeText(schemaInput, 'not-valid-json');
      fireEvent.press(screen.getByTestId('submit-api-proof-button'));

      expect(await screen.findByTestId('expected-body-schema-input-error')).toBeTruthy();
    });

    it('passes validation with a valid JSON schema', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeApiGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            verification_status: 'pending',
          }),
        });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('endpoint-url-input');

      const schemaInput = screen.getByTestId('expected-body-schema-input');
      const validJson = JSON.stringify({ type: 'object', properties: { status: { type: 'string' } } }, null, 2);
      fireEvent.changeText(schemaInput, validJson);

      fireEvent.press(screen.getByTestId('submit-api-proof-button'));

      await waitFor(() => {
        expect(screen.queryByTestId('expected-body-schema-input-error')).toBeNull();
      });
    });
  });

  describe('AC 4: Shows request URL, method, headers, and body that were sent', () => {
    it('displays request details in the result after verification', async () => {
      const verificationDetails = {
        request_url: 'https://api.example.com/health',
        request_method: 'GET',
        expected_status: 200,
        request_headers: { Authorization: 'Bearer test-token' },
        actual_status: 200,
        actual_headers: { 'content-type': 'application/json' },
        response_body_preview: '{"status":"ok"}',
        status_passed: true,
        is_json: true,
        schema_passed: true,
      };

      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeApiGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            verification_status: 'pending',
          }),
        });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('endpoint-url-input');

      fireEvent.press(screen.getByTestId('submit-api-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('verification-pending')).toBeTruthy();
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
        expect(screen.getByTestId('verification-result')).toBeTruthy();
      });

      expect(screen.getByText(/https:\/\/api\.example\.com\/health/)).toBeTruthy();
      expect(screen.getByText(/GET/)).toBeTruthy();
    });

    it('shows sent request details section', async () => {
      const verificationDetails = {
        request_url: 'https://api.example.com/health',
        request_method: 'POST',
        expected_status: 200,
        request_headers: { 'X-Custom': 'value' },
        actual_status: 200,
        actual_headers: { 'content-type': 'application/json' },
        response_body_preview: '{"status":"ok"}',
        status_passed: true,
        is_json: true,
        schema_passed: true,
      };

      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeApiGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            verification_status: 'pending',
          }),
        });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('endpoint-url-input');

      fireEvent.press(screen.getByTestId('submit-api-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('verification-pending')).toBeTruthy();
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
        expect(screen.getByTestId('verification-result')).toBeTruthy();
      });

      expect(screen.getByTestId('request-details')).toBeTruthy();
    });
  });

  describe('AC 5: Shows actual vs expected status, headers, and body', () => {
    it('shows comparison of expected vs actual status', async () => {
      const verificationDetails = {
        request_url: 'https://api.example.com/health',
        request_method: 'GET',
        expected_status: 200,
        actual_status: 200,
        actual_headers: {},
        response_body_preview: '{"status":"ok"}',
        status_passed: true,
        is_json: true,
        response_body_json: { status: 'ok' },
        schema_passed: true,
      };

      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeApiGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            verification_status: 'pending',
          }),
        });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('endpoint-url-input');

      fireEvent.press(screen.getByTestId('submit-api-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('verification-pending')).toBeTruthy();
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
        expect(screen.getByTestId('status-result')).toBeTruthy();
      });

      expect(screen.getByText(/Status: Expected/)).toBeTruthy();
    });

    it('shows failed status comparison when status does not match', async () => {
      const verificationDetails = {
        request_url: 'https://api.example.com/health',
        request_method: 'GET',
        expected_status: 200,
        actual_status: 500,
        actual_headers: {},
        response_body_preview: 'Internal Server Error',
        status_passed: false,
        status_failure_reason: 'Expected status 200, got 500',
      };

      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeApiGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            verification_status: 'pending',
          }),
        });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('endpoint-url-input');

      fireEvent.press(screen.getByTestId('submit-api-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('verification-pending')).toBeTruthy();
      });

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          submission_id: 'sub-1',
          verification_status: 'failed',
          verification_details: verificationDetails,
        }),
      });

      jest.advanceTimersByTime(3000);

      await waitFor(() => {
        expect(screen.getByTestId('status-result')).toBeTruthy();
      });

      expect(screen.getByText(/Status: Expected/)).toBeTruthy();
      expect(screen.getByText(/Expected status 200, got 500/)).toBeTruthy();
    });

    it('shows response body preview in the result', async () => {
      const verificationDetails = {
        request_url: 'https://api.example.com/health',
        request_method: 'GET',
        expected_status: 200,
        actual_status: 200,
        actual_headers: { 'content-type': 'application/json' },
        response_body_preview: '{"status":"ok"}',
        status_passed: true,
        is_json: true,
        response_body_json: { status: 'ok' },
        schema_passed: true,
      };

      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeApiGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            verification_status: 'pending',
          }),
        });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('endpoint-url-input');

      fireEvent.press(screen.getByTestId('submit-api-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('verification-pending')).toBeTruthy();
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
        expect(screen.getByTestId('response-body')).toBeTruthy();
      });

      expect(screen.getByText(/"status":"ok"/)).toBeTruthy();
    });

    it('shows schema pass/fail result', async () => {
      const verificationDetails = {
        request_url: 'https://api.example.com/health',
        request_method: 'GET',
        expected_status: 200,
        actual_status: 200,
        actual_headers: {},
        response_body_preview: '{"status":"ok"}',
        status_passed: true,
        is_json: true,
        response_body_json: { status: 'ok' },
        schema_passed: true,
      };

      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeApiGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            verification_status: 'pending',
          }),
        });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('endpoint-url-input');

      fireEvent.press(screen.getByTestId('submit-api-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('verification-pending')).toBeTruthy();
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
        expect(screen.getByTestId('schema-result')).toBeTruthy();
      });

      expect(screen.getByText(/Schema: Passed/)).toBeTruthy();
    });
  });

  describe('AC 6: Templates can be saved with a name and loaded again', () => {
    it('saves current form as a template', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeApiGoal,
      });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('endpoint-url-input');

      const templateNameInput = screen.getByTestId('template-name-input');
      fireEvent.changeText(templateNameInput, 'Health Check');

      fireEvent.press(screen.getByTestId('save-template-button'));

      expect(await screen.findByTestId('template-saved-message')).toBeTruthy();
      const saved = JSON.parse(mockLocalStorage.getItem('api_endpoint_templates') || '{}');
      expect(saved['Health Check']).toBeTruthy();
      expect(saved['Health Check'].url).toBe('https://api.example.com/health');
    });

    it('loads a saved template and fills form fields', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeApiGoal,
      });

      const templates = {
        'Health Check': {
          url: 'https://api.example.com/health',
          method: 'GET',
          headers: [{ key: 'Authorization', value: 'Bearer tok' }],
          expected_status: '200',
          expected_body_schema: JSON.stringify({ type: 'object' }),
        },
      };
      mockLocalStorage.setItem('api_endpoint_templates', JSON.stringify(templates));

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('endpoint-url-input');

      fireEvent.press(screen.getByTestId('load-template-dropdown'));

      const loadButton = await screen.findByText('Health Check');
      fireEvent.press(loadButton);

      await waitFor(async () => {
        const urlInput = screen.getByTestId('endpoint-url-input');
        expect(urlInput.props.value).toBe('https://api.example.com/health');
      });
    });

    it('shows template save button on the screen', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeApiGoal,
      });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('endpoint-url-input');

      expect(screen.getByTestId('save-template-button')).toBeTruthy();
    });
  });

  describe('Navigation and basic display', () => {
    it('shows goal title and description at the top', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeApiGoal,
      });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);

      expect(await screen.findByText('API health check')).toBeTruthy();
      expect(await screen.findByText(/Verify the health endpoint/)).toBeTruthy();
    });

    it('navigates back on back button press', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => activeApiGoal,
      });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('endpoint-url-input');

      fireEvent.press(screen.getByText('←'));

      expect(mockGoBack).toHaveBeenCalled();
    });

    it('shows deadline passed message when goal is expired', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => expiredGoal,
      });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-2" />);

      expect(await screen.findByTestId('deadline-passed-message')).toBeTruthy();
    });

    it('hides submission form when deadline has passed', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => expiredGoal,
      });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-2" />);
      await screen.findByTestId('deadline-passed-message');

      expect(screen.queryByTestId('endpoint-url-input')).toBeNull();
      expect(screen.queryByTestId('submit-api-proof-button')).toBeNull();
    });
  });

  describe('Submission flow', () => {
    it('shows error when URL is empty on submit', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          ...activeApiGoal,
          criteria: {
            ...activeApiGoal.criteria,
            criteria_data: { ...activeApiGoal.criteria.criteria_data, url: '' },
          },
        }),
      });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('endpoint-url-input');

      const urlInput = screen.getByTestId('endpoint-url-input');
      fireEvent.changeText(urlInput, '');

      fireEvent.press(screen.getByTestId('submit-api-proof-button'));

      expect(await screen.findByTestId('endpoint-url-input-error')).toBeTruthy();
    });

    it('shows pending state after successful submission', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeApiGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            verification_status: 'pending',
          }),
        });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('endpoint-url-input');

      fireEvent.press(screen.getByTestId('submit-api-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('verification-pending')).toBeTruthy();
      });
    });

    it('shows API error on submission failure', async () => {
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeApiGoal })
        .mockResolvedValueOnce({
          ok: false,
          status: 422,
          text: async () => JSON.stringify({ detail: 'URL is required' }),
        });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('endpoint-url-input');

      fireEvent.press(screen.getByTestId('submit-api-proof-button'));

      expect(await screen.findByText(/URL is required/i)).toBeTruthy();
    });

    it('shows verified result with green styling', async () => {
      const verificationDetails = {
        request_url: 'https://api.example.com/health',
        request_method: 'GET',
        expected_status: 200,
        actual_status: 200,
        actual_headers: {},
        response_body_preview: '{"status":"ok"}',
        status_passed: true,
        is_json: true,
        schema_passed: true,
      };

      mockFetch
        .mockResolvedValueOnce({ ok: true, json: async () => activeApiGoal })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            submission_id: 'sub-1',
            verification_status: 'pending',
          }),
        });

      const screen = render(<ApiEndpointSubmissionScreen goalId="goal-1" />);
      await screen.findByTestId('endpoint-url-input');

      fireEvent.press(screen.getByTestId('submit-api-proof-button'));

      await waitFor(() => {
        expect(screen.getByTestId('verification-pending')).toBeTruthy();
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
    });
  });
});
