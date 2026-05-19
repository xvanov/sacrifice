import { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, Text, View } from 'react-native';
import { api } from '../services/api';
import { useNavigation } from '../hooks/useNavigation';
import type { Goal } from '../types';

interface Props {
  goalId: string;
}

function formatAmount(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

function statusBadgeBg(status: string): string {
  switch (status) {
    case 'verified':
      return 'bg-green-100';
    case 'failed':
      return 'bg-red-100';
    case 'active':
      return 'bg-blue-100';
    case 'draft':
      return 'bg-gray-100';
    default:
      return 'bg-yellow-100';
  }
}

function statusBadgeText(status: string): string {
  switch (status) {
    case 'verified':
      return 'text-green-700';
    case 'failed':
      return 'text-red-700';
    case 'active':
      return 'text-blue-700';
    case 'draft':
      return 'text-gray-700';
    default:
      return 'text-yellow-700';
  }
}

function statusLabel(status: string): string {
  return status.replace('_', ' ').replace(/\b\w/g, (c) => c.toUpperCase());
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
      <Text className="text-xs font-medium uppercase tracking-wide text-gray-400">
        {label}
      </Text>
      <Text className="mt-1 text-base text-gray-800">{value}</Text>
    </View>
  );
}

function Divider() {
  return <View className="my-3 h-px bg-gray-100" />;
}

function LoadingSkeleton() {
  return (
    <View className="flex-1 bg-white px-4 pt-6" testID="goal-detail-loading">
      <View className="mb-6 h-7 w-3/4 rounded bg-gray-200" />
      <View className="mb-4 h-20 rounded-2xl bg-gray-100" />
      <View className="mb-3 h-4 w-1/3 rounded bg-gray-200" />
      <View className="mb-3 h-4 w-1/2 rounded bg-gray-100" />
      <View className="mb-3 h-10 w-full rounded-2xl bg-gray-100" />
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
      <View className="flex-1 bg-white">
        <View className="flex-row items-center px-4 pt-14 pb-2">
          <Pressable onPress={() => navigate({ name: 'home' })} className="mr-3 p-1">
            <Text className="text-2xl text-gray-600">{'<'}</Text>
          </Pressable>
          <Text className="text-xl font-bold text-gray-900 flex-1" numberOfLines={1}>
            Loading...
          </Text>
        </View>
        <LoadingSkeleton />
      </View>
    );
  }

  if (error || !goal) {
    return (
      <View className="flex-1 bg-white">
        <View className="flex-row items-center px-4 pt-14 pb-2">
          <Pressable onPress={() => navigate({ name: 'home' })} className="mr-3 p-1">
            <Text className="text-2xl text-gray-600">{'<'}</Text>
          </Pressable>
          <Text className="text-xl font-bold text-gray-900 flex-1">Error</Text>
        </View>
        <View className="flex-1 items-center justify-center px-6">
          <Text className="mb-2 text-lg text-red-500">
            {error || 'Goal not found'}
          </Text>
          <Pressable
            className="rounded-xl bg-indigo-600 px-6 py-3"
            onPress={() => navigate({ name: 'home' })}
          >
            <Text className="text-base font-semibold text-white">Go Home</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  return (
    <View className="flex-1 bg-white">
      <View className="flex-row items-center px-4 pt-14 pb-2">
        <Pressable onPress={() => navigate({ name: 'home' })} className="mr-3 p-1">
          <Text className="text-2xl text-gray-600">{'<'}</Text>
        </Pressable>
        <Text className="text-xl font-bold text-gray-900 flex-1" numberOfLines={1}>
          {goal.title}
        </Text>
      </View>

      <ScrollView className="flex-1 px-4" showsVerticalScrollIndicator={false}>
        <View className="mb-6 rounded-2xl border border-gray-200 p-4">
          <InfoRow label="Description" value={goal.description || 'No description'} />

          <Divider />

          <View className="mb-3 flex-row">
            <View className="flex-1">
              <Text className="text-xs font-medium uppercase tracking-wide text-gray-400">Status</Text>
              <View className={`mt-1 self-start rounded-full px-3 py-1 ${statusBadgeBg(goal.status)}`}>
                <Text className={`text-xs font-medium ${statusBadgeText(goal.status)}`}>
                  {statusLabel(goal.status)}
                </Text>
              </View>
            </View>
            <View className="flex-1">
              <Text className="text-xs font-medium uppercase tracking-wide text-gray-400">Type</Text>
              <Text className="mt-1 text-base text-gray-800">
                {typeLabel(goal.goal_type)}
              </Text>
            </View>
          </View>

          <Divider />

          <InfoRow label="Pledge Amount" value={formatAmount(goal.pledge_amount)} />

          <Divider />

          <InfoRow
            label="Deadline"
            value={humanDate(goal.deadline)}
          />

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
              <Text className="mb-2 text-xs font-medium uppercase tracking-wide text-gray-400">
                Criteria ({goal.criteria.criteria_type})
              </Text>
              {Object.entries(goal.criteria.criteria_data).map(([key, value]) => (
                <View key={key} className="mb-1.5">
                  <Text className="text-xs text-gray-500">
                    {key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
                  </Text>
                  <Text className="text-sm text-gray-800">
                    {typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)}
                  </Text>
                </View>
              ))}
            </>
          )}

          <Divider />

          <InfoRow label="Created" value={humanDate(goal.created_at)} />
          <InfoRow label="Updated" value={humanDate(goal.updated_at)} />
        </View>

        {goal.status === 'active' && (
          <Pressable
            testID="submit-proof-button"
            className="mb-6 rounded-xl bg-indigo-600 px-6 py-4"
            onPress={() => {
              if (goal.goal_type === 'api_endpoint') {
                navigate({ name: 'api-endpoint-proof-submission', goalId: goal.id });
              } else {
                navigate({ name: 'proof-submission', goalId: goal.id });
              }
            }}
          >
            <Text className="text-center text-base font-semibold text-white">
              Submit Proof
            </Text>
          </Pressable>
        )}
      </ScrollView>
    </View>
  );
}
