import React, { useEffect, useRef } from 'react';
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
  const hasRequestedRef = useRef(false);

  // Request camera and microphone permissions when permission state
  // becomes available from the Expo hooks.
  useEffect(() => {
    // Guard against infinite re-requests: only issue requests once per mount.
    if (hasRequestedRef.current) return;

    // Wait until both permission objects are available (no longer loading).
    if (!cameraPerm || !micPerm) return;

    // Request any permission that hasn't been granted yet.
    if (!cameraPerm.granted || !micPerm.granted) {
      hasRequestedRef.current = true;
      const requests: Promise<any>[] = [];
      if (!cameraPerm.granted) {
        requests.push(requestCamera());
      }
      if (!micPerm.granted) {
        requests.push(requestMic());
      }
      if (requests.length > 0) {
        Promise.allSettled(requests);
      }
    }
  }, [cameraPerm, micPerm, requestCamera, requestMic]);

  // Derive permission state synchronously from the hook return values.
  // The hooks return null while loading, then the current permission status.
  if (!cameraPerm || !micPerm) {
    return (
      <View testID="camera-capture-loading" className="flex-1 items-center justify-center bg-codex-bg px-6">
        <Text className="font-sans text-sm text-codex-muted">Requesting camera permission...</Text>
      </View>
    );
  }

  // Show denied-permission UI when either camera or microphone is denied.
  // Both are required for recording; denying either blocks the capture flow.
  if (!cameraPerm.granted || !micPerm.granted) {
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