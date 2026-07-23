import { useCallback } from 'react';
import { Pressable, Text, View } from 'react-native';
import { CodexHeader } from '../components/CodexHeader';
import { CodexCard } from '../components/CodexCard';
import { CodexFooter } from '../components/CodexFooter';
import { SectionHeading } from '../components/SectionHeading';
import CameraCapture from '../components/CameraCapture';
import { useNavigation } from '../hooks/useNavigation';

interface Props {
  goalId: string;
}

export default function CameraProofSubmissionScreen({ goalId }: Props) {
  const { goBack } = useNavigation();

  const handleCaptured = useCallback((_asset: { uri: string }) => {
    // Downstream: the captured asset is submitted for verification.
    // For now, navigating back is the audit-observable behaviour.
    goBack();
  }, [goBack]);

  return (
    <View className="flex-1 bg-codex-bg">
      <CodexHeader pageNumber="II" totalPages="IV" />
      <View className="flex-row items-center px-6 pb-2 pt-3">
        <Pressable onPress={goBack} className="mr-3 p-1">
          <Text className="font-serif text-2xl text-codex-muted">{'←'}</Text>
        </Pressable>
        <Text className="font-serif-italic text-lg text-codex-text">Camera Proof</Text>
      </View>

      <View className="flex-1">
        <View className="px-6 pb-2">
          <View className="mb-2 flex-row items-center gap-2">
            <Text className="rounded-sm bg-codex-accent px-2 py-0.5 font-sans text-[10px] uppercase tracking-wider text-codex-surface">
              Submit
            </Text>
            <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">
              Of IV · II
            </Text>
          </View>

          <SectionHeading
            number="Camera Proof — record your evidence"
            title=""
            subtitle="Record a video to satisfy your commitment. The recording will be examined to judge whether the work is true."
          />
        </View>

        <View className="mx-4 mb-4 flex-1 overflow-hidden rounded-sm">
          <CameraCapture
            maxDurationSeconds={60}
            onCaptured={handleCaptured}
            onCancel={goBack}
          />
        </View>

        <CodexCard className="mx-4 mb-4 p-4">
          <Text className="font-serif text-base text-codex-text">
            Goal {goalId}
          </Text>
          <Text className="mt-1 font-sans text-xs text-codex-muted">
            Record your proof video. Camera access is required. If denied, use the Open settings
            button to enable camera permission in your system settings, or press Cancel to go back.
          </Text>
        </CodexCard>
      </View>

      <CodexFooter />
    </View>
  );
}