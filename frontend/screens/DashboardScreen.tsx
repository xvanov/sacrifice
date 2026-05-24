import { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, Text, View } from 'react-native';
import { CodexHeader } from '../components/CodexHeader';
import { CodexCard } from '../components/CodexCard';
import { CodexButton } from '../components/CodexButton';
import { statusLabel } from '../components/StatusBadge';
import { api } from '../services/api';
import { useNavigation } from '../hooks/useNavigation';
import type { DashboardHistoryItem, DashboardStats } from '../types';

function formatAmount(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

function statusColor(status: string): string {
  switch (status) {
    case 'verified':
      return 'text-codex-accent';
    case 'failed':
    case 'payment_failed':
      return 'text-codex-dark';
    case 'active':
      return 'text-codex-dark-light';
    default:
      return 'text-codex-muted';
  }
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
                  value={formatAmount(stats.total_donated)}
                  testID="stat-total-donated"
                />
              </View>
              <View className="flex-1">
                <StatCard
                  label="Total Saved"
                  value={formatAmount(stats.total_saved)}
                  testID="stat-total-saved"
                />
              </View>
            </View>
          </View>
        )}

        <Text className="mb-3 font-serif text-base text-codex-text">History</Text>

        {history.length === 0 ? (
          <View className="mb-6 items-center rounded-sm border border-codex-border bg-codex-surface p-6">
            <Text className="font-sans text-sm text-codex-muted">No goal history yet</Text>
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
                <View className="mb-1 flex-row items-center justify-between">
                  <Text className="flex-1 font-serif text-base text-codex-text" numberOfLines={1}>
                    {item.title}
                  </Text>
                  <Text className={`ml-2 font-sans text-xs uppercase ${statusColor(item.status)}`}>
                    {statusLabel(item.status)}
                  </Text>
                </View>
                <View className="flex-row items-center justify-between">
                  <Text className="font-sans text-xs text-codex-muted">{typeLabel(item.goal_type)}</Text>
                  <Text className="font-mono text-xs text-codex-muted">
                    {formatAmount(item.pledge_amount)}
                  </Text>
                </View>
              </Pressable>
            ))}
          </View>
        )}
      </ScrollView>
    </View>
  );
}
