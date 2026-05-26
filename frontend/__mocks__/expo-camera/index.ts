import React from 'react';

export const CameraView: React.FC<any> = () => null;

export function useCameraPermissions(): [any, () => Promise<any>] {
  return [null, async () => ({ granted: false })];
}

export function useMicrophonePermissions(): [any, () => Promise<any>] {
  return [null, async () => ({ granted: false })];
}