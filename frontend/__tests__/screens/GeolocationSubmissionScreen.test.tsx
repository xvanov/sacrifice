import React from 'react';
import { render, fireEvent, waitFor } from '@testing-library/react-native';
import GeolocationSubmissionScreen from '../../screens/GeolocationSubmissionScreen';

const mockGoBack = jest.fn();

jest.mock('../../hooks/useNavigation', () => ({
  useNavigation: () => ({
    currentScreen: { name: 'geolocation-proof-submission', goalId: 'goal-1' },
    navigate: jest.fn(),
    goBack: mockGoBack,
  }),
}));

jest.mock('react-native', () => {
  const RN = jest.requireActual('react-native');
  RN.Platform.OS = 'web';
  return RN;
});

jest.mock('../../services/api', () => ({
  api: {
    getGoal: jest.fn(),
    submitGeolocationProof: jest.fn(),
    getVerificationStatus: jest.fn(),
  },
}));

const { api } = jest.requireMock('../../services/api');

const goal = {
  id: 'goal-1',
  title: 'Be at the gym',
  description: null,
  goal_type: 'geolocation',
  pledge_amount: 500,
  currency: 'usd',
  deadline: new Date(Date.now() + 24 * 3600 * 1000).toISOString(),
  status: 'active',
  charity_id: null,
  criteria: {
    criteria_type: 'geolocation',
    criteria_data: { target_latitude: 37.8199, target_longitude: -122.4783, radius_m: 150 },
  },
};

function mockGeolocation(pos?: { latitude: number; longitude: number; accuracy: number }, errCode?: number) {
  const geolocation = {
    getCurrentPosition: (ok: any, fail: any) => {
      if (pos) ok({ coords: pos });
      else fail({ code: errCode ?? 1, PERMISSION_DENIED: 1 });
    },
  };
  Object.defineProperty(global, 'navigator', {
    value: { geolocation },
    writable: true,
    configurable: true,
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  api.getGoal.mockResolvedValue({ data: goal });
});

describe('GeolocationSubmissionScreen', () => {
  it('shows the target, radius, and a capture button', async () => {
    render(<GeolocationSubmissionScreen goalId="goal-1" />);
    const screen = render(<GeolocationSubmissionScreen goalId="goal-1" />);
    await screen.findByTestId('capture-location-button');
    expect(screen.getByText(/Be within 150m of the target/)).toBeTruthy();
    expect(screen.getByText(/37.81990, -122.47830/)).toBeTruthy();
  });

  it('captures the browser location and reveals Check in', async () => {
    mockGeolocation({ latitude: 37.82, longitude: -122.478, accuracy: 10 });
    const screen = render(<GeolocationSubmissionScreen goalId="goal-1" />);
    fireEvent.press(await screen.findByTestId('capture-location-button'));
    await screen.findByTestId('captured-position');
    expect(screen.getByText(/37.82000, -122.47800/)).toBeTruthy();
    expect(screen.getByTestId('submit-proof-button')).toBeTruthy();
  });

  it('shows a permission-denied message when geolocation is blocked', async () => {
    mockGeolocation(undefined, 1);
    const screen = render(<GeolocationSubmissionScreen goalId="goal-1" />);
    fireEvent.press(await screen.findByTestId('capture-location-button'));
    await screen.findByTestId('api-error');
    expect(screen.getByText(/permission was denied/i)).toBeTruthy();
  });

  it('submits captured coordinates and polls to verified', async () => {
    mockGeolocation({ latitude: 37.82, longitude: -122.478, accuracy: 10 });
    api.submitGeolocationProof.mockResolvedValue({ data: { submission_id: 'sub-1' } });
    api.getVerificationStatus.mockResolvedValue({
      data: {
        submission_id: 'sub-1',
        verification_status: 'verified',
        verification_details: { distance_m: 42 },
      },
    });
    jest.useFakeTimers();
    const screen = render(<GeolocationSubmissionScreen goalId="goal-1" />);
    fireEvent.press(await screen.findByTestId('capture-location-button'));
    fireEvent.press(await screen.findByTestId('submit-proof-button'));
    await waitFor(() => {
      expect(api.submitGeolocationProof).toHaveBeenCalledWith('goal-1', {
        latitude: 37.82,
        longitude: -122.478,
        accuracy_m: 10,
      });
    });
    await screen.findByTestId('verification-pending');
    jest.advanceTimersByTime(2100);
    await screen.findByTestId('verification-verified');
    expect(screen.getByText(/42m/)).toBeTruthy();
    jest.useRealTimers();
  });

  it('shows failure details when the check-in is outside the radius', async () => {
    mockGeolocation({ latitude: 37.9, longitude: -122.478, accuracy: 10 });
    api.submitGeolocationProof.mockResolvedValue({ data: { submission_id: 'sub-1' } });
    api.getVerificationStatus.mockResolvedValue({
      data: {
        submission_id: 'sub-1',
        verification_status: 'failed',
        verification_details: {
          failure_reason: 'Location is 8,900m from the target — outside the allowed 150m radius.',
        },
      },
    });
    jest.useFakeTimers();
    const screen = render(<GeolocationSubmissionScreen goalId="goal-1" />);
    fireEvent.press(await screen.findByTestId('capture-location-button'));
    fireEvent.press(await screen.findByTestId('submit-proof-button'));
    await screen.findByTestId('verification-pending');
    jest.advanceTimersByTime(2100);
    await screen.findByTestId('verification-failed');
    expect(screen.getByText(/8,900m from the target/)).toBeTruthy();
    jest.useRealTimers();
  });

  it('blocks check-in after the deadline', async () => {
    api.getGoal.mockResolvedValue({
      data: { ...goal, deadline: new Date(Date.now() - 3600 * 1000).toISOString() },
    });
    const screen = render(<GeolocationSubmissionScreen goalId="goal-1" />);
    await screen.findByTestId('deadline-passed-message');
    expect(screen.queryByTestId('capture-location-button')).toBeNull();
  });
});
