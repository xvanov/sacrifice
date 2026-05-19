import { useCallback, useEffect, useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { useNavigation } from '../hooks/useNavigation';
import { api } from '../services/api';

export function NotificationBell() {
  const { navigate } = useNavigation();
  const [unreadCount, setUnreadCount] = useState(0);

  const fetchUnread = useCallback(async () => {
    const result = await api.getUnreadCount();
    if (result.data) {
      setUnreadCount(result.data.unread_count);
    }
  }, []);

  useEffect(() => {
    fetchUnread();
    const interval = setInterval(fetchUnread, 15000);
    return () => clearInterval(interval);
  }, [fetchUnread]);

  return (
    <Pressable
      testID="notification-bell"
      className="relative rounded-lg bg-gray-100 px-3 py-2"
      onPress={() => navigate({ name: 'notifications' })}
    >
      <Text className="text-lg text-gray-700">{'\uD83D\uDD14'}</Text>
      {unreadCount > 0 && (
        <View
          testID="unread-badge"
          className="absolute -right-1 -top-1 flex h-5 min-w-[20px] items-center justify-center rounded-full bg-red-500 px-1"
        >
          <Text className="text-xs font-bold text-white">
            {unreadCount > 99 ? '99+' : unreadCount}
          </Text>
        </View>
      )}
    </Pressable>
  );
}
