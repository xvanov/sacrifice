import React from 'react';
import { render, fireEvent, waitFor } from '@testing-library/react-native';
import { openSettings } from 'expo-linking';

// ── Mock expo-camera: permissions start unloaded (null); request fns resolve denied ──
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

  it('requests camera and mic permissions on mount then renders denied UI when both are denied', async () => {
    const { getByText } = render(<CameraCapture onCancel={mockOnCancel} />);

    // Initial render: permission hooks returned null so a checking placeholder is shown
    expect(getByText('Requesting camera access...')).toBeTruthy();

    // After both permission requests resolve as denied the denial UI appears
    await waitFor(() => {
      expect(getByText('Camera access is required to submit this proof')).toBeTruthy();
    });

    // The three denied-state affordances are all present
    expect(getByText('Open settings')).toBeTruthy();
    expect(getByText('Cancel')).toBeTruthy();

    // Permission request functions were each called exactly once
    expect(mockRequestCamera).toHaveBeenCalledTimes(1);
    expect(mockRequestMic).toHaveBeenCalledTimes(1);
  });

  it('calls openSettings when "Open settings" is pressed', async () => {
    const { getByText } = render(<CameraCapture onCancel={mockOnCancel} />);

    await waitFor(() => {
      expect(getByText('Camera access is required to submit this proof')).toBeTruthy();
    });

    fireEvent.press(getByText('Open settings'));
    expect(openSettings).toHaveBeenCalledTimes(1);
  });

  it('calls onCancel when "Cancel" is pressed', async () => {
    const { getByText } = render(<CameraCapture onCancel={mockOnCancel} />);

    await waitFor(() => {
      expect(getByText('Camera access is required to submit this proof')).toBeTruthy();
    });

    fireEvent.press(getByText('Cancel'));
    expect(mockOnCancel).toHaveBeenCalledTimes(1);
  });
});