import { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, FlatList, Pressable, Text, View } from 'react-native';
import { CodexHeader } from '../components/CodexHeader';
import { CodexButton } from '../components/CodexButton';
import { useNavigation } from '../hooks/useNavigation';
import { formatRelative } from '../utils/format';
import { api } from '../services/api';
import type { Notification } from '../types';

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
      <View className="flex-1 bg-codex-bg" testID="notifications-loading">
        <CodexHeader />
        <View className="flex-row items-center px-6 pb-2 pt-3">
          <Pressable onPress={goBack} className="mr-3 p-1">
            <Text className="font-serif text-2xl text-codex-muted">{'←'}</Text>
          </Pressable>
          <Text className="flex-1 font-serif-italic text-lg text-codex-text">Notifications</Text>
          <ActivityIndicator size="small" color="#8A2A1C" />
        </View>
        <View className="px-6">
          {[1, 2, 3].map((i) => (
            <View key={i} className="mb-3 h-20 rounded-sm bg-codex-surface" />
          ))}
        </View>
      </View>
    );
  }

  if (error) {
    return (
      <View className="flex-1 bg-codex-bg">
        <CodexHeader />
        <View className="flex-row items-center px-6 pb-2 pt-3">
          <Pressable onPress={goBack} className="mr-3 p-1">
            <Text className="font-serif text-2xl text-codex-muted">{'←'}</Text>
          </Pressable>
          <Text className="flex-1 font-serif-italic text-lg text-codex-text">Notifications</Text>
        </View>
        <View className="flex-1 items-center justify-center px-6">
          <Text className="mb-2 font-sans text-lg text-codex-accent">{error}</Text>
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
        <Pressable onPress={goBack} className="mr-3 p-1">
          <Text className="font-serif text-2xl text-codex-muted">{'←'}</Text>
        </Pressable>
        <Text className="flex-1 font-serif-italic text-lg text-codex-text">Notifications</Text>
        {unreadCount > 0 && (
          <Pressable
            className="rounded-sm border border-codex-border bg-codex-surface px-3 py-1.5"
            onPress={handleMarkAllRead}
          >
            <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">Mark All Read</Text>
          </Pressable>
        )}
      </View>

      {notifications.length === 0 ? (
        <View className="flex-1 items-center justify-center px-6">
          <Text className="font-serif text-4xl">{'\uD83D\uDCE2'}</Text>
          <Text className="mt-4 font-serif text-lg text-codex-text">No notifications</Text>
          <Text className="mt-1 font-sans text-sm text-codex-muted">
            You're all caught up!
          </Text>
        </View>
      ) : (
        <FlatList
          className="flex-1 px-6"
          data={notifications}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) => (
            <Pressable
              testID={`notification-${item.id}`}
              className={`mb-2 rounded-sm border p-4 active:bg-codex-bg ${
                item.read ? 'border-codex-border bg-codex-surface' : 'border-codex-accent bg-codex-surface'
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
                      className={`flex-1 font-sans text-sm ${
                        item.read ? 'text-codex-text' : 'font-sans-medium text-codex-text'
                      }`}
                      numberOfLines={2}
                    >
                      {item.title}
                    </Text>
                    {!item.read && (
                      <View testID="unread-badge" className="ml-2 h-2.5 w-2.5 rounded-full bg-codex-accent" />
                    )}
                  </View>
                  {item.body && (
                    <Text className="mt-0.5 font-sans text-xs text-codex-muted" numberOfLines={2}>
                      {item.body}
                    </Text>
                  )}
                  <Text className="mt-1 font-sans text-xs text-codex-muted">
                    {formatRelative(item.created_at)}
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
