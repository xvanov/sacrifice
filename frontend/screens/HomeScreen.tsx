import { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  ScrollView,
  Text,
  View,
} from 'react-native';
import { NotificationBell } from '../components/NotificationBell';
import { api } from '../services/api';
import { useAuth } from '../hooks/useAuth';
import { useNavigation } from '../hooks/useNavigation';
import type { Goal } from '../types';

type FilterTab = 'All' | 'Active' | 'Verified' | 'Failed';

const FILTER_TABS: FilterTab[] = ['All', 'Active', 'Verified', 'Failed'];

const TAB_STATUSB: Record<FilterTab, string> = {
  All: '',
  Active: 'active',
  Verified: 'verified',
  Failed: 'failed',
};

function formatAmount(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

function formatDeadline(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function statusColor(status: string): string {
  switch (status) {
    case 'verified':
      return 'bg-green-100 text-green-700';
    case 'failed':
      return 'bg-red-100 text-red-700';
    case 'active':
      return 'bg-blue-100 text-blue-700';
    case 'draft':
      return 'bg-gray-100 text-gray-700';
    default:
      return 'bg-yellow-100 text-yellow-700';
  }
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

function LoadingSkeleton() {
  return (
    <View className="px-4 pt-2" testID="goals-loading">
      {[1, 2, 3].map((i) => (
        <View key={i} className="mb-3 rounded-2xl border border-gray-100 bg-white p-4">
          <View className="mb-2 h-5 w-3/4 rounded bg-gray-200" />
          <View className="mb-3 h-4 w-1/2 rounded bg-gray-100" />
          <View className="flex-row items-center justify-between">
            <View className="h-6 w-16 rounded-full bg-gray-200" />
            <View className="h-5 w-20 rounded bg-gray-100" />
          </View>
        </View>
      ))}
    </View>
  );
}

function EmptyState({ onNavigate }: { onNavigate: () => void }) {
  return (
    <View className="flex-1 items-center justify-center px-6 pt-10">
      <View className="w-full max-w-sm rounded-2xl border border-gray-200 p-6">
        <Text className="mb-2 text-lg font-semibold text-gray-900">No goals yet</Text>
        <Text className="text-sm text-gray-500">
          Create your first goal to get started. Put money on the line and
          stay accountable.
        </Text>
      </View>

      <Pressable
        testID="create-goal-button"
        className="mt-6 w-full max-w-sm items-center rounded-xl bg-indigo-600 py-3.5"
        onPress={onNavigate}
      >
        <Text className="text-base font-semibold text-white">Create Goal</Text>
      </Pressable>
    </View>
  );
}

function GoalCard({
  goal,
  onPress,
}: {
  goal: Goal;
  onPress: () => void;
}) {
  return (
    <Pressable
      className="mb-3 rounded-2xl border border-gray-100 bg-white p-4 active:bg-gray-50"
      onPress={onPress}
    >
      <View className="mb-1 flex-row items-center justify-between">
        <Text className="flex-1 text-base font-semibold text-gray-900" numberOfLines={1}>
          {goal.title}
        </Text>
        <View
          className={`ml-2 rounded-full px-2.5 py-0.5 ${statusBadgeBg(goal.status)}`}
          testID={`status-badge-${goal.status}`}
        >
          <Text className={`text-xs font-medium ${statusBadgeText(goal.status)}`}>
            {statusLabel(goal.status)}
          </Text>
        </View>
      </View>

      <Text className="mb-2 text-sm text-gray-500" numberOfLines={1}>
        {typeLabel(goal.goal_type)}
      </Text>

      <View className="flex-row items-center justify-between">
        <Text className="text-lg font-bold text-gray-900">
          {formatAmount(goal.pledge_amount)}
        </Text>
        <Text className="text-xs text-gray-400">
          {formatDeadline(goal.deadline)}
        </Text>
      </View>
    </Pressable>
  );
}

export default function HomeScreen() {
  const { user, logout } = useAuth();
  const { navigate } = useNavigation();
  const [goals, setGoals] = useState<Goal[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeFilter, setActiveFilter] = useState<FilterTab>('All');

  const fetchGoals = useCallback(async (isRefresh = false) => {
    if (isRefresh) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    setError(null);

    const result = await api.getGoals();

    if (result.data) {
      setGoals(result.data);
    } else {
      setError(result.error || 'Failed to load goals');
    }

    setLoading(false);
    setRefreshing(false);
  }, []);

  useEffect(() => {
    fetchGoals();
  }, [fetchGoals]);

  const handleRefresh = useCallback(() => {
    fetchGoals(true);
  }, [fetchGoals]);

  const handleGoalPress = useCallback((goalId: string) => {
    navigate({ name: 'goal-detail', goalId });
  }, [navigate]);

  const handleCreateGoal = useCallback(() => {
    navigate({ name: 'goal-create' });
  }, [navigate]);

  const handleDashboard = useCallback(() => {
    navigate({ name: 'dashboard' });
  }, [navigate]);

  const filteredGoals = goals.filter((g) => {
    if (activeFilter === 'All') return true;
    return g.status === TAB_STATUSB[activeFilter];
  });

  if (loading) {
    return (
      <View className="flex-1 bg-white">
        <View className="flex-row items-center justify-between px-6 pt-16 pb-4">
          <Text className="text-2xl font-bold text-indigo-600">Sacrifice</Text>
          <View className="flex-row gap-2">
            <NotificationBell />
            <Pressable
              className="rounded-lg bg-gray-100 px-4 py-2"
              onPress={logout}
            >
              <Text className="text-sm font-medium text-gray-700">Logout</Text>
            </Pressable>
          </View>
        </View>
        <LoadingSkeleton />
      </View>
    );
  }

  if (error) {
    return (
      <View className="flex-1 bg-white">
        <View className="flex-row items-center justify-between px-6 pt-16 pb-4">
          <Text className="text-2xl font-bold text-indigo-600">Sacrifice</Text>
          <View className="flex-row gap-2">
            <NotificationBell />
            <Pressable
              className="rounded-lg bg-gray-100 px-4 py-2"
              onPress={logout}
            >
              <Text className="text-sm font-medium text-gray-700">Logout</Text>
            </Pressable>
          </View>
        </View>
        <View className="flex-1 items-center justify-center px-6">
          <Text className="mb-2 text-lg text-red-500">Failed to load goals</Text>
          <Text className="mb-6 text-sm text-gray-500">{error}</Text>
          <Pressable
            className="rounded-xl bg-indigo-600 px-6 py-3"
            onPress={() => fetchGoals()}
          >
            <Text className="text-base font-semibold text-white">Retry</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  return (
    <View className="flex-1 bg-white">
      <View className="flex-row items-center justify-between px-6 pt-16 pb-4">
        <Text className="text-2xl font-bold text-indigo-600">Sacrifice</Text>
        <View className="flex-row gap-2">
          <Pressable
            className="rounded-lg bg-indigo-100 px-4 py-2"
            onPress={handleCreateGoal}
          >
            <Text className="text-sm font-medium text-indigo-700">+ New</Text>
          </Pressable>
          <Pressable
            className="rounded-lg bg-gray-100 px-4 py-2"
            onPress={() => navigate({ name: 'dashboard' })}
          >
            <Text className="text-sm font-medium text-gray-700">Dashboard</Text>
          </Pressable>
          <NotificationBell />
          <Pressable
            className="rounded-lg bg-gray-100 px-4 py-2"
            onPress={logout}
          >
            <Text className="text-sm font-medium text-gray-700">Logout</Text>
          </Pressable>
        </View>
      </View>

      <View className="mb-2 px-4">
        <ScrollView horizontal showsHorizontalScrollIndicator={false} className="flex-row gap-2">
          {FILTER_TABS.map((tab) => (
            <Pressable
              key={tab}
              testID={`filter-tab-${tab}`}
              className={`rounded-full px-4 py-2 ${activeFilter === tab ? 'bg-indigo-600' : 'bg-gray-100'}`}
              onPress={() => setActiveFilter(tab)}
            >
              <Text
                className={`text-sm font-medium ${activeFilter === tab ? 'text-white' : 'text-gray-700'}`}
              >
                {tab}
              </Text>
            </Pressable>
          ))}
        </ScrollView>
      </View>

      {filteredGoals.length === 0 ? (
        <EmptyState onNavigate={handleCreateGoal} />
      ) : (
        <FlatList
          testID="goals-list"
          className="flex-1 px-4"
          data={filteredGoals}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) => (
            <GoalCard goal={item} onPress={() => handleGoalPress(item.id)} />
          )}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={handleRefresh}
              tintColor="#4F46E5"
              colors={['#4F46E5']}
            />
          }
          contentContainerStyle={{ paddingBottom: 24 }}
          showsVerticalScrollIndicator={false}
        />
      )}
    </View>
  );
}
