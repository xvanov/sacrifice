import { useCallback, useEffect, useState } from 'react';
import {
  FlatList,
  Pressable,
  RefreshControl,
  ScrollView,
  Text,
  View,
} from 'react-native';
import { CodexHeader } from '../components/CodexHeader';
import { StatusBadge, typeLabelShort } from '../components/StatusBadge';
import { formatDateTime, formatMoney } from '../utils/format';
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

function NavChip({
  label,
  onPress,
  active,
  accent,
  testID,
}: {
  label: string;
  onPress: () => void;
  active?: boolean;
  accent?: boolean;
  testID?: string;
}) {
  const base = 'rounded-full px-3.5 py-2';
  const cls = active
    ? 'bg-codex-accent'
    : accent
      ? 'border border-codex-accent bg-codex-surface'
      : 'border border-codex-border bg-codex-surface';
  const textCls = active
    ? 'text-codex-surface'
    : accent
      ? 'text-codex-accent'
      : 'text-codex-text-secondary';
  return (
    <Pressable testID={testID} className={`${base} ${cls}`} onPress={onPress}>
      <Text className={`font-sans-medium text-sm leading-none tracking-wide ${textCls}`}>{label}</Text>
    </Pressable>
  );
}

function HomeNav({
  activeFilter,
  onFilter,
  onCreate,
  onDashboard,
  onPayments,
  onLogout,
}: {
  activeFilter: FilterTab;
  onFilter: (t: FilterTab) => void;
  onCreate: () => void;
  onDashboard: () => void;
  onPayments: () => void;
  onLogout: () => void;
}) {
  // Rendered INSIDE the CodexHeader brand row, so the logo, actions, filters
  // and sign-out all share one line at the very top of the app.
  return (
    <>
      <NavChip testID="home-create-goal-shortcut" label="+ New goal" accent onPress={onCreate} />
      <NavChip label="Ledger" onPress={onDashboard} />
      <NavChip testID="home-payment-methods" label="Payments" onPress={onPayments} />
      <View className="mx-1.5 h-5 w-px bg-codex-border" />
      {FILTER_TABS.map((tab) => (
        <NavChip
          key={tab}
          testID={`filter-tab-${tab}`}
          label={tab}
          active={activeFilter === tab}
          onPress={() => onFilter(tab)}
        />
      ))}
      <View className="flex-1" />
      <NavChip label="Sign out" onPress={onLogout} />
    </>
  );
}

function LoadingSkeleton() {
  return (
    <View className="px-4 pt-1" testID="goals-loading">
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

function EmptyState({ filter, onCreate }: { filter: FilterTab; onCreate: () => void }) {
  const filtered = filter !== 'All';
  return (
    <View className="flex-1 items-center justify-center px-6 pb-16">
      <View className="w-full max-w-sm items-center rounded-sm border border-codex-border bg-codex-surface p-6">
        <Text className="font-serif text-2xl text-codex-text">
          {filtered ? `No ${filter.toLowerCase()} goals` : 'No goals yet'}
        </Text>
        <Text className="mt-2 text-center font-sans text-sm leading-relaxed text-codex-muted">
          {filtered
            ? 'Nothing here under this filter. Switch to All to see everything.'
            : 'Put money on the line and stay accountable. Create your first goal to begin.'}
        </Text>
        {!filtered && (
          <Pressable
            testID="create-goal-button"
            className="mt-5 w-full items-center rounded-sm bg-codex-accent py-3.5"
            onPress={onCreate}
          >
            <Text className="font-sans-medium text-base text-codex-surface">Create a goal</Text>
          </Pressable>
        )}
      </View>
    </View>
  );
}

function GoalCard({ goal, onPress }: { goal: Goal; onPress: () => void }) {
  return (
    <Pressable
      className="mb-3 rounded-sm border border-codex-border bg-codex-surface p-4 active:bg-codex-bg"
      onPress={onPress}
    >
      <View className="mb-2 flex-row items-start justify-between gap-2">
        <Text className="flex-1 font-serif text-lg leading-tight text-codex-text" numberOfLines={2}>
          {goal.title}
        </Text>
        <StatusBadge status={goal.status} testID={`status-badge-${goal.status}`} />
      </View>

      <View className="flex-row items-center justify-between">
        <View>
          <Text className="font-sans-bold text-lg text-codex-text">
            {formatMoney(goal.pledge_amount, goal.currency)}
          </Text>
          <Text className="mt-0.5 font-sans text-xs uppercase tracking-wider text-codex-muted">
            {typeLabelShort(goal.goal_type)}
          </Text>
        </View>
        <View className="items-end">
          <Text className="font-sans text-[10px] uppercase tracking-wider text-codex-muted">Due</Text>
          <Text className="mt-0.5 font-sans text-sm text-codex-text">{formatDateTime(goal.deadline)}</Text>
        </View>
      </View>
    </Pressable>
  );
}

export default function HomeScreen() {
  const { logout } = useAuth();
  const { navigate } = useNavigation();
  const [goals, setGoals] = useState<Goal[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeFilter, setActiveFilter] = useState<FilterTab>('All');

  const fetchGoals = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    setError(null);

    const result = await api.getGoals();
    if (result.data) setGoals(result.data);
    else setError(result.error || 'Failed to load goals');

    setLoading(false);
    setRefreshing(false);
  }, []);

  useEffect(() => {
    fetchGoals();
  }, [fetchGoals]);

  const handleRefresh = useCallback(() => fetchGoals(true), [fetchGoals]);
  const handleGoalPress = useCallback((goalId: string) => navigate({ name: 'goal-detail', goalId }), [navigate]);
  const handleCreateGoal = useCallback(() => navigate({ name: 'chat-goal-create' }), [navigate]);
  const handleDashboard = useCallback(() => navigate({ name: 'dashboard' }), [navigate]);
  const handlePayments = useCallback(() => navigate({ name: 'payment-methods' }), [navigate]);

  const filteredGoals = goals.filter((g) => {
    if (activeFilter === 'All') return true;
    return g.status === TAB_STATUSB[activeFilter];
  });

  const nav = (
    <HomeNav
      activeFilter={activeFilter}
      onFilter={setActiveFilter}
      onCreate={handleCreateGoal}
      onDashboard={handleDashboard}
      onPayments={handlePayments}
      onLogout={logout}
    />
  );

  if (loading) {
    return (
      <View className="flex-1 bg-codex-bg">
        <CodexHeader>{nav}</CodexHeader>
        <LoadingSkeleton />
      </View>
    );
  }

  if (error) {
    return (
      <View className="flex-1 bg-codex-bg">
        <CodexHeader>{nav}</CodexHeader>
        <View className="flex-1 items-center justify-center px-6 pb-16">
          <Text className="mb-2 font-serif text-xl text-codex-accent">Couldn't load your goals</Text>
          <Text className="mb-6 text-center font-sans text-sm text-codex-muted">{error}</Text>
          <Pressable className="rounded-sm bg-codex-accent px-6 py-3" onPress={() => fetchGoals()}>
            <Text className="font-sans-medium text-base text-codex-surface">Try again</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  return (
    <View className="flex-1 bg-codex-bg">
      <CodexHeader>{nav}</CodexHeader>

      {filteredGoals.length === 0 ? (
        <EmptyState filter={activeFilter} onCreate={handleCreateGoal} />
      ) : (
        <FlatList
          testID="goals-list"
          className="flex-1 px-4"
          data={filteredGoals}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) => <GoalCard goal={item} onPress={() => handleGoalPress(item.id)} />}
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
