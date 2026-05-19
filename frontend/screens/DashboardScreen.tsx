import { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, FlatList, Pressable, ScrollView, Text, View } from 'react-native';
import { api } from '../services/api';
import { useNavigation } from '../hooks/useNavigation';
import type { DashboardHistoryItem, DashboardStats } from '../types';

function formatAmount(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

function statusLabel(status: string): string {
  return status.replace('_', ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function statusColor(status: string): string {
  switch (status) {
    case 'verified':
      return 'text-green-600';
    case 'failed':
      return 'text-red-600';
    case 'active':
      return 'text-blue-600';
    case 'draft':
      return 'text-gray-600';
    default:
      return 'text-yellow-600';
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
  color,
  testID,
}: {
  label: string;
  value: string;
  color?: string;
  testID?: string;
}) {
  return (
    <View className={`rounded-2xl border border-gray-100 bg-white p-4 ${color || ''}`} testID={testID}>
      <Text className="text-xs font-medium uppercase tracking-wide text-gray-400">
        {label}
      </Text>
      <Text className="mt-1 text-2xl font-bold text-gray-900">
        {value}
      </Text>
    </View>
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
      <View className="flex-1 bg-white">
        <View className="flex-row items-center px-6 pt-16 pb-4">
          <Pressable onPress={() => navigate({ name: 'home' })} className="mr-3 p-1">
            <Text className="text-2xl text-gray-600">{'<'}</Text>
          </Pressable>
          <Text className="text-2xl font-bold text-indigo-600 flex-1">Dashboard</Text>
          <ActivityIndicator size="small" color="#4F46E5" testID="loading-indicator" />
        </View>
        <View className="px-4">
          <View className="mb-3 h-24 rounded-2xl bg-gray-100" />
          <View className="mb-3 h-24 rounded-2xl bg-gray-100" />
          <View className="h-40 rounded-2xl bg-gray-100" />
        </View>
      </View>
    );
  }

  if (error) {
    return (
      <View className="flex-1 bg-white">
        <View className="flex-row items-center px-6 pt-16 pb-4">
          <Pressable onPress={() => navigate({ name: 'home' })} className="mr-3 p-1">
            <Text className="text-2xl text-gray-600">{'<'}</Text>
          </Pressable>
          <Text className="text-2xl font-bold text-indigo-600">Dashboard</Text>
        </View>
        <View className="flex-1 items-center justify-center px-6">
          <Text className="mb-2 text-lg text-red-500" testID="dashboard-error">
            {error}
          </Text>
          <Pressable
            className="rounded-xl bg-indigo-600 px-6 py-3"
            onPress={fetchData}
          >
            <Text className="text-base font-semibold text-white">Retry</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  return (
    <View className="flex-1 bg-white">
      <View className="flex-row items-center px-6 pt-16 pb-4">
        <Pressable onPress={() => navigate({ name: 'home' })} className="mr-3 p-1">
          <Text className="text-2xl text-gray-600">{'<'}</Text>
        </Pressable>
        <Text className="text-2xl font-bold text-indigo-600 flex-1">Dashboard</Text>
      </View>

      <ScrollView className="flex-1 px-4" showsVerticalScrollIndicator={false}>
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

        <Text className="mb-3 text-base font-semibold text-gray-900">History</Text>

        {history.length === 0 ? (
          <View className="mb-6 items-center rounded-2xl border border-gray-200 p-6">
            <Text className="text-sm text-gray-500">No goal history yet</Text>
          </View>
        ) : (
          <View className="mb-6" testID="history-list">
            {history.map((item) => (
              <Pressable
                key={item.id}
                className="mb-2 rounded-2xl border border-gray-100 bg-white p-4 active:bg-gray-50"
                onPress={() => navigate({ name: 'goal-detail', goalId: item.id })}
                testID={`history-item-${item.id}`}
              >
                <View className="mb-1 flex-row items-center justify-between">
                  <Text className="flex-1 text-base font-semibold text-gray-900" numberOfLines={1}>
                    {item.title}
                  </Text>
                  <Text className={`ml-2 text-xs font-medium ${statusColor(item.status)}`}>
                    {statusLabel(item.status)}
                  </Text>
                </View>
                <View className="flex-row items-center justify-between">
                  <Text className="text-xs text-gray-400">{typeLabel(item.goal_type)}</Text>
                  <Text className="text-xs text-gray-400">
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
