import { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Platform, Pressable, ScrollView, Text, View } from 'react-native';
import { CodexHeader } from '../components/CodexHeader';
import { CodexCard } from '../components/CodexCard';
import { CodexButton } from '../components/CodexButton';
import { formatDateTime, formatMoney } from '../utils/format';
import { api } from '../services/api';
import { useNavigation } from '../hooks/useNavigation';
import type { Goal } from '../types';

interface Props {
  goalId: string;
}

interface Position {
  latitude: number;
  longitude: number;
  accuracy_m: number | null;
}

function getCurrentPosition(): Promise<Position> {
  return new Promise((resolve, reject) => {
    if (Platform.OS !== 'web' || typeof navigator === 'undefined' || !navigator.geolocation) {
      reject(new Error('Location is not available in this environment.'));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) =>
        resolve({
          latitude: pos.coords.latitude,
          longitude: pos.coords.longitude,
          accuracy_m: pos.coords.accuracy ?? null,
        }),
      (err) =>
        reject(
          new Error(
            err.code === err.PERMISSION_DENIED
              ? 'Location permission was denied. Allow location access for this site and try again.'
              : 'Could not read your location. Move somewhere with better GPS reception and retry.',
          ),
        ),
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
    );
  });
}

export default function GeolocationSubmissionScreen({ goalId }: Props) {
  const { goBack } = useNavigation();
  const [goal, setGoal] = useState<Goal | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [position, setPosition] = useState<Position | null>(null);
  const [capturing, setCapturing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [verificationStatus, setVerificationStatus] = useState<string | null>(null);
  const [verificationDetails, setVerificationDetails] = useState<Record<string, unknown> | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    api.getGoal(goalId).then((res) => {
      if (res.data) setGoal(res.data);
      else setError(res.error || 'Failed to load goal');
      setLoading(false);
    });
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [goalId]);

  const capture = useCallback(async () => {
    setCapturing(true);
    setError(null);
    try {
      setPosition(await getCurrentPosition());
    } catch (e: any) {
      setError(e?.message || 'Could not read your location.');
    } finally {
      setCapturing(false);
    }
  }, []);

  const startPolling = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const res = await api.getVerificationStatus(goalId);
      if (res.data && res.data.verification_status !== 'pending') {
        setVerificationStatus(res.data.verification_status);
        setVerificationDetails(res.data.verification_details);
        if (pollRef.current) clearInterval(pollRef.current);
      }
    }, 2000);
  }, [goalId]);

  const submit = useCallback(async () => {
    if (!position) return;
    setSubmitting(true);
    setError(null);
    const res = await api.submitGeolocationProof(goalId, {
      latitude: position.latitude,
      longitude: position.longitude,
      accuracy_m: position.accuracy_m ?? undefined,
    });
    setSubmitting(false);
    if (res.error) {
      setError(res.error);
      return;
    }
    setVerificationStatus('pending');
    startPolling();
  }, [goalId, position, startPolling]);

  if (loading) {
    return (
      <View className="flex-1 items-center justify-center bg-codex-bg">
        <ActivityIndicator size="large" color="#8A2A1C" />
      </View>
    );
  }

  if (!goal) {
    // Never spin forever on a load failure (e.g. an expired session) —
    // show what happened and a way out.
    return (
      <View className="flex-1 items-center justify-center bg-codex-bg px-6">
        <Text testID="load-error" className="mb-2 font-serif text-lg text-codex-accent">
          Couldn't load this goal
        </Text>
        <Text className="mb-4 text-center font-sans text-sm text-codex-muted">
          {error || 'Something went wrong.'} If you've been away a while, your session may
          have expired — going back and signing in again fixes it.
        </Text>
        <View className="flex-row gap-2">
          <CodexButton onPress={goBack} variant="secondary">Back</CodexButton>
        </View>
      </View>
    );
  }

  const criteria = (goal.criteria?.criteria_data || {}) as Record<string, unknown>;
  const targetLat = criteria.target_latitude as number | undefined;
  const targetLon = criteria.target_longitude as number | undefined;
  const radius = (criteria.radius_m as number | undefined) ?? 150;
  const isDeadlinePassed = new Date(goal.deadline) < new Date();
  const distance = verificationDetails?.distance_m as number | undefined;

  return (
    <View className="flex-1 bg-codex-bg">
      <CodexHeader />
      <View className="flex-row items-center px-6 pb-2 pt-3">
        <Pressable testID="back-button" onPress={goBack} className="mr-3 p-1">
          <Text className="font-serif text-2xl text-codex-muted">{'←'}</Text>
        </Pressable>
        <Text className="font-serif-italic text-lg text-codex-text">Check in</Text>
      </View>

      <ScrollView className="flex-1 px-6" showsVerticalScrollIndicator={false}>
        <CodexCard className="mb-4 p-4">
          <Text className="font-serif text-lg text-codex-text">{goal.title}</Text>
          <Text className="mt-1 font-sans text-sm text-codex-muted">
            Be within {radius}m of the target before the deadline.
          </Text>
          <Text className="mt-2 font-sans text-xs text-codex-muted">
            Deadline: {formatDateTime(goal.deadline)} · Pledge: {formatMoney(goal.pledge_amount, goal.currency)}
          </Text>
          {targetLat !== undefined && targetLon !== undefined && (
            <Text className="mt-1 font-sans text-xs text-codex-muted">
              Target: {targetLat.toFixed(5)}, {targetLon.toFixed(5)}
            </Text>
          )}
        </CodexCard>

        {isDeadlinePassed ? (
          <View testID="deadline-passed-message" className="mb-4 rounded-sm border border-codex-accent bg-codex-surface p-4">
            <Text className="font-sans text-sm text-codex-accent">
              Deadline has passed — you can no longer check in.
            </Text>
          </View>
        ) : verificationStatus === 'verified' ? (
          <View testID="verification-verified" className="mb-4 rounded-sm border border-codex-border bg-codex-surface p-4">
            <Text className="font-serif text-lg text-codex-text">You made it.</Text>
            <Text className="mt-1 font-sans text-sm text-codex-muted">
              Checked in {distance !== undefined ? `${Math.round(distance)}m` : 'within range'} from the target. Your pledge is safe.
            </Text>
          </View>
        ) : verificationStatus === 'failed' ? (
          <View testID="verification-failed" className="mb-4 rounded-sm border border-codex-accent bg-codex-surface p-4">
            <Text className="font-serif text-lg text-codex-accent">Not close enough.</Text>
            <Text className="mt-1 font-sans text-sm text-codex-muted">
              {(verificationDetails?.failure_reason as string) ||
                'Your location did not match the target.'}
            </Text>
          </View>
        ) : verificationStatus === 'pending' ? (
          <View testID="verification-pending" className="mb-4 items-center py-4">
            <ActivityIndicator size="large" color="#8A2A1C" />
            <Text className="mt-2 font-sans text-sm text-codex-muted">Checking your location…</Text>
          </View>
        ) : (
          <CodexCard className="mb-4 p-4">
            {position ? (
              <View testID="captured-position" className="mb-3">
                <Text className="font-sans text-sm text-codex-text">
                  Your location: {position.latitude.toFixed(5)}, {position.longitude.toFixed(5)}
                </Text>
                {position.accuracy_m != null && (
                  <Text className="mt-0.5 font-sans text-xs text-codex-muted">
                    Accuracy: ±{Math.round(position.accuracy_m)}m
                  </Text>
                )}
              </View>
            ) : (
              <Text className="mb-3 font-sans text-sm text-codex-muted">
                Capture your current location to check in. Your browser will ask for permission.
              </Text>
            )}
            <View className="flex-row gap-2">
              <CodexButton testID="capture-location-button" onPress={capture} disabled={capturing} variant={position ? 'secondary' : 'primary'}>
                {capturing ? 'Locating…' : position ? 'Re-capture' : 'Capture my location'}
              </CodexButton>
              {position && (
                <CodexButton testID="submit-proof-button" onPress={submit} disabled={submitting}>
                  {submitting ? 'Submitting…' : 'Check in'}
                </CodexButton>
              )}
            </View>
          </CodexCard>
        )}

        {error && (
          <Text testID="api-error" className="mb-4 font-sans text-sm text-codex-accent">
            {error}
          </Text>
        )}
      </ScrollView>
    </View>
  );
}
