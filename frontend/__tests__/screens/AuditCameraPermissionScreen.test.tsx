import React from 'react';
import { fireEvent, render } from '@testing-library/react-native';
import AuditCameraPermissionScreen from '../../screens/AuditCameraPermissionScreen';

jest.mock('../../components/CameraCapture', () => {
  const React = require('react');
  const { Pressable, Text, View } = require('react-native');

  return function MockCameraCapture({ onCancel }: { onCancel?: () => void }) {
    return (
      <View>
        <Text>Camera capture mounted</Text>
        <Pressable onPress={onCancel}>
          <Text>Close capture</Text>
        </Pressable>
      </View>
    );
  };
});

describe('AuditCameraPermissionScreen', () => {
  it('shows Record proof entry action before camera flow is opened', () => {
    const screen = render(<AuditCameraPermissionScreen />);

    expect(screen.getByText('Record proof')).toBeTruthy();
    expect(screen.queryByText('Camera capture mounted')).toBeNull();
  });

  it('mounts camera capture after Record proof is pressed', () => {
    const screen = render(<AuditCameraPermissionScreen />);

    fireEvent.press(screen.getByText('Record proof'));

    expect(screen.getByText('Camera capture mounted')).toBeTruthy();
  });

  it('returns to Record proof entry when capture is canceled', () => {
    const screen = render(<AuditCameraPermissionScreen />);

    fireEvent.press(screen.getByText('Record proof'));
    fireEvent.press(screen.getByText('Close capture'));

    expect(screen.getByText('Record proof')).toBeTruthy();
    expect(screen.queryByText('Camera capture mounted')).toBeNull();
  });
});
