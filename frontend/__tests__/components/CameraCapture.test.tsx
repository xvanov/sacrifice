import React from 'react';
import { render, fireEvent, waitFor } from '@testing-library/react-native';
import { openSettings } from 'expo-linking';

// ── Mock expo-camera hooks: permission null = unloaded, request fns resolve denied ──
const mockRequestCamera = jest.fn();
const mockRequestMic = jest.fn();

jest.mock('expo-camera', () => ({
  CameraView: () => null,
  useCameraPermissions: () => [null, mockRequestCamera],
  useMicrophonePermissions: () => [null, mockRequestMic],
}));

// ── Import the component under test ──
// This import MUST fail (module not found) until the Dev creates the file.
import CameraCapture from '../../components/CameraCapture';

describe('CameraCapture — denied-permission state', () => {
  const mockOnCancel = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    mockRequestCamera.mockResolvedValue({ granted: false });
    mockRequestMic.mockResolvedValue({ granted: false });
  });

  it('renders the denied-permission message when camera and mic permissions are denied', async () => {
    const { getByText } = render(<CameraCapture onCancel={mockOnCancel} />);

    // Phase 1: while permissions are resolving, show checking state
    expect(getByText('Requesting camera access...')).toBeTruthy();

    // Phase 2: after both permission requests settle as denied, show denial UI
    await waitFor(() => {
      expect(
        getByText('Camera access is required to submit this proof'),
      ).toBeTruthy();
    });

    // All three denial affordances rendered
    expect(getByText('Open settings')).toBeTruthy();
    expect(getByText('Cancel')).toBeTruthy();

    // Permissions were actually requested
    expect(mockRequestCamera).toHaveBeenCalledTimes(1);
    expect(mockRequestMic).toHaveBeenCalledTimes(1);
  });

  it('calls openSettings when "Open settings" is pressed in denied state', async () => {
    const { getByText } = render(<CameraCapture onCancel={mockOnCancel} />);

    await waitFor(() => {
      expect(
        getByText('Camera access is required to submit this proof'),
      ).toBeTruthy();
    });

    fireEvent.press(getByText('Open settings'));
    expect(openSettings).toHaveBeenCalledTimes(1);
  });

  it('calls onCancel when "Cancel" is pressed in denied state', async () => {
    const { getByText } = render(<CameraCapture onCancel={mockOnCancel} />);

    await waitFor(() => {
      expect(
        getByText('Camera access is required to submit this proof'),
      ).toBeTruthy();
    });

    fireEvent.press(getByText('Cancel'));
    expect(mockOnCancel).toHaveBeenCalledTimes(1);
  });

  it('does not crash when rendering denied-permission state', () => {
    // Synchronous render — component must not throw even with denied perms
    const { getByText } = render(<CameraCapture onCancel={mockOnCancel} />);
    // The checking placeholder proves the render completed without throwing
    expect(getByText('Requesting camera access...')).toBeTruthy();
  });
});