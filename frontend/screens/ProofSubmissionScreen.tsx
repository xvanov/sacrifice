import { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { CodexHeader } from '../components/CodexHeader';
import { CodexCard } from '../components/CodexCard';
import { CodexButton } from '../components/CodexButton';
import { CodexInput } from '../components/CodexInput';
import { CodexFooter } from '../components/CodexFooter';
import { SectionHeading } from '../components/SectionHeading';
import { formatDateTime, formatMoney } from '../utils/format';
import { api } from '../services/api';
import { useNavigation } from '../hooks/useNavigation';
import type { Goal } from '../types';

const YOUTUBE_REGEX = /^(https?:\/\/)?(www\.)?(youtube\.com\/watch\?v=|youtu\.be\/)[\w-]{11}/;

interface Props {
  goalId: string;
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
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
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
      <View className="flex-1 bg-codex-bg">
        <CodexHeader pageNumber="II" totalPages="IV" />
        <View className="flex-row items-center px-6 pb-2 pt-3">
          <Pressable onPress={goBack} className="mr-3 p-1">
            <Text className="font-serif text-2xl text-codex-muted">{'←'}</Text>
          </Pressable>
          <Text className="font-serif-italic text-lg text-codex-text">The Witness</Text>
        </View>
        <View className="flex-1 items-center justify-center">
          <ActivityIndicator size="large" color="#8A2A1C" />
        </View>
      </View>
    );
  }

  if (error || !goal) {
    return (
      <View className="flex-1 bg-codex-bg">
        <CodexHeader pageNumber="II" totalPages="IV" />
        <View className="flex-row items-center px-6 pb-2 pt-3">
          <Pressable onPress={goBack} className="mr-3 p-1">
            <Text className="font-serif text-2xl text-codex-muted">{'←'}</Text>
          </Pressable>
          <Text className="font-serif-italic text-lg text-codex-text">The Witness</Text>
        </View>
        <View className="flex-1 items-center justify-center px-6">
          <Text className="mb-2 font-sans text-lg text-codex-accent">{error || 'Goal not found'}</Text>
          <CodexButton onPress={fetchGoal}>
            Retry
          </CodexButton>
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
    <View className="flex-1 bg-codex-bg">
      <CodexHeader pageNumber="II" totalPages="IV" />
      <View className="flex-row items-center px-6 pb-2 pt-3">
        <Pressable onPress={goBack} className="mr-3 p-1">
          <Text className="font-serif text-2xl text-codex-muted">{'←'}</Text>
        </Pressable>
        <Text className="font-serif-italic text-lg text-codex-text">The Witness</Text>
      </View>

      <ScrollView className="flex-1 px-6" showsVerticalScrollIndicator={false}>
        <View className="mb-2 flex-row items-center gap-2">
          <Text className="rounded-sm bg-codex-accent px-2 py-0.5 font-sans text-[10px] uppercase tracking-wider text-codex-surface">
            Submit
          </Text>
          <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">
            Of IV · II
          </Text>
        </View>

        <SectionHeading
          number="The Witness — proof submitted"
          title=""
          subtitle="Show the work, and let it be judged. The article will be examined by tests and by the eye of a reviewer, to judge whether the work is true."
        />

        <CodexCard className="mb-4 p-4">
          <Text className="font-serif text-lg text-codex-text">{goal.title}</Text>
          <Text className="mt-1 font-sans text-sm text-codex-muted">{goal.description || 'No description'}</Text>
          <Text className="mt-2 font-sans text-xs text-codex-muted">
            Deadline: {formatDateTime(goal.deadline)}
          </Text>
          {minDuration && (
            <Text className="mt-1 font-sans text-xs text-codex-muted">
              Min duration: {minDuration}s
            </Text>
          )}
          {videoDescription && (
            <Text className="mt-1 font-sans text-xs text-codex-muted" numberOfLines={2}>
              Expected content: {videoDescription}
            </Text>
          )}
        </CodexCard>

        {isDeadlinePassed ? (
          <View testID="deadline-passed-message" className="mb-4 rounded-sm border border-codex-accent bg-codex-surface p-4">
            <Text className="font-sans text-sm text-codex-accent">
              Deadline has passed — you can no longer submit proof.
            </Text>
          </View>
        ) : verificationStatus === 'verified' || verificationStatus === 'failed' ? null : (
          <View className="mb-4">
            <CodexInput
              testID="youtube-url-input"
              label="YouTube URL — the record"
              value={youtubeUrl}
              onChangeText={handleUrlChange}
              placeholder="https://www.youtube.com/watch?v=..."
              editable={!submitting && verificationStatus !== 'pending'}
              error={urlError}
            />

            {submitting ? (
              <View testID="submission-loading" className="items-center py-4">
                <ActivityIndicator size="large" color="#8A2A1C" />
                <Text className="mt-2 font-sans text-sm text-codex-muted">Submitting proof...</Text>
              </View>
            ) : verificationStatus === 'pending' ? (
              <View testID="verification-pending" className="items-center py-4">
                <ActivityIndicator size="large" color="#8A2A1C" />
                <Text className="mt-2 font-sans text-sm text-codex-muted">
                  Verifying your video... checking duration and content
                </Text>
              </View>
            ) : (
              <CodexButton
                testID="submit-proof-button"
                onPress={handleSubmit}
                variant="primary"
                className="w-full"
              >
                Submit for Judgement ↳
              </CodexButton>
            )}
            {apiError && (
              <Text testID="api-error" className="mt-2 font-sans text-sm text-codex-accent">
                {apiError}
              </Text>
            )}
          </View>
        )}

        {verificationStatus === 'verified' && (
          <View testID="verification-verified" className="mb-4 rounded-sm border border-codex-border bg-codex-surface p-4">
            <View className="mb-3 items-center">
              <Text className="font-serif text-2xl text-codex-text">Verdict: True</Text>
              <Text className="mt-1 font-sans text-sm text-codex-muted">The work is verified</Text>
            </View>
            <View className="mb-2 flex-row items-center">
              <Text className="mr-2 font-sans text-sm text-codex-text">
                ✓ Duration: Passed
                {durationSeconds != null && minDurationSeconds != null && ` (${durationSeconds}s / ${minDurationSeconds}s)`}
              </Text>
            </View>
            <View className="mb-2 flex-row items-center">
              <Text className="mr-2 font-sans text-sm text-codex-text">
                ✓ Content: Passed
              </Text>
            </View>
            {llmReasoning && (
              <Text className="mt-2 font-serif-italic text-xs text-codex-muted">
                "{llmReasoning}"
              </Text>
            )}
          </View>
        )}

        {verificationStatus === 'failed' && (
          <View testID="verification-failed" className="mb-4 rounded-sm border border-codex-accent bg-codex-surface p-4">
            <View className="mb-3 items-center">
              <Text className="font-serif text-2xl text-codex-accent">Verdict: False</Text>
              <Text className="mt-1 font-sans text-sm text-codex-muted">Verification failed</Text>
            </View>
            <View className="mb-2 flex-row items-center">
              <Text className="mr-2 font-sans text-sm text-codex-text">
                {durationPassed ? '✓' : '✗'} Duration: {durationPassed ? 'Passed' : 'Failed'}
                {durationSeconds != null && minDurationSeconds != null && ` (${durationSeconds}s / ${minDurationSeconds}s)`}
              </Text>
            </View>
            <View className="mb-2 flex-row items-center">
              <Text className="mr-2 font-sans text-sm text-codex-text">
                {llmJudgmentPassed ? '✓' : '✗'} Content: {llmJudgmentPassed ? 'Passed' : 'Failed'}
              </Text>
            </View>
            {failureReason && (
              <Text className="mt-2 font-sans text-xs text-codex-accent">{failureReason}</Text>
            )}
            {llmReasoning && (
              <Text className="mt-2 font-serif-italic text-xs text-codex-muted">
                "{llmReasoning}"
              </Text>
            )}
          </View>
        )}

        <CodexCard className="mb-4 p-4">
          <Text className="font-serif text-base text-codex-text">
            {goal.title} · {formatMoney(goal.pledge_amount, goal.currency)}
          </Text>
          <Text className="mt-1 font-sans text-xs text-codex-muted">
            {goal.charity_id ? 'Pledge goes to your chosen recipient if this fails.' : 'If this fails, your pledge is charged.'}
          </Text>
        </CodexCard>
      </ScrollView>

      <CodexFooter />
    </View>
  );
}
