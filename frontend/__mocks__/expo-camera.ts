import React, { useState, useCallback } from 'react';

// Mock values controllable from tests via the exported ref.
// Tests import { mockCamera } from '__mocks__/expo-camera' and
// set fields before rendering.
export const mockCamera = {
  requestPermissions: jest.fn(),
  getPermissions: jest.fn(),
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
  return null;
});

export function useCameraPermissions() {
  const [permission, setPermission] = useState<{
    granted: boolean;
    canAskAgain: boolean;
    expires: string;
  } | null>(() => {
    // Resolve initial permission synchronously so tests don't need
    // to flush async microtasks before the first assertion.
    const initial = mockCamera.getPermissions();
    return initial ?? null;
  });

  const requestPermission = useCallback(async () => {
    let result = await mockCamera.requestPermissions();
    if (!result) {
      result = await mockCamera.getPermissions();
    }
    if (result) {
      setPermission(result);
    }
    return result;
  }, []);

  const getPermission = useCallback(async () => {
    const result = await mockCamera.getPermissions();
    if (result) {
      setPermission(result);
    }
    return result;
  }, []);

  return [permission, requestPermission, getPermission] as const;
}

export const Camera = {
  VideoStabilization: { off: 0 },
};