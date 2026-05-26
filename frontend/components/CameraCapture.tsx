import { useEffect, useState } from 'react';
import { Text, View, Pressable } from 'react-native';
import { useCameraPermissions, useMicrophonePermissions } from 'expo-camera';
import { openSettings } from 'expo-linking';

interface Props {
  onCancel: () => void;
}

export default function CameraCapture({ onCancel }: Props) {
  const [cameraPerms, requestCamera] = useCameraPermissions();
  const [micPerms, requestMic] = useMicrophonePermissions();

  const camGranted = cameraPerms?.granted === true;
  const micGranted = micPerms?.granted === true;
  const camLoaded = cameraPerms !== null;
  const micLoaded = micPerms !== null;
  const bothLoaded = camLoaded && micLoaded;

  const [permStage, setPermStage] = useState<'checking' | 'granted' | 'denied'>(() => {
    if (bothLoaded) {
      return camGranted && micGranted ? 'granted' : 'denied';
    }
    return 'checking';
  });

  useEffect(() => {
    if (bothLoaded) return;
    (async () => {
      let cGranted = camGranted;
      let mGranted = micGranted;

      if (!camLoaded) {
        const result = await requestCamera();
        cGranted = result?.granted === true;
      }
      if (!micLoaded) {
        const result = await requestMic();
        mGranted = result?.granted === true;
      }

      setPermStage(cGranted && mGranted ? 'granted' : 'denied');
    })();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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
          onPress={() => openSettings()}
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
    <View className="flex-1 items-center justify-center bg-codex-bg px-6">
      <Text className="font-sans text-sm text-codex-muted">Camera ready — recording not yet implemented</Text>
    </View>
  );
}