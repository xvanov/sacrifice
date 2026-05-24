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
    <View className="flex-1 items-center justify-center bg-codex-bg px-6">
      <View className="mb-12 items-center">
        <Text className="font-serif text-5xl text-codex-text">Sacrifice</Text>
        <Text className="mt-3 font-serif-italic text-base text-codex-muted">
          Commit to your goals. Put money on the line.
        </Text>
      </View>

      <View className="w-full max-w-sm gap-4">
        <Pressable
          className={`flex-row items-center justify-center rounded-sm border border-codex-border bg-codex-surface py-3.5 px-6 ${googleLoading ? 'opacity-50' : ''}`}
          onPress={handleGoogle}
          disabled={googleLoading}
        >
          {googleLoading ? (
            <ActivityIndicator size="small" color="#85796A" />
          ) : (
            <Text className="font-sans-medium text-base text-codex-text">
              Sign in with Google
            </Text>
          )}
        </Pressable>

        <Pressable
          className={`flex-row items-center justify-center rounded-sm bg-codex-dark py-3.5 px-6 ${githubLoading ? 'opacity-50' : ''}`}
          onPress={handleGithub}
          disabled={githubLoading}
        >
          {githubLoading ? (
            <ActivityIndicator size="small" color="#FFFFFF" />
          ) : (
            <Text className="font-sans-medium text-base text-codex-bg">
              Sign in with GitHub
            </Text>
          )}
        </Pressable>
      </View>
    </View>
  );
}
