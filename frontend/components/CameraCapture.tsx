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
  const cameraLoaded = cameraPerms !== null;
  const micLoaded = micPerms !== null;

  const [permStage, setPermStage] = useState<'checking' | 'granted' | 'denied'>(
    () => (camGranted && micGranted ? 'granted' : 'checking'),
  );

  useEffect(() => {
    if (camGranted && micGranted) {
      setPermStage('granted');
      return;
    }

    // If both permissions are already determined (not null) but not both
    // granted, we can skip requesting and go straight to denied.
    if (cameraLoaded && micLoaded) {
      setPermStage('denied');
      return;
    }

    let cancelled = false;

    (async () => {
      try {
        const cResult = cameraLoaded ? cameraPerms : await requestCamera();
        const mResult = micLoaded ? micPerms : await requestMic();
        if (!cancelled) {
          setPermStage(
            cResult?.granted === true && mResult?.granted === true ? 'granted' : 'denied',
          );
        }
      } catch {
        if (!cancelled) {
          setPermStage('denied');
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [camGranted, micGranted, cameraLoaded, micLoaded, cameraPerms, micPerms, requestCamera, requestMic]);

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