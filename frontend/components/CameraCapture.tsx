import React from 'react';
import { View } from 'react-native';

export interface CameraCaptureProps {
  onCancel?: () => void;
  onCaptured?: (asset: any) => void;
  maxDurationSeconds?: number;
}

const CameraCapture: React.FC<CameraCaptureProps> = () => {
  return <View testID="camera-capture-stub" />;
};

export default CameraCapture;