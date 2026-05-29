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
import { CodexHeader } from '../components/CodexHeader';
import { StatusBadge, statusLabel } from '../components/StatusBadge';
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

function typeLabel(t: string): string {
  switch (t) {
    case 'youtube_video':
      return 'YouTube';
    case 'api_endpoint':
      return 'API';
    case 'dev_sandbox':
      return 'Sandbox';
    default:
      return t;
  }
}

function LoadingSkeleton() {
  return (
    <View className="px-4 pt-2" testID="goals-loading">
      {[1, 2, 3].map((i) => (
        <View key={i} className="mb-3 rounded-sm border border-codex-border bg-codex-surface p-4">
          <View className="mb-2 h-5 w-3/4 rounded-sm bg-codex-border" />
          <View className="mb-3 h-4 w-1/2 rounded-sm bg-codex-bg" />
          <View className="flex-row items-center justify-between">
            <View className="h-6 w-16 rounded-sm bg-codex-border" />
            <View className="h-5 w-20 rounded-sm bg-codex-bg" />
          </View>
        </View>
      ))}
    </View>
  );
}

function EmptyState({ onNavigate }: { onNavigate: () => void }) {
  return (
    <View className="flex-1 items-center justify-center px-6 pt-10">
      <View className="w-full max-w-sm rounded-sm border border-codex-border bg-codex-surface p-6">
        <Text className="font-serif text-lg text-codex-text">No goals yet</Text>
        <Text className="mt-2 font-sans text-sm leading-relaxed text-codex-muted">
          Create your first goal to get started. Put money on the line and stay accountable.
        </Text>
      </View>

      <Pressable
        testID="create-goal-button"
        className="mt-6 w-full max-w-sm items-center rounded-sm bg-codex-accent py-3.5"
        onPress={onNavigate}
      >
        <Text className="font-sans-medium text-base text-codex-surface">Create Goal</Text>
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
      className="mb-3 rounded-sm border border-codex-border bg-codex-surface p-4 active:bg-codex-bg"
      onPress={onPress}
    >
      <View className="mb-1 flex-row items-center justify-between">
        <Text className="flex-1 font-serif text-base text-codex-text" numberOfLines={1}>
          {goal.title}
        </Text>
        <StatusBadge status={goal.status} testID={`status-badge-${goal.status}`} />
      </View>

      <Text className="mb-2 font-sans text-sm text-codex-muted" numberOfLines={1}>
        {typeLabel(goal.goal_type)}
      </Text>

      <View className="flex-row items-center justify-between">
        <Text className="font-sans-bold text-lg text-codex-text">
          {formatAmount(goal.pledge_amount)}
        </Text>
        <Text className="font-sans text-xs text-codex-muted">
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
    navigate({ name: 'goal-create-chat' });
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
      <View className="flex-1 bg-codex-bg">
        <CodexHeader />
        <View className="flex-row items-center justify-between px-3 pb-3">
          <ScrollView horizontal showsHorizontalScrollIndicator={false} className="flex-row gap-1.5">
            <Pressable className="rounded-sm border border-codex-border bg-codex-surface px-2.5 py-1.5" onPress={handleCreateGoal}>
              <Text className="font-sans text-xs uppercase tracking-wider text-codex-accent">+ New</Text>
            </Pressable>
            <Pressable className="rounded-sm border border-codex-border bg-codex-surface px-2.5 py-1.5" onPress={handleDashboard}>
              <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">Ledger</Text>
            </Pressable>
            {FILTER_TABS.map((tab) => (
              <Pressable key={tab} className="rounded-sm border border-codex-border bg-codex-surface px-2.5 py-1.5" onPress={() => {}}>
                <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">{tab}</Text>
              </Pressable>
            ))}
          </ScrollView>
          <Pressable className="ml-2 rounded-sm border border-codex-border bg-codex-surface px-2.5 py-1.5" onPress={logout}>
            <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">Exit</Text>
          </Pressable>
        </View>
        <LoadingSkeleton />
      </View>
    );
  }

  if (error) {
    return (
      <View className="flex-1 bg-codex-bg">
        <CodexHeader />
        <View className="flex-row items-center justify-between px-3 pb-3">
          <ScrollView horizontal showsHorizontalScrollIndicator={false} className="flex-row gap-1.5">
            <Pressable className="rounded-sm border border-codex-border bg-codex-surface px-2.5 py-1.5" onPress={handleCreateGoal}>
              <Text className="font-sans text-xs uppercase tracking-wider text-codex-accent">+ New</Text>
            </Pressable>
            <Pressable className="rounded-sm border border-codex-border bg-codex-surface px-2.5 py-1.5" onPress={handleDashboard}>
              <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">Ledger</Text>
            </Pressable>
            {FILTER_TABS.map((tab) => (
              <Pressable key={tab} className="rounded-sm border border-codex-border bg-codex-surface px-2.5 py-1.5" onPress={() => {}}>
                <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">{tab}</Text>
              </Pressable>
            ))}
          </ScrollView>
          <Pressable className="ml-2 rounded-sm border border-codex-border bg-codex-surface px-2.5 py-1.5" onPress={logout}>
            <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">Exit</Text>
          </Pressable>
        </View>
        <View className="flex-1 items-center justify-center px-6">
          <Text className="mb-2 font-sans text-lg text-codex-accent">Failed to load goals</Text>
          <Text className="mb-6 font-sans text-sm text-codex-muted">{error}</Text>
          <Pressable
            className="rounded-sm bg-codex-accent px-6 py-3"
            onPress={() => fetchGoals()}
          >
            <Text className="font-sans-medium text-base text-codex-surface">Retry</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  return (
    <View className="flex-1 bg-codex-bg">
      <CodexHeader />
      <View className="flex-row items-center justify-between px-3 pb-3">
        <ScrollView horizontal showsHorizontalScrollIndicator={false} className="flex-row gap-1.5">
          <Pressable
            className="rounded-sm border border-codex-border bg-codex-surface px-2.5 py-1.5"
            onPress={handleCreateGoal}
          >
            <Text className="font-sans text-xs uppercase tracking-wider text-codex-accent">+ New</Text>
          </Pressable>
          <Pressable
            className="rounded-sm border border-codex-border bg-codex-surface px-2.5 py-1.5"
            onPress={() => navigate({ name: 'dashboard' })}
          >
            <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">Ledger</Text>
          </Pressable>
          {FILTER_TABS.map((tab) => (
            <Pressable
              key={tab}
              testID={`filter-tab-${tab}`}
              className={`rounded-sm px-2.5 py-1.5 ${activeFilter === tab ? 'bg-codex-accent' : 'border border-codex-border bg-codex-surface'}`}
              onPress={() => setActiveFilter(tab)}
            >
              <Text
                className={`font-sans text-xs uppercase tracking-wider ${activeFilter === tab ? 'text-codex-surface' : 'text-codex-muted'}`}
              >
                {tab}
              </Text>
            </Pressable>
          ))}
        </ScrollView>
        <Pressable className="ml-2 rounded-sm border border-codex-border bg-codex-surface px-2.5 py-1.5" onPress={logout}>
          <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">Exit</Text>
        </Pressable>
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
              tintColor="#8A2A1C"
              colors={['#8A2A1C']}
            />
          }
          contentContainerStyle={{ paddingBottom: 24 }}
          showsVerticalScrollIndicator={false}
        />
      )}
    </View>
  );
}
