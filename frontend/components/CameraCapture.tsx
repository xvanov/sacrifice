import React, { useEffect } from 'react';
import { Pressable, Text, View } from 'react-native';
import { useCameraPermissions, useMicrophonePermissions } from 'expo-camera';
import { openSettings } from 'expo-linking';

export interface CameraCaptureProps {
  onCancel?: () => void;
  onCaptured?: (asset: any) => void;
  maxDurationSeconds?: number;
}

const CameraCapture: React.FC<CameraCaptureProps> = ({ onCancel }) => {
  const [cameraPerm, requestCamera] = useCameraPermissions();
  const [micPerm, requestMic] = useMicrophonePermissions();

  // Request camera and microphone permissions on mount if not already granted.
  useEffect(() => {
    const requests: Promise<any>[] = [];
    if (cameraPerm && !cameraPerm.granted) {
      requests.push(requestCamera());
    }
    if (micPerm && !micPerm.granted) {
      requests.push(requestMic());
    }
    if (requests.length > 0) {
      Promise.allSettled(requests);
    }
    // Only run on mount — permission objects and request fns are stable
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Derive permission state synchronously from the hook return values.
  // The hooks return null while loading, then the current permission status.
  if (!cameraPerm) {
    return (
      <View testID="camera-capture-loading" className="flex-1 items-center justify-center bg-codex-bg px-6">
        <Text className="font-sans text-sm text-codex-muted">Requesting camera permission...</Text>
      </View>
    );
  }

  if (!cameraPerm.granted) {
    return (
      <View testID="camera-capture-denied" className="flex-1 items-center justify-center bg-codex-bg px-6">
        <Text className="mb-4 text-center font-sans text-base text-codex-text">
          Camera access is required to submit this proof
        </Text>
        <Pressable
          onPress={() => openSettings()}
          className="mb-3 rounded-sm bg-codex-accent px-6 py-3"
        >
          <Text className="font-sans-medium text-base text-codex-surface">Open settings</Text>
        </Pressable>
        <Pressable onPress={() => onCancel?.()} className="px-6 py-3">
          <Text className="font-sans text-base text-codex-muted">Cancel</Text>
        </Pressable>
      </View>
    );
  }

  // Permission granted — placeholder for future recording lifecycle
  return (
    <View testID="camera-capture-granted" className="flex-1 items-center justify-center bg-codex-bg px-6">
      <Text className="font-sans text-sm text-codex-muted">Camera ready</Text>
    </View>
  );
};

export default CameraCapture;