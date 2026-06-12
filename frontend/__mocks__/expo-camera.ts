import React, { useState, useCallback } from 'react';
import { View } from 'react-native';

// Mock values controllable from tests via the exported ref.
// Tests import { mockCamera } from '__mocks__/expo-camera' and
// set fields before rendering.
export const mockCamera = {
  requestPermissions: jest.fn(),
  getPermissions: jest.fn(),
  requestMicrophonePermissions: jest.fn(),
  getMicrophonePermissions: jest.fn(),
  record: jest.fn(),
  recordAsync: jest.fn(),
  stopRecording: jest.fn(),
};

export const CameraView = React.forwardRef((props: any, ref: any) => {
  React.useImperativeHandle(ref, () => ({
    record: mockCamera.record,
    recordAsync: mockCamera.recordAsync,
    stopRecording: mockCamera.stopRecording,
  }));
  return React.createElement(View, { testID: 'camera-preview', ...props });
});

function usePermissionState(
  getPermissionMock: typeof mockCamera.getPermissions,
  requestPermissionMock: typeof mockCamera.requestPermissions
) {
  const [permission, setPermission] = useState<{
    granted: boolean;
    canAskAgain: boolean;
    expires: string;
  } | null>(() => {
    const initial = getPermissionMock();
    return initial ?? null;
  });

  const requestPermission = useCallback(async () => {
    let result = await requestPermissionMock();
    if (!result) {
      result = await getPermissionMock();
    }
    if (result) {
      setPermission(result);
    }
    return result;
  }, [getPermissionMock, requestPermissionMock]);

  const getPermission = useCallback(async () => {
    const result = await getPermissionMock();
    if (result) {
      setPermission(result);
    }
    return result;
  }, [getPermissionMock]);

  return [permission, requestPermission, getPermission] as const;
}

export function useCameraPermissions() {
  return usePermissionState(mockCamera.getPermissions, mockCamera.requestPermissions);
}

export function useMicrophonePermissions() {
  return usePermissionState(
    mockCamera.getMicrophonePermissions,
    mockCamera.requestMicrophonePermissions
  );
}

export const Camera = {
  VideoStabilization: { off: 0 },
};