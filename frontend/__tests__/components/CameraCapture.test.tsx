import React from 'react';
import { render, fireEvent, act } from '@testing-library/react-native';

// ── Mock expo-camera with configurable permission hooks ──
// The request functions return promises the test controls so we can
// observe the intermediate "requesting" state before settlement.

let resolveCameraRequest: (value: any) => void = () => {};
let resolveMicRequest: (value: any) => void = () => {};

const mockRequestCamera = jest.fn(
  () => new Promise((resolve) => { resolveCameraRequest = resolve; }),
);
const mockRequestMic = jest.fn(
  () => new Promise((resolve) => { resolveMicRequest = resolve; }),
);

// Start with null (loading) — tests drive transitions explicitly.
let cameraPermValue: any = null;
let micPermValue: any = null;

const mockUseCameraPermissions = jest.fn(() => [cameraPermValue, mockRequestCamera]);
const mockUseMicrophonePermissions = jest.fn(() => [micPermValue, mockRequestMic]);

jest.mock('expo-camera', () => ({
  CameraView: () => null,
  useCameraPermissions: (...args: any[]) => mockUseCameraPermissions(...args),
  useMicrophonePermissions: (...args: any[]) => mockUseMicrophonePermissions(...args),
}));

// ── Import expo-linking from the auto-discovered __mocks__ ──
import { openSettings } from 'expo-linking';

// ── Import the component under test ──
import CameraCapture from '../../components/CameraCapture';

describe('CameraCapture — denied-permission state', () => {
  const mockOnCancel = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    // Reset resolvers to no-ops.
    resolveCameraRequest = () => {};
    resolveMicRequest = () => {};
    // Default: hooks start as null (loading).
    cameraPermValue = null;
    micPermValue = null;
    mockUseCameraPermissions.mockImplementation(() => [cameraPermValue, mockRequestCamera]);
    mockUseMicrophonePermissions.mockImplementation(() => [micPermValue, mockRequestMic]);
  });

  // ── Full async lifecycle: null → undetermined → requesting → settled (denied) ──

  it('transitions through loading → requesting → denied without flashing denied prematurely', async () => {
    // Phase 1: hooks still loading (null) — loading UI, not denied.
    const { getByText, queryByText, rerender } = render(
      <CameraCapture onCancel={mockOnCancel} />,
    );
    expect(getByText('Requesting camera permission...')).toBeTruthy();
    expect(queryByText('Camera access is required to submit this proof')).toBeNull();

    // Phase 2: hooks resolve to non-granted (undetermined). The effect fires,
    // requestStatus becomes 'requesting'. Denied UI must still be absent.
    cameraPermValue = { granted: false };
    micPermValue = { granted: false };
    mockUseCameraPermissions.mockImplementation(() => [cameraPermValue, mockRequestCamera]);
    mockUseMicrophonePermissions.mockImplementation(() => [micPermValue, mockRequestMic]);

    await act(async () => {
      rerender(<CameraCapture onCancel={mockOnCancel} />);
    });

    // Still loading because requestStatus is 'requesting'.
    expect(getByText('Requesting camera permission...')).toBeTruthy();
    expect(queryByText('Camera access is required to submit this proof')).toBeNull();
    expect(mockRequestCamera).toHaveBeenCalledTimes(1);
    expect(mockRequestMic).toHaveBeenCalledTimes(1);

    // Phase 3: resolve the pending requests — perms stay denied.
    // The .finally() fires, setting requestStatus to 'settled'.
    await act(async () => {
      resolveCameraRequest({ granted: false });
      resolveMicRequest({ granted: false });
      // Flush the microtask so .finally() runs.
      await Promise.resolve();
      rerender(<CameraCapture onCancel={mockOnCancel} />);
    });

    // Now denied UI should be visible with all required elements.
    expect(getByText('Camera access is required to submit this proof')).toBeTruthy();
    expect(getByText('Open settings')).toBeTruthy();
    expect(getByText('Cancel')).toBeTruthy();

    // Exercise recovery paths from the denied screen (folded from CR test-quality #3).
    fireEvent.press(getByText('Open settings'));
    expect(openSettings).toHaveBeenCalledTimes(1);

    fireEvent.press(getByText('Cancel'));
    expect(mockOnCancel).toHaveBeenCalledTimes(1);
  });

  // ── Flash prevention: no denied UI while requests are in flight ──

  it('shows loading UI (not denied) while permission requests are in flight', () => {
    // Start with hooks already resolved to non-granted but requests not settled.
    cameraPermValue = { granted: false };
    micPermValue = { granted: false };

    const { getByText, queryByText } = render(
      <CameraCapture onCancel={mockOnCancel} />,
    );

    // The effect fires synchronously during render and sets requestStatus
    // to 'requesting'. The denied message must NOT appear yet.
    expect(getByText('Requesting camera permission...')).toBeTruthy();
    expect(queryByText('Camera access is required to submit this proof')).toBeNull();
  });

  // ── Granted state renders camera preview ──

  it('renders CameraView and Start recording button when permissions are granted', async () => {
    cameraPermValue = { granted: true };
    micPermValue = { granted: true };

    const { getByText, getByTestId } = render(
      <CameraCapture onCancel={mockOnCancel} />,
    );

    // The component sets requestStatus to 'settled' immediately when both
    // perms are already granted (no request needed).
    await act(async () => {
      await Promise.resolve();
    });

    expect(getByTestId('camera-capture-granted')).toBeTruthy();
    expect(getByText('Start recording')).toBeTruthy();
    // No permission requests should fire when both are already granted.
    expect(mockRequestCamera).not.toHaveBeenCalled();
    expect(mockRequestMic).not.toHaveBeenCalled();
  });
});