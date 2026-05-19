import { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, FlatList, Pressable, Text, View } from 'react-native';
import { useNavigation } from '../hooks/useNavigation';
import { api } from '../services/api';
import type { Notification } from '../types';

function formatDate(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function typeIcon(type: string): string {
  switch (type) {
    case 'goal_created':
      return '\uD83C\uDF89';
    case 'goal_completed':
      return '\u2705';
    case 'goal_failed':
      return '\u274C';
    case 'proof_received':
      return '\uD83D\uDCDC';
    case 'donation_receipt':
      return '\uD83D\uDCB5';
    default:
      return '\uD83D\uDD14';
  }
}

export default function NotificationListScreen() {
  const { navigate, goBack } = useNavigation();
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unreadCount, setUnreadCount] = useState(0);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    const [notifsResult, unreadResult] = await Promise.all([
      api.getNotifications(),
      api.getUnreadCount(),
    ]);
    if (notifsResult.data) {
      setNotifications(notifsResult.data);
    } else {
      setError(notifsResult.error || 'Failed to load notifications');
    }
    if (unreadResult.data) {
      setUnreadCount(unreadResult.data.unread_count);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleNotifPress = useCallback((notif: Notification) => {
    if (notif.goal_id) {
      navigate({ name: 'goal-detail', goalId: notif.goal_id });
    }
    if (!notif.read) {
      api.markNotificationRead(notif.id).then(() => {
        setNotifications((prev) =>
          prev.map((n) => (n.id === notif.id ? { ...n, read: true } : n)),
        );
        setUnreadCount((c) => Math.max(0, c - 1));
      });
    }
  }, [navigate]);

  const handleMarkAllRead = useCallback(async () => {
    await api.markAllNotificationsRead();
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
    setUnreadCount(0);
  }, []);

  if (loading) {
    return (
      <View className="flex-1 bg-white" testID="notifications-loading">
        <View className="flex-row items-center px-6 pt-16 pb-4">
          <Pressable onPress={goBack} className="mr-3 p-1">
            <Text className="text-2xl text-gray-600">{'<'}</Text>
          </Pressable>
          <Text className="flex-1 text-2xl font-bold text-indigo-600">Notifications</Text>
          <ActivityIndicator size="small" color="#4F46E5" />
        </View>
        <View className="px-4">
          {[1, 2, 3].map((i) => (
            <View key={i} className="mb-3 h-20 rounded-2xl bg-gray-100" />
          ))}
        </View>
      </View>
    );
  }

  if (error) {
    return (
      <View className="flex-1 bg-white">
        <View className="flex-row items-center px-6 pt-16 pb-4">
          <Pressable onPress={goBack} className="mr-3 p-1">
            <Text className="text-2xl text-gray-600">{'<'}</Text>
          </Pressable>
          <Text className="flex-1 text-2xl font-bold text-indigo-600">Notifications</Text>
        </View>
        <View className="flex-1 items-center justify-center px-6">
          <Text className="mb-2 text-lg text-red-500">{error}</Text>
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
        <Pressable onPress={goBack} className="mr-3 p-1">
          <Text className="text-2xl text-gray-600">{'<'}</Text>
        </Pressable>
        <Text className="flex-1 text-2xl font-bold text-indigo-600">Notifications</Text>
        {unreadCount > 0 && (
          <Pressable
            className="rounded-lg bg-indigo-100 px-3 py-1.5"
            onPress={handleMarkAllRead}
          >
            <Text className="text-sm font-medium text-indigo-700">Mark All Read</Text>
          </Pressable>
        )}
      </View>

      {notifications.length === 0 ? (
        <View className="flex-1 items-center justify-center px-6">
          <Text className="text-4xl">{'\uD83D\uDCE2'}</Text>
          <Text className="mt-4 text-lg font-semibold text-gray-900">No notifications</Text>
          <Text className="mt-1 text-sm text-gray-500">
            You're all caught up!
          </Text>
        </View>
      ) : (
        <FlatList
          className="flex-1 px-4"
          data={notifications}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) => (
            <Pressable
              testID={`notification-${item.id}`}
              className={`mb-2 rounded-2xl border p-4 active:bg-gray-50 ${
                item.read ? 'border-gray-100 bg-white' : 'border-indigo-100 bg-indigo-50'
              }`}
              onPress={() => handleNotifPress(item)}
            >
              <View className="flex-row items-start">
                <View className="mr-3 mt-0.5">
                  <Text className="text-xl">{typeIcon(item.type)}</Text>
                </View>
                <View className="flex-1">
                  <View className="flex-row items-center justify-between">
                    <Text
                      className={`flex-1 text-sm ${
                        item.read ? 'font-medium text-gray-900' : 'font-semibold text-gray-900'
                      }`}
                      numberOfLines={2}
                    >
                      {item.title}
                    </Text>
                    {!item.read && (
                      <View testID="unread-badge" className="ml-2 h-2.5 w-2.5 rounded-full bg-indigo-500" />
                    )}
                  </View>
                  {item.body && (
                    <Text className="mt-0.5 text-xs text-gray-500" numberOfLines={2}>
                      {item.body}
                    </Text>
                  )}
                  <Text className="mt-1 text-xs text-gray-400">
                    {formatDate(item.created_at)}
                  </Text>
                </View>
              </View>
            </Pressable>
          )}
          contentContainerStyle={{ paddingBottom: 24 }}
          showsVerticalScrollIndicator={false}
        />
      )}
    </View>
  );
}
