import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';

// ── Mock expo-camera with configurable permission hooks ──
const mockRequestCamera = jest.fn();
const mockRequestMic = jest.fn();

const mockUseCameraPermissions = jest.fn(() => [{ granted: false }, mockRequestCamera]);
const mockUseMicrophonePermissions = jest.fn(() => [{ granted: false }, mockRequestMic]);

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
    mockUseCameraPermissions.mockReturnValue([{ granted: false }, mockRequestCamera]);
    mockUseMicrophonePermissions.mockReturnValue([{ granted: false }, mockRequestMic]);
  });

  it('renders the denied-permission message and does not crash when permissions are denied', () => {
    // Rendering with denied permissions should not throw — if the component
    // crashes, the render call itself will throw and fail the test.
    const { getByText } = render(<CameraCapture onCancel={mockOnCancel} />);
    expect(getByText('Camera access is required to submit this proof')).toBeTruthy();
  });

  it('requests camera and microphone permissions on mount when both are not granted', () => {
    render(<CameraCapture onCancel={mockOnCancel} />);
    expect(mockRequestCamera).toHaveBeenCalledTimes(1);
    expect(mockRequestMic).toHaveBeenCalledTimes(1);
  });

  it('renders denied-permission state when only microphone permission is denied', () => {
    mockUseCameraPermissions.mockReturnValue([{ granted: true }, mockRequestCamera]);
    mockUseMicrophonePermissions.mockReturnValue([{ granted: false }, mockRequestMic]);

    const { getByText } = render(<CameraCapture onCancel={mockOnCancel} />);
    expect(getByText('Camera access is required to submit this proof')).toBeTruthy();
  });

  it('renders Open settings button that calls openSettings when pressed from the denied-permission UI', () => {
    const { getByText } = render(<CameraCapture onCancel={mockOnCancel} />);

    // Verify the denied-permission shell is rendered before interacting.
    expect(getByText('Camera access is required to submit this proof')).toBeTruthy();

    fireEvent.press(getByText('Open settings'));
    expect(openSettings).toHaveBeenCalledTimes(1);
  });

  it('renders Cancel button that calls onCancel when pressed', () => {
    const { getByText } = render(<CameraCapture onCancel={mockOnCancel} />);

    fireEvent.press(getByText('Cancel'));
    expect(mockOnCancel).toHaveBeenCalledTimes(1);
  });
});