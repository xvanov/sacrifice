import { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { api } from '../services/api';
import { useNavigation } from '../hooks/useNavigation';
import type { Goal } from '../types';

const YOUTUBE_REGEX = /^(https?:\/\/)?(www\.)?(youtube\.com\/watch\?v=|youtu\.be\/)[\w-]{11}/;

interface Props {
  goalId: string;
}

function humanDate(iso: string): string {
  return new Date(iso).toLocaleString();
}

export default function ProofSubmissionScreen({ goalId }: Props) {
  const { goBack } = useNavigation();
  const [goal, setGoal] = useState<Goal | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [youtubeUrl, setYoutubeUrl] = useState('');
  const [urlError, setUrlError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [verificationStatus, setVerificationStatus] = useState<string | null>(null);
  const [verificationDetails, setVerificationDetails] = useState<Record<string, unknown> | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [goalLoaded, setGoalLoaded] = useState(false);

  const fetchGoal = useCallback(async () => {
    setLoading(true);
    setError(null);
    const result = await api.getGoal(goalId);
    if (result.data) {
      setGoal(result.data);
      setGoalLoaded(true);
    } else {
      setError(result.error || 'Failed to load goal');
    }
    setLoading(false);
  }, [goalId]);

  useEffect(() => {
    fetchGoal();
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, [fetchGoal]);

  const isDeadlinePassed = goal ? new Date(goal.deadline) < new Date() : false;

  const validateUrl = (url: string): string | null => {
    if (!url.trim()) return 'YouTube URL is required';
    if (!YOUTUBE_REGEX.test(url.trim())) return 'Must be a valid YouTube URL (youtube.com or youtu.be)';
    return null;
  };

  const handleUrlChange = (text: string) => {
    setYoutubeUrl(text);
    if (urlError && !validateUrl(text)) {
      setUrlError(null);
    }
  };

  const startPolling = useCallback((submissionId: string) => {
    pollingRef.current = setInterval(async () => {
      const result = await api.getVerificationStatus(goalId);
      if (result.data) {
        setVerificationStatus(result.data.verification_status);
        setVerificationDetails(result.data.verification_details);
        if (result.data.verification_status === 'verified' || result.data.verification_status === 'failed') {
          if (pollingRef.current) {
            clearInterval(pollingRef.current);
            pollingRef.current = null;
          }
        }
      }
    }, 3000);
  }, [goalId]);

  const handleSubmit = async () => {
    const validationError = validateUrl(youtubeUrl);
    if (validationError) {
      setUrlError(validationError);
      return;
    }
    setUrlError(null);
    setApiError(null);
    setSubmitting(true);
    setVerificationStatus('pending');

    const result = await api.submitProof(goalId, { youtube_url: youtubeUrl.trim() });
    if (result.error) {
      setApiError(result.error);
      setSubmitting(false);
      setVerificationStatus(null);
      return;
    }

    setSubmitting(false);
    setVerificationStatus('pending');
    startPolling(result.data?.submission_id || '');
  };

  if (loading) {
    return (
      <View className="flex-1 bg-white">
        <View className="flex-row items-center px-4 pt-14 pb-2">
          <Pressable onPress={goBack} className="mr-3 p-1">
            <Text className="text-2xl text-gray-600">{'<'}</Text>
          </Pressable>
          <Text className="text-xl font-bold text-gray-900">Submit Proof</Text>
        </View>
        <View className="flex-1 items-center justify-center">
          <ActivityIndicator size="large" color="#4F46E5" />
        </View>
      </View>
    );
  }

  if (error || !goal) {
    return (
      <View className="flex-1 bg-white">
        <View className="flex-row items-center px-4 pt-14 pb-2">
          <Pressable onPress={goBack} className="mr-3 p-1">
            <Text className="text-2xl text-gray-600">{'<'}</Text>
          </Pressable>
          <Text className="text-xl font-bold text-gray-900">Error</Text>
        </View>
        <View className="flex-1 items-center justify-center px-6">
          <Text className="mb-2 text-lg text-red-500">{error || 'Goal not found'}</Text>
          <Pressable
            testID="retry-button"
            className="rounded-xl bg-indigo-600 px-6 py-3"
            onPress={fetchGoal}
          >
            <Text className="text-base font-semibold text-white">Retry</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  const criteriaData = goal.criteria?.criteria_data || {};
  const minDuration = criteriaData.min_duration_seconds as number | undefined;
  const videoDescription = criteriaData.video_description as string | undefined;

  const durationPassed = verificationDetails?.duration_passed as boolean | undefined;
  const durationSeconds = verificationDetails?.duration_seconds as number | undefined;
  const minDurationSeconds = verificationDetails?.min_duration_seconds as number | undefined;
  const llmJudgmentPassed = verificationDetails?.llm_judgment_passed as boolean | undefined;
  const llmReasoning = verificationDetails?.llm_reasoning as string | undefined;
  const failureReason = verificationDetails?.failure_reason as string | undefined;

  return (
    <View className="flex-1 bg-white">
      <View className="flex-row items-center px-4 pt-14 pb-2">
        <Pressable onPress={goBack} className="mr-3 p-1">
          <Text className="text-2xl text-gray-600">{'<'}</Text>
        </Pressable>
        <Text className="text-xl font-bold text-gray-900">Submit Proof</Text>
      </View>

      <ScrollView className="flex-1 px-4" showsVerticalScrollIndicator={false}>
        <View className="mb-4 rounded-2xl border border-gray-200 p-4">
          <Text className="text-lg font-bold text-gray-900">{goal.title}</Text>
          <Text className="mt-1 text-sm text-gray-600">{goal.description || 'No description'}</Text>
          <Text className="mt-2 text-xs text-gray-400">
            Deadline: {humanDate(goal.deadline)}
          </Text>
          {minDuration && (
            <Text className="mt-1 text-xs text-gray-400">
              Min duration: {minDuration}s
            </Text>
          )}
          {videoDescription && (
            <Text className="mt-1 text-xs text-gray-400" numberOfLines={2}>
              Expected content: {videoDescription}
            </Text>
          )}
        </View>

        {isDeadlinePassed ? (
          <View testID="deadline-passed-message" className="mb-4 rounded-xl bg-red-50 p-4">
            <Text className="text-sm font-semibold text-red-700">
              Deadline has passed — you can no longer submit proof.
            </Text>
          </View>
        ) : verificationStatus === 'verified' || verificationStatus === 'failed' ? null : (
          <View className="mb-4">
            <Text className="mb-1 text-sm font-medium text-gray-700">YouTube Video URL</Text>
            <TextInput
              testID="youtube-url-input"
              className="rounded-xl border border-gray-300 px-4 py-3 text-base text-gray-900"
              placeholder="https://www.youtube.com/watch?v=..."
              value={youtubeUrl}
              onChangeText={handleUrlChange}
              editable={!submitting && verificationStatus !== 'pending'}
              autoCapitalize="none"
              autoCorrect={false}
            />
            {urlError && (
              <Text testID="url-validation-error" className="mt-1 text-sm text-red-500">
                {urlError}
              </Text>
            )}
            {submitting ? (
              <View testID="submission-loading" className="mt-4 items-center py-4">
                <ActivityIndicator size="large" color="#4F46E5" />
                <Text className="mt-2 text-sm text-gray-500">Submitting proof...</Text>
              </View>
            ) : verificationStatus === 'pending' ? (
              <View testID="verification-pending" className="mt-4 items-center py-4">
                <ActivityIndicator size="large" color="#4F46E5" />
                <Text className="mt-2 text-sm text-gray-500">
                  Verifying your video... checking duration and content
                </Text>
              </View>
            ) : (
              <Pressable
                testID="submit-proof-button"
                className="mt-4 rounded-xl bg-indigo-600 px-6 py-4"
                onPress={handleSubmit}
              >
                <Text className="text-center text-base font-semibold text-white">
                  Submit Proof
                </Text>
              </Pressable>
            )}
            {apiError && (
              <Text testID="api-error" className="mt-2 text-sm text-red-500">
                {apiError}
              </Text>
            )}
          </View>
        )}

        {verificationStatus === 'verified' && (
          <View testID="verification-verified" className="mb-4 rounded-xl bg-green-50 p-4">
            <View testID="verification-icon-passed" className="mb-2 items-center">
              <Text className="text-4xl">✅</Text>
            </View>
            <Text className="mb-3 text-center text-lg font-bold text-green-700">
              Verified!
            </Text>
            <View testID="duration-result" className="mb-2 flex-row items-center">
              <Text className="mr-2 text-lg">
                {durationPassed ? '✅' : '❌'}
              </Text>
              <Text className="text-sm text-gray-700">
                Duration: {durationPassed ? 'Passed' : 'Failed'}
                {durationSeconds != null && minDurationSeconds != null && ` (${durationSeconds}s / ${minDurationSeconds}s)`}
              </Text>
            </View>
            <View testID="llm-result" className="mb-2 flex-row items-center">
              <Text className="mr-2 text-lg">
                {llmJudgmentPassed ? '✅' : '❌'}
              </Text>
              <Text className="text-sm text-gray-700">
                Content: {llmJudgmentPassed ? 'Passed' : 'Failed'}
              </Text>
            </View>
            {llmReasoning && (
              <Text className="mt-2 text-xs italic text-gray-500">
                {llmReasoning}
              </Text>
            )}
          </View>
        )}

        {verificationStatus === 'failed' && (
          <View testID="verification-failed" className="mb-4 rounded-xl bg-red-50 p-4">
            <View testID="verification-icon-failed" className="mb-2 items-center">
              <Text className="text-4xl">❌</Text>
            </View>
            <Text className="mb-3 text-center text-lg font-bold text-red-700">
              Verification Failed
            </Text>
            <View testID="duration-result" className="mb-2 flex-row items-center">
              <Text className="mr-2 text-lg">
                {durationPassed ? '✅' : '❌'}
              </Text>
              <Text className="text-sm text-gray-700">
                Duration: {durationPassed ? 'Passed' : 'Failed'}
                {durationSeconds != null && minDurationSeconds != null && (
                  <Text>{` (${durationSeconds}s / ${minDurationSeconds}s)`}</Text>
                )}
              </Text>
            </View>
            <View testID="llm-result" className="mb-2 flex-row items-center">
              <Text className="mr-2 text-lg">
                {llmJudgmentPassed ? '✅' : '❌'}
              </Text>
              <Text className="text-sm text-gray-700">
                Content: {llmJudgmentPassed ? 'Passed' : 'Failed'}
              </Text>
            </View>
            {failureReason && (
              <Text className="mt-2 text-xs text-red-600">
                {failureReason}
              </Text>
            )}
            {llmReasoning && (
              <Text className="mt-2 text-xs italic text-gray-500">
                {llmReasoning}
              </Text>
            )}
          </View>
        )}
      </ScrollView>
    </View>
  );
}
