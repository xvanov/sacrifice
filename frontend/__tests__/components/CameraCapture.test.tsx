import React from 'react';
import { render } from '@testing-library/react-native';
import CameraCapture from '../../components/CameraCapture';

const mockOnCaptured = jest.fn();
const mockOnCancel = jest.fn();

jest.mock('expo-camera', () => ({
  CameraView: () => null,
  useCameraPermissions: () => [
    {
      status: 'denied',
      granted: false,
      expires: 'never',
      canAskAgain: false,
    },
    jest.fn(),
    jest.fn(),
  ],
  useMicrophonePermissions: () => [
    {
      status: 'denied',
      granted: false,
      expires: 'never',
      canAskAgain: false,
    },
    jest.fn(),
    jest.fn(),
  ],
}));

jest.mock('expo-linking', () => ({
  openSettings: jest.fn(),
}));

beforeEach(() => {
  mockOnCaptured.mockReset();
  mockOnCancel.mockReset();
});

describe('CameraCapture - denied permissions', () => {
  it('renders denied-permission message and actions without crashing', () => {
    const { getByText } = render(
      <CameraCapture onCaptured={mockOnCaptured} onCancel={mockOnCancel} />,
    );

    expect(
      getByText('Camera access is required to submit this proof'),
    ).toBeTruthy();
    expect(getByText('Open settings')).toBeTruthy();
    expect(getByText('Cancel')).toBeTruthy();
  });
});