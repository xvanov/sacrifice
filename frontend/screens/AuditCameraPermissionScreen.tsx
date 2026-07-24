import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import CameraCapture from '../components/CameraCapture';

export default function AuditCameraPermissionScreen() {
  const [isCaptureOpen, setIsCaptureOpen] = useState(false);

  if (isCaptureOpen) {
    return <CameraCapture onCancel={() => setIsCaptureOpen(false)} />;
  }

  return (
    <View className="flex-1 items-center justify-center bg-codex-bg px-6">
      <Pressable
        accessibilityRole="button"
        className="rounded-sm bg-codex-accent px-6 py-3 active:bg-codex-accent-light"
        onPress={() => setIsCaptureOpen(true)}
      >
        <Text className="font-sans-medium text-base text-codex-surface">Record proof</Text>
      </Pressable>
    </View>
  );
}
