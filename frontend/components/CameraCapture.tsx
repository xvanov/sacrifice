import { useEffect, useState } from 'react';
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

    (async () => {
      const cResult = await requestCamera();
      const mResult = await requestMic();
      setPermStage(
        cResult?.granted === true && mResult?.granted === true ? 'granted' : 'denied',
      );
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