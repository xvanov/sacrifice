import React from 'react';

// Mock values controllable from tests via the exported ref.
// Tests import { mockCamera } from '__mocks__/expo-camera' and
// set fields before rendering.
export const mockCamera = {
  requestPermissions: jest.fn(),
  getPermissions: jest.fn(),
  recordAsync: jest.fn(),
  stopRecording: jest.fn(),
};

export const CameraView = React.forwardRef((props: any, ref: any) => {
  React.useImperativeHandle(ref, () => ({
    recordAsync: mockCamera.recordAsync,
    stopRecording: mockCamera.stopRecording,
  }));
  return null;
});

export function useCameraPermissions() {
  return [null, mockCamera.requestPermissions, mockCamera.getPermissions] as const;
}

export const Camera = {
  VideoStabilization: { off: 0 },
};