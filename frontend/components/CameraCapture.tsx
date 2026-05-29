import { useCallback, useEffect, useState } from 'react';
import { Text, View, Pressable } from 'react-native';
import { CameraView, useCameraPermissions, useMicrophonePermissions } from 'expo-camera';
import { openSettings } from 'expo-linking';

interface Props {
  onCancel: () => void;
}

export default function CameraCapture({ onCancel }: Props) {
  const [cameraPerms, requestCamera] = useCameraPermissions();
  const [micPerms, requestMic] = useMicrophonePermissions();

  const camGranted = cameraPerms?.granted === true;
  const micGranted = micPerms?.granted === true;

  const [permStage, setPermStage] = useState<'checking' | 'granted' | 'denied'>(
    () => (camGranted && micGranted ? 'granted' : 'checking'),
  );

  useEffect(() => {
    if (camGranted && micGranted) {
      setPermStage('granted');
      return;
    }

    let cancelled = false;

    (async () => {
      // Request both permissions together so that a failure in one does
      // not prevent the other from being requested (CR #2).
      const settleResults = await Promise.allSettled([
        camGranted ? Promise.resolve(cameraPerms) : requestCamera(),
        micGranted ? Promise.resolve(micPerms) : requestMic(),
      ]);

      if (cancelled) return;

      const camResult =
        settleResults[0].status === 'fulfilled' ? settleResults[0].value : null;
      const micResult =
        settleResults[1].status === 'fulfilled' ? settleResults[1].value : null;

      setPermStage(
        camResult?.granted === true && micResult?.granted === true
          ? 'granted'
          : 'denied',
      );
    })();

    return () => {
      cancelled = true;
    };
  }, [camGranted, micGranted, cameraPerms, micPerms, requestCamera, requestMic]);

  const handleOpenSettings = useCallback(() => {
    try {
      const result = openSettings();
      if (result && typeof result.catch === 'function') {
        result.catch(() => {});
      }
    } catch {
      // Swallow — keep the denial screen stable
    }
  }, []);

  if (permStage === 'checking') {
    return (
      <View className="flex-1 items-center justify-center bg-codex-bg px-6">
        <Text className="font-sans text-sm text-codex-muted">Requesting camera access...</Text>
      </View>
    );
  }

  if (permStage === 'denied') {
    return (
      <View className="flex-1 items-center justify-center bg-codex-bg px-6">
        <Text className="mb-6 text-center font-sans text-base text-codex-text">
          Camera access is required to submit this proof
        </Text>
        <Pressable
          onPress={handleOpenSettings}
          className="mb-4 rounded-sm bg-codex-accent px-6 py-3"
        >
          <Text className="font-sans text-sm font-medium text-codex-surface">
            Open settings
          </Text>
        </Pressable>
        <Pressable onPress={onCancel} className="px-6 py-3">
          <Text className="font-sans text-sm text-codex-muted">Cancel</Text>
        </Pressable>
      </View>
    );
  }

  // permStage === 'granted' — recording lifecycle is out of scope for this story
  return (
    <View className="flex-1 bg-codex-bg">
      <CameraView className="flex-1" facing="back" />
      <View className="items-center px-6 pb-10 pt-4">
        <Pressable className="rounded-sm bg-codex-accent px-8 py-3">
          <Text className="font-sans text-sm font-medium text-codex-surface">
            Start recording
          </Text>
        </Pressable>
      </View>
    </View>
  );
}