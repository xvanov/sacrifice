import { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, Text, View } from 'react-native';
import { CodexHeader } from '../components/CodexHeader';
import { CodexCard } from '../components/CodexCard';
import { CodexButton } from '../components/CodexButton';
import { StatusBadge, statusLabel } from '../components/StatusBadge';
import { api } from '../services/api';
import { useNavigation } from '../hooks/useNavigation';
import type { Goal } from '../types';

interface Props {
  goalId: string;
}

function formatAmount(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

function typeLabel(t: string): string {
  switch (t) {
    case 'youtube_video':
      return 'YouTube Video';
    case 'api_endpoint':
      return 'API Endpoint';
    case 'dev_sandbox':
      return 'Dev Sandbox';
    default:
      return t;
  }
}

function recurrenceLabel(r: string | null): string {
  if (!r || r === 'none') return 'None';
  return r.charAt(0).toUpperCase() + r.slice(1);
}

function humanDate(iso: string): string {
  return new Date(iso).toLocaleString();
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <View className="mb-3">
      <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">
        {label}
      </Text>
      <Text className="mt-1 font-sans text-base text-codex-text">{value}</Text>
    </View>
  );
}

function Divider() {
  return <View className="my-3 h-px bg-codex-border" />;
}

function LoadingSkeleton() {
  return (
    <View className="flex-1 bg-codex-bg px-6 pt-3" testID="goal-detail-loading">
      <View className="mb-6 h-7 w-3/4 rounded-sm bg-codex-border" />
      <View className="mb-4 h-20 rounded-sm bg-codex-surface" />
      <View className="mb-3 h-4 w-1/3 rounded-sm bg-codex-border" />
      <View className="mb-3 h-4 w-1/2 rounded-sm bg-codex-surface" />
      <View className="mb-3 h-10 w-full rounded-sm bg-codex-surface" />
    </View>
  );
}

export default function GoalDetailScreen({ goalId }: Props) {
  const { navigate } = useNavigation();
  const [goal, setGoal] = useState<Goal | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchGoal = useCallback(async () => {
    setLoading(true);
    setError(null);
    const result = await api.getGoal(goalId);
    if (result.data) {
      setGoal(result.data);
    } else {
      setError(result.error || 'Failed to load goal');
    }
    setLoading(false);
  }, [goalId]);

  useEffect(() => {
    fetchGoal();
  }, [fetchGoal]);

  if (loading) {
    return (
      <View className="flex-1 bg-codex-bg">
        <CodexHeader />
        <View className="flex-row items-center px-6 pb-2 pt-3">
          <Pressable onPress={() => navigate({ name: 'home' })} className="mr-3 p-1">
            <Text className="font-serif text-2xl text-codex-muted">{'←'}</Text>
          </Pressable>
          <Text className="flex-1 font-serif-italic text-lg text-codex-text" numberOfLines={1}>
            Loading...
          </Text>
        </View>
        <LoadingSkeleton />
      </View>
    );
  }

  if (error || !goal) {
    return (
      <View className="flex-1 bg-codex-bg">
        <CodexHeader />
        <View className="flex-row items-center px-6 pb-2 pt-3">
          <Pressable onPress={() => navigate({ name: 'home' })} className="mr-3 p-1">
            <Text className="font-serif text-2xl text-codex-muted">{'←'}</Text>
          </Pressable>
          <Text className="flex-1 font-serif-italic text-lg text-codex-text">Error</Text>
        </View>
        <View className="flex-1 items-center justify-center px-6">
          <Text className="mb-2 font-sans text-lg text-codex-accent">
            {error || 'Goal not found'}
          </Text>
          <CodexButton onPress={() => navigate({ name: 'home' })}>
            Go Home
          </CodexButton>
        </View>
      </View>
    );
  }

  return (
    <View className="flex-1 bg-codex-bg">
      <CodexHeader />
      <View className="flex-row items-center px-6 pb-2 pt-3">
        <Pressable onPress={() => navigate({ name: 'home' })} className="mr-3 p-1">
          <Text className="font-serif text-2xl text-codex-muted">{'←'}</Text>
        </Pressable>
        <Text className="flex-1 font-serif-italic text-lg text-codex-text" numberOfLines={1}>
          {goal.title}
        </Text>
      </View>

      <ScrollView className="flex-1 px-6" showsVerticalScrollIndicator={false}>
        <CodexCard className="mb-6 p-4">
          <InfoRow label="Description" value={goal.description || 'No description'} />
          <Divider />

          <View className="mb-3 flex-row">
            <View className="flex-1">
              <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">Status</Text>
              <View className="mt-1">
                <StatusBadge status={goal.status} />
              </View>
            </View>
            <View className="flex-1">
              <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">Type</Text>
              <Text className="mt-1 font-sans text-base text-codex-text">
                {typeLabel(goal.goal_type)}
              </Text>
            </View>
          </View>

          <Divider />
          <InfoRow label="Pledge Amount" value={formatAmount(goal.pledge_amount)} />
          <Divider />
          <InfoRow label="Deadline" value={humanDate(goal.deadline)} />
          <Divider />
          <InfoRow label="Timezone" value={goal.timezone} />
          <Divider />
          <InfoRow label="Recurrence" value={recurrenceLabel(goal.recurrence)} />

          {goal.charity_id && (
            <>
              <Divider />
              <InfoRow label="Charity ID" value={goal.charity_id} />
            </>
          )}

          {goal.criteria && (
            <>
              <Divider />
              <Text className="mb-2 font-sans text-xs uppercase tracking-wider text-codex-muted">
                Criteria ({goal.criteria.criteria_type})
              </Text>
              {Object.entries(goal.criteria.criteria_data).map(([key, value]) => (
                <View key={key} className="mb-1.5">
                  <Text className="font-sans text-xs text-codex-muted">
                    {key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
                  </Text>
                  <Text className="font-sans text-sm text-codex-text">
                    {typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)}
                  </Text>
                </View>
              ))}
            </>
          )}

          <Divider />
          <InfoRow label="Created" value={humanDate(goal.created_at)} />
          <InfoRow label="Updated" value={humanDate(goal.updated_at)} />
        </CodexCard>

        {goal.status === 'active' && (
          <CodexButton
            testID="submit-proof-button"
            onPress={() => {
              if (goal.goal_type === 'api_endpoint') {
                navigate({ name: 'api-endpoint-proof-submission', goalId: goal.id });
              } else if (goal.goal_type === 'dev_sandbox' || goal.goal_type === 'github_repo') {
                navigate({ name: 'dev-sandbox-proof-submission', goalId: goal.id });
              } else {
                navigate({ name: 'proof-submission', goalId: goal.id });
              }
            }}
            variant="primary"
            className="mb-6"
          >
            Submit Proof
          </CodexButton>
        )}
      </ScrollView>
    </View>
  );
}
