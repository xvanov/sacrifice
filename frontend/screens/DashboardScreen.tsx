import { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, Text, View } from 'react-native';
import { CodexHeader } from '../components/CodexHeader';
import { CodexCard } from '../components/CodexCard';
import { CodexButton } from '../components/CodexButton';
import { StatusBadge, typeLabelShort } from '../components/StatusBadge';
import { formatMoney } from '../utils/format';
import { api } from '../services/api';
import { useNavigation } from '../hooks/useNavigation';
import type { DashboardHistoryItem, DashboardStats } from '../types';

function StatCard({
  label,
  value,
  testID,
}: {
  label: string;
  value: string;
  testID?: string;
}) {
  return (
    <CodexCard className="p-4" testID={testID}>
      <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">
        {label}
      </Text>
      <Text className="mt-1 font-serif text-2xl text-codex-text">
        {value}
      </Text>
    </CodexCard>
  );
}

export default function DashboardScreen() {
  const { navigate } = useNavigation();
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [history, setHistory] = useState<DashboardHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);

    const [statsResult, historyResult] = await Promise.all([
      api.getDashboardStats(),
      api.getDashboardHistory(),
    ]);

    if (statsResult.data) {
      setStats(statsResult.data);
    } else {
      setError(statsResult.error || 'Failed to load stats');
    }

    if (historyResult.data) {
      setHistory(historyResult.data);
    }

    setLoading(false);
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  if (loading) {
    return (
      <View className="flex-1 bg-codex-bg">
        <CodexHeader />
        <View className="flex-row items-center px-6 pb-2 pt-3">
          <Pressable onPress={() => navigate({ name: 'home' })} className="mr-3 p-1">
            <Text className="font-serif text-2xl text-codex-muted">{'←'}</Text>
          </Pressable>
          <Text className="flex-1 font-serif-italic text-lg text-codex-text">The Ledger</Text>
          <ActivityIndicator size="small" color="#8A2A1C" testID="loading-indicator" />
        </View>
        <View className="px-6">
          <View className="mb-3 h-24 rounded-sm bg-codex-surface" />
          <View className="mb-3 h-24 rounded-sm bg-codex-surface" />
          <View className="h-40 rounded-sm bg-codex-surface" />
        </View>
      </View>
    );
  }

  if (error) {
    return (
      <View className="flex-1 bg-codex-bg">
        <CodexHeader />
        <View className="flex-row items-center px-6 pb-2 pt-3">
          <Pressable onPress={() => navigate({ name: 'home' })} className="mr-3 p-1">
            <Text className="font-serif text-2xl text-codex-muted">{'←'}</Text>
          </Pressable>
          <Text className="flex-1 font-serif-italic text-lg text-codex-text">The Ledger</Text>
        </View>
        <View className="flex-1 items-center justify-center px-6">
          <Text className="mb-2 font-sans text-lg text-codex-accent" testID="dashboard-error">
            {error}
          </Text>
          <CodexButton onPress={fetchData}>
            Retry
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
        <Text className="flex-1 font-serif-italic text-lg text-codex-text">The Ledger</Text>
      </View>

      <ScrollView className="flex-1 px-6" showsVerticalScrollIndicator={false}>
        {stats && (
          <View testID="stats-cards">
            <View className="mb-3 flex-row gap-3">
              <View className="flex-1">
                <StatCard
                  label="Total Goals"
                  value={String(stats.total_goals)}
                  testID="stat-total-goals"
                />
              </View>
              <View className="flex-1">
                <StatCard
                  label="Success Rate"
                  value={`${stats.success_rate}%`}
                  testID="stat-success-rate"
                />
              </View>
            </View>
            <View className="mb-4 flex-row gap-3">
              <View className="flex-1">
                <StatCard
                  label="Total Donated"
                  value={formatMoney(stats.total_donated)}
                  testID="stat-total-donated"
                />
              </View>
              <View className="flex-1">
                <StatCard
                  label="Total Saved"
                  value={formatMoney(stats.total_saved)}
                  testID="stat-total-saved"
                />
              </View>
            </View>
          </View>
        )}

        <Text className="mb-3 font-serif text-xl text-codex-text">History</Text>

        {history.length === 0 ? (
          <View className="mb-6 items-center rounded-sm border border-codex-border bg-codex-surface px-6 py-8">
            <Text className="font-serif text-lg text-codex-text">No history yet</Text>
            <Text className="mt-1 text-center font-sans text-sm text-codex-muted">
              Completed and failed goals will show up here once you have some.
            </Text>
          </View>
        ) : (
          <View className="mb-6" testID="history-list">
            {history.map((item) => (
              <Pressable
                key={item.id}
                className="mb-2 rounded-sm border border-codex-border bg-codex-surface p-4 active:bg-codex-bg"
                onPress={() => navigate({ name: 'goal-detail', goalId: item.id })}
                testID={`history-item-${item.id}`}
              >
                <View className="mb-2 flex-row items-center justify-between gap-2">
                  <Text className="flex-1 font-serif text-base text-codex-text" numberOfLines={1}>
                    {item.title}
                  </Text>
                  <StatusBadge status={item.status} />
                </View>
                <View className="flex-row items-center justify-between">
                  <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">
                    {typeLabelShort(item.goal_type)}
                  </Text>
                  <Text className="font-sans-medium text-sm text-codex-text">
                    {formatMoney(item.pledge_amount)}
                  </Text>
                </View>
              </Pressable>
            ))}
          </View>
        )}
      {/* Dev-only diagnostics link (AC6) */}
        {__DEV__ && (
          <View className="mb-6 items-center">
            <Pressable
              testID="diagnostics-link"
              className="rounded-sm border border-codex-border px-4 py-2 active:bg-codex-surface"
              onPress={() => navigate({ name: 'diagnostics' })}
            >
              <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">
                Diagnostics
              </Text>
            </Pressable>
          </View>
        )}
      </ScrollView>
    </View>
  );
}
