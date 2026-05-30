import React from 'react';
import { render } from '@testing-library/react-native';

// The CameraCapture component does not exist yet — this import MUST fail.
// When the component is created at frontend/components/CameraCapture.tsx,
// this test will begin to exercise real behavior.
import CameraCapture from '../../components/CameraCapture';

describe('CameraCapture — permission denied state', () => {
  it('renders denied-permission message when camera permission is denied', () => {
    // When expo-camera permissions return denied, the component should
    // show a clear message and not crash.
    const { getByText } = render(
      <CameraCapture
        onCaptured={jest.fn()}
        maxDurationSeconds={60}
      />,
    );

    // The component must surface a human-readable explanation.
    expect(
      getByText(/Camera access is required/i),
    ).toBeTruthy();

    // The component must offer an "Open settings" link.
    expect(getByText(/Open settings/i)).toBeTruthy();

    // The component must offer a "Cancel" link to return to the prior screen.
    expect(getByText(/Cancel/i)).toBeTruthy();
  });

  it('does not crash when permissions are denied', () => {
    // The component must not throw an uncaught error during render
    // when permissions are denied.
    expect(() => {
      render(
        <CameraCapture
          onCaptured={jest.fn()}
          maxDurationSeconds={60}
        />,
      );
    }).not.toThrow();
  });
});