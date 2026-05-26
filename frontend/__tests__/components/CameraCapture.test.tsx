import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';

// Manual mock at __mocks__/expo-camera/index.ts provides default denied-permission stubs.
// Manual mock at __mocks__/expo-linking/index.ts provides openSettings stub.

import CameraCapture from '../../components/CameraCapture';

describe('CameraCapture — denied-permission state', () => {
  const mockOnCancel = jest.fn();

  beforeEach(() => {
    mockOnCancel.mockReset();
  });

  it('renders the denied-permission UI when camera permission is denied', () => {
    const { getByText } = render(
      <CameraCapture onCancel={mockOnCancel} />,
    );

    // Assert the denied-permission message is visible
    expect(getByText('Camera access is required to submit this proof')).toBeTruthy();

    // Assert the "Open settings" affordance is present
    expect(getByText('Open settings')).toBeTruthy();

    // Assert the "Cancel" affordance is present
    expect(getByText('Cancel')).toBeTruthy();
  });

  it('calls onCancel when Cancel is pressed', () => {
    const { getByText } = render(
      <CameraCapture onCancel={mockOnCancel} />,
    );

    fireEvent.press(getByText('Cancel'));
    expect(mockOnCancel).toHaveBeenCalledTimes(1);
  });

  it('does not crash when permissions are denied', () => {
    // Rendering without throwing is the assertion
    expect(() => {
      render(<CameraCapture onCancel={mockOnCancel} />);
    }).not.toThrow();
  });
});