import { useState } from 'react';
import { ActivityIndicator, Pressable, Text, View } from 'react-native';
import { useAuth } from '../hooks/useAuth';

export default function LoginScreen() {
  const { loginWithGoogle, loginWithGithub } = useAuth();
  const [googleLoading, setGoogleLoading] = useState(false);
  const [githubLoading, setGithubLoading] = useState(false);

  const handleGoogle = () => {
    setGoogleLoading(true);
    loginWithGoogle();
  };

  const handleGithub = () => {
    setGithubLoading(true);
    loginWithGithub();
  };

  return (
    <View className="flex-1 items-center justify-center bg-white px-6">
      <View className="mb-12 items-center">
        <Text className="text-4xl font-bold text-indigo-600">Sacrifice</Text>
        <Text className="mt-2 text-base text-gray-500 text-center">
          Commit to your goals. Put money on the line.
        </Text>
      </View>

      <View className="w-full max-w-sm gap-4">
        <Pressable
          className={`flex-row items-center justify-center rounded-xl border border-gray-300 bg-white py-3.5 px-6 ${googleLoading ? 'opacity-50' : ''}`}
          onPress={handleGoogle}
          disabled={googleLoading}
        >
          {googleLoading ? (
            <ActivityIndicator size="small" color="#6B7280" />
          ) : (
            <Text className="text-base font-medium text-gray-800">
              Sign in with Google
            </Text>
          )}
        </Pressable>

        <Pressable
          className={`flex-row items-center justify-center rounded-xl bg-gray-900 py-3.5 px-6 ${githubLoading ? 'opacity-50' : ''}`}
          onPress={handleGithub}
          disabled={githubLoading}
        >
          {githubLoading ? (
            <ActivityIndicator size="small" color="#FFFFFF" />
          ) : (
            <Text className="text-base font-medium text-white">
              Sign in with GitHub
            </Text>
          )}
        </Pressable>
      </View>
    </View>
  );
}
