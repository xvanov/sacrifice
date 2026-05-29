import React from 'react';
import { render, fireEvent, waitFor } from '@testing-library/react-native';
import { openSettings } from 'expo-linking';

// ── In-test mock overrides the manual __mocks__/expo-camera so we control ──
// ── the async permission-request flow (hooks return null = unloaded).    ──

const mockRequestCamera = jest.fn();
const mockRequestMic = jest.fn();

jest.mock('expo-camera', () => ({
  CameraView: () => null,
  useCameraPermissions: () => [null, mockRequestCamera],
  useMicrophonePermissions: () => [null, mockRequestMic],
}));

import CameraCapture from '../../components/CameraCapture';

describe('CameraCapture — denied-permission state', () => {
  const mockOnCancel = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    mockRequestCamera.mockResolvedValue({ granted: false });
    mockRequestMic.mockResolvedValue({ granted: false });
  });

  /**
   * Drive the component through the full unloaded→denied path:
   *  1. Render with both permission hooks returning null (unloaded).
   *  2. Assert the checking placeholder is visible.
   *  3. Wait for requestCamera / requestMic to resolve as denied.
   *  4. Assert the denied UI appears with all three affordances.
   */
  it('renders the denied-permission UI when camera permission is denied', async () => {
    const { getByText } = render(
      <CameraCapture onCancel={mockOnCancel} />,
    );

    // Still requesting — permission hooks haven't resolved yet
    expect(getByText('Requesting camera access...')).toBeTruthy();

    // Wait for the async effect to settle into the denied state
    await waitFor(() => {
      expect(getByText('Camera access is required to submit this proof')).toBeTruthy();
    });

    // All three denied-state affordances are present
    expect(getByText('Open settings')).toBeTruthy();
    expect(getByText('Cancel')).toBeTruthy();

    // Permissions were actually requested (not just statically loaded)
    expect(mockRequestCamera).toHaveBeenCalledTimes(1);
    expect(mockRequestMic).toHaveBeenCalledTimes(1);
  });

  /**
   * After the denied UI is visible, pressing "Open settings" must call
   * the platform settings bridge so the user can change permissions.
   */
  it('calls openSettings when Open settings is pressed after denied', async () => {
    const { getByText } = render(
      <CameraCapture onCancel={mockOnCancel} />,
    );

    await waitFor(() => {
      expect(getByText('Camera access is required to submit this proof')).toBeTruthy();
    });

    fireEvent.press(getByText('Open settings'));
    expect(openSettings).toHaveBeenCalledTimes(1);
  });

  /**
   * After the denied UI is visible, pressing "Cancel" must invoke the
   * onCancel callback exactly once so the caller can navigate back.
   */
  it('calls onCancel when Cancel is pressed after denied', async () => {
    const { getByText } = render(
      <CameraCapture onCancel={mockOnCancel} />,
    );

    await waitFor(() => {
      expect(getByText('Camera access is required to submit this proof')).toBeTruthy();
    });

    fireEvent.press(getByText('Cancel'));
    expect(mockOnCancel).toHaveBeenCalledTimes(1);
  });
});