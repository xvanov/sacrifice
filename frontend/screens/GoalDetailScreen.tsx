import { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, Text, View } from 'react-native';
import { api } from '../services/api';
import { useAuth } from '../hooks/useAuth';
import { useNavigation } from '../hooks/useNavigation';
import type { Goal } from '../types';

interface Props {
  goalId: string;
}

export default function GoalDetailScreen({ goalId }: Props) {
  const { navigate } = useNavigation();
  const { user } = useAuth();
  const [goal, setGoal] = useState<Goal | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      setLoading(true);
      const result = await api.get<Goal>(`/api/goals/${goalId}`);
      if (result.data) {
        setGoal(result.data);
      } else {
        setError(result.error || 'Failed to load goal');
      }
      setLoading(false);
    })();
  }, [goalId]);

  if (loading) {
    return (
      <View className="flex-1 items-center justify-center bg-white">
        <ActivityIndicator size="large" color="#4F46E5" />
      </View>
    );
  }

  if (error || !goal) {
    return (
      <View className="flex-1 items-center justify-center bg-white px-6">
        <Text className="text-lg text-red-500 mb-4">{error || 'Goal not found'}</Text>
        <Pressable
          className="rounded-xl bg-indigo-600 px-6 py-3"
          onPress={() => navigate({ name: 'home' })}
        >
          <Text className="text-base font-semibold text-white">Go Home</Text>
        </Pressable>
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

      <View className="flex-1 px-4">
        <View className="mb-6 rounded-2xl border border-gray-200 p-4">
          <View className="mb-3">
            <Text className="text-xs font-medium uppercase tracking-wide text-gray-400">
              Description
            </Text>
            <Text className="mt-1 text-base text-gray-800">
              {goal.description || 'No description'}
            </Text>
          </View>

          <View className="mb-3 flex-row">
            <View className="flex-1">
              <Text className="text-xs font-medium uppercase tracking-wide text-gray-400">Status</Text>
              <View className={`mt-1 self-start rounded-full px-3 py-1 ${
                goal.status === 'verified' ? 'bg-green-100' :
                goal.status === 'failed' ? 'bg-red-100' :
                goal.status === 'active' ? 'bg-blue-100' :
                goal.status === 'draft' ? 'bg-gray-100' :
                'bg-yellow-100'
              }`}>
                <Text className={`text-xs font-medium ${
                  goal.status === 'verified' ? 'text-green-700' :
                  goal.status === 'failed' ? 'text-red-700' :
                  goal.status === 'active' ? 'text-blue-700' :
                  goal.status === 'draft' ? 'text-gray-700' :
                  'text-yellow-700'
                }`}>
                  {goal.status.replace('_', ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
                </Text>
              </View>
            </View>
            <View className="flex-1">
              <Text className="text-xs font-medium uppercase tracking-wide text-gray-400">Type</Text>
              <Text className="mt-1 text-base text-gray-800">
                {goal.goal_type.replace('_', ' ').replace(/\b\w/g, (c) => c.toUpperCase())}
              </Text>
            </View>
          </View>

          <View className="mb-3">
            <Text className="text-xs font-medium uppercase tracking-wide text-gray-400">Pledge Amount</Text>
            <Text className="mt-1 text-2xl font-bold text-gray-900">
              ${(goal.pledge_amount / 100).toFixed(2)}
            </Text>
          </View>

          <View className="mb-3">
            <Text className="text-xs font-medium uppercase tracking-wide text-gray-400">Deadline</Text>
            <Text className="mt-1 text-base text-gray-800">
              {new Date(goal.deadline).toLocaleString()}
            </Text>
          </View>
        </View>
      </View>
    </View>
  );
}
