import { Pressable, Text, View } from 'react-native';
import { useAuth } from '../hooks/useAuth';

export default function HomeScreen() {
  const { user, logout } = useAuth();

  return (
    <View className="flex-1 bg-white">
      <View className="flex-row items-center justify-between px-6 pt-16 pb-4">
        <Text className="text-2xl font-bold text-indigo-600">Sacrifice</Text>
        <Pressable
          className="rounded-lg bg-gray-100 px-4 py-2"
          onPress={logout}
        >
          <Text className="text-sm font-medium text-gray-700">Logout</Text>
        </Pressable>
      </View>

      <View className="flex-1 items-center justify-center px-6">
        <View className="items-center mb-8">
          {user?.avatar_url ? (
            <View className="mb-4 h-20 w-20 rounded-full bg-gray-200" />
          ) : (
            <View className="mb-4 h-20 w-20 rounded-full bg-indigo-100 items-center justify-center">
              <Text className="text-2xl font-bold text-indigo-600">
                {user?.display_name?.charAt(0)?.toUpperCase() || '?'}
              </Text>
            </View>
          )}
          <Text className="text-xl font-semibold text-gray-900">
            {user?.display_name || 'User'}
          </Text>
          <Text className="text-sm text-gray-500 mt-1">
            {user?.email || ''}
          </Text>
        </View>

        <View className="w-full max-w-sm rounded-2xl border border-gray-200 p-6">
          <Text className="text-lg font-semibold text-gray-900 mb-2">
            Welcome to Sacrifice
          </Text>
          <Text className="text-sm text-gray-500">
            Create your first goal to get started. Put money on the line and
            stay accountable.
          </Text>
        </View>
      </View>
    </View>
  );
}
