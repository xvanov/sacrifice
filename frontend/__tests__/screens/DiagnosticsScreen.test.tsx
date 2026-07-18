import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react-native';
import DiagnosticsScreen from '../../screens/DiagnosticsScreen';

const mockGoBack = jest.fn();

jest.mock('../../hooks/useNavigation', () => ({
  useNavigation: () => ({
    currentScreen: { name: 'diagnostics' },
    navigate: jest.fn(),
    goBack: mockGoBack,
  }),
}));

// AC6.1: resolved API URL is shown
// AC6.2: backend /api/health status is shown
// AC6.3: platform/OS is shown
// AC6.4: app version is shown

describe('DiagnosticsScreen', () => {
  let mockFetch: jest.Mock;

  beforeEach(() => {
    mockFetch = jest.fn();
    global.fetch = mockFetch as any;
    mockGoBack.mockReset();
  });

  it('shows the resolved API URL (AC6.1)', () => {
    const { getByText } = render(<DiagnosticsScreen />);
    expect(getByText('API Base URL')).toBeTruthy();
    // The resolved URL should contain http (localhost default)
    const urlEl = getByText(/^https?:\/\/.+/);
    expect(urlEl).toBeTruthy();
  });

  it('shows backend health status on initial load (AC6.2)', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: 'ok', version: '1.0.0' }),
    });

    const { findByText } = render(<DiagnosticsScreen />);

    await findByText(/status.*ok/i);
  });

  it('shows platform and OS information (AC6.3)', () => {
    const { getByText } = render(<DiagnosticsScreen />);
    expect(getByText('Platform')).toBeTruthy();
    expect(getByText('OS Version')).toBeTruthy();
    expect(getByText('React Native')).toBeTruthy();
  });

  it('shows app version (AC6.4)', () => {
    const { getByText } = render(<DiagnosticsScreen />);
    expect(getByText('App Version')).toBeTruthy();
  });

  it('shows connection error when health check fails', async () => {
    mockFetch.mockRejectedValueOnce(new Error('Network Error'));

    const { findByText } = render(<DiagnosticsScreen />);

    await findByText('Connection Error');
    await findByText('Network Error');
  });

  it('retry button updates UI with new health payload', async () => {
    mockFetch
      .mockResolvedValueOnce({ ok: true, json: async () => ({ status: 'ok' }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ status: 'ok', retry: true }) });

    const { findByTestId, findByText, getByText } = render(<DiagnosticsScreen />);

    // Wait for initial health payload
    await findByText(/status.*ok/i);

    // Capture the initial rendered health text
    const initialEl = getByText(/status.*ok/i);
    expect(initialEl).toBeTruthy();

    // Press retry — fetches again with different payload
    const retryBtn = await findByTestId('diagnostics-retry');
    fireEvent.press(retryBtn);

    // UI must reflect the retried payload: assert the new JSON content is
    // visible to the user (not just that fetch was called again).
    // JSON.stringify produces {"status":"ok","retry":true} — no spaces.
    const retryEl = await findByText(/"retry":\s*true/i);
    expect(retryEl).toBeTruthy();

    // Verify the rendered text contains the full retry payload the user sees
    expect(retryEl.props.children).toEqual(expect.stringContaining('"retry":true'));

    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it('back button calls goBack', () => {
    const { getByText } = render(<DiagnosticsScreen />);
    const backBtn = getByText('←');
    fireEvent.press(backBtn);
    expect(mockGoBack).toHaveBeenCalledTimes(1);
  });

  it('shows HTTP error status when backend returns non-200', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 503,
      statusText: 'Service Unavailable',
    });

    const { findByText } = render(<DiagnosticsScreen />);

    await findByText(/HTTP 503/i);
  });
});