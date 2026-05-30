import React from 'react';
import { render, fireEvent, waitFor } from '@testing-library/react-native';

// ── Mock expo-camera: permissions start denied; request fns resolve denied ──
const mockRequestCamera = jest.fn();
const mockRequestMic = jest.fn();

// The default __mocks__/expo-camera returns granted:false.
// Override here so we control exactly what the request functions resolve to.
jest.mock('expo-camera', () => ({
  CameraView: () => null,
  useCameraPermissions: () => [{ granted: false }, mockRequestCamera],
  useMicrophonePermissions: () => [{ granted: false }, mockRequestMic],
}));

// ── Mock expo-linking for openSettings ──
const mockOpenSettings = jest.fn();
jest.mock('expo-linking', () => ({
  openSettings: mockOpenSettings,
}));

// ── Import the component under test ──
import CameraCapture from '../../components/CameraCapture';

describe('CameraCapture — denied-permission state', () => {
  const mockOnCancel = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders the denied-permission message when permissions are denied on mount', () => {
    const { getByText } = render(<CameraCapture onCancel={mockOnCancel} />);

    expect(getByText('Camera access is required to submit this proof')).toBeTruthy();
  });

  it('renders Open settings button that calls openSettings when pressed', () => {
    const { getByText } = render(<CameraCapture onCancel={mockOnCancel} />);

    fireEvent.press(getByText('Open settings'));
    expect(mockOpenSettings).toHaveBeenCalledTimes(1);
  });

  it('renders Cancel button that calls onCancel when pressed', () => {
    const { getByText } = render(<CameraCapture onCancel={mockOnCancel} />);

    fireEvent.press(getByText('Cancel'));
    expect(mockOnCancel).toHaveBeenCalledTimes(1);
  });

  it('does not crash when permissions are denied and renders denial UI', () => {
    // Rendering the component should not throw, even with denied permissions.
    // If the component crashes, this render call itself will throw.
    const { getByText } = render(<CameraCapture onCancel={mockOnCancel} />);

    // The denial UI must be present — this also proves the component rendered
    // successfully without crashing.
    expect(getByText('Camera access is required to submit this proof')).toBeTruthy();
  });
});