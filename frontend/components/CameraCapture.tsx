import React, { useEffect, useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { CameraView, useCameraPermissions, useMicrophonePermissions } from 'expo-camera';
import { openSettings } from 'expo-linking';

export interface CameraCaptureProps {
  onCancel?: () => void;
  onCaptured?: (asset: any) => void;
  maxDurationSeconds?: number;
}

type RequestStatus = 'idle' | 'requesting' | 'settled';

const CameraCapture: React.FC<CameraCaptureProps> = ({ onCancel }) => {
  const [cameraPerm, requestCamera] = useCameraPermissions();
  const [micPerm, requestMic] = useMicrophonePermissions();
  const [requestStatus, setRequestStatus] = useState<RequestStatus>('idle');

  // Request camera and microphone permissions when permission state
  // becomes available from the Expo hooks.
  useEffect(() => {
    // Wait until both permission objects are available (no longer loading).
    if (!cameraPerm || !micPerm) return;

    // Only issue requests once per mount.
    if (requestStatus !== 'idle') return;

    if (!cameraPerm.granted || !micPerm.granted) {
      setRequestStatus('requesting');
      const requests: Promise<any>[] = [];
      if (!cameraPerm.granted) {
        requests.push(requestCamera());
      }
      if (!micPerm.granted) {
        requests.push(requestMic());
      }
      Promise.allSettled(requests).finally(() => {
        setRequestStatus('settled');
      });
    } else {
      setRequestStatus('settled');
    }
  }, [cameraPerm, micPerm, requestCamera, requestMic, requestStatus]);

  // Loading state: hooks haven't resolved yet, or permission requests
  // are still in flight.  Never show denied UI during this phase.
  if (!cameraPerm || !micPerm || requestStatus === 'idle' || requestStatus === 'requesting') {
    return (
      <View testID="camera-capture-loading" className="flex-1 items-center justify-center bg-codex-bg px-6">
        <Text className="font-sans text-sm text-codex-muted">Requesting camera permission...</Text>
      </View>
    );
  }

  // Show denied-permission UI only after requests have settled and
  // either camera or microphone permission is definitively denied.
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

  // Permission granted — camera preview with start-recording shell
  return (
    <View testID="camera-capture-granted" className="flex-1 bg-codex-bg">
      <View className="flex-1 overflow-hidden">
        <CameraView style={{ flex: 1 }} />
      </View>
      <View className="items-center px-6 pb-8 pt-4">
        <Pressable className="rounded-sm bg-codex-accent px-8 py-3">
          <Text className="font-sans-medium text-base text-codex-surface">Start recording</Text>
        </Pressable>
      </View>
    </View>
  );
};

export default CameraCapture;