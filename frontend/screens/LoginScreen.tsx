import { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, Text, TextInput, View } from 'react-native';
import { useAuth } from '../hooks/useAuth';

type Mode = 'login' | 'register';

function providerLabel(provider: string | undefined): string {
  if (provider === 'google') return 'Google';
  if (provider === 'github') return 'GitHub';
  if (provider === 'email') return 'email';
  return provider || 'another provider';
}

export default function LoginScreen() {
  const {
    loginWithGoogle,
    loginWithGithub,
    loginWithEmail,
    registerWithEmail,
    redirectError,
    clearRedirectError,
  } = useAuth();

  const [googleLoading, setGoogleLoading] = useState(false);
  const [githubLoading, setGithubLoading] = useState(false);
  const [emailLoading, setEmailLoading] = useState(false);

  const [mode, setMode] = useState<Mode>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');

  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [conflictProvider, setConflictProvider] = useState<string | null>(null);

  useEffect(() => {
    if (redirectError?.error === 'account_exists' && redirectError.provider) {
      setConflictProvider(redirectError.provider);
      setErrorMessage(null);
    }
  }, [redirectError]);

  const handleGoogle = () => {
    setGoogleLoading(true);
    setErrorMessage(null);
    setConflictProvider(null);
    clearRedirectError();
    loginWithGoogle();
  };

  const handleGithub = () => {
    setGithubLoading(true);
    setErrorMessage(null);
    setConflictProvider(null);
    clearRedirectError();
    loginWithGithub();
  };

  const handleSubmitEmail = async () => {
    setErrorMessage(null);
    setConflictProvider(null);
    if (!email || !password) {
      setErrorMessage('Email and password are required.');
      return;
    }
    if (mode === 'register' && password.length < 8) {
      setErrorMessage('Password must be at least 8 characters.');
      return;
    }
    setEmailLoading(true);
    try {
      const result =
        mode === 'register'
          ? await registerWithEmail(email, password, displayName || undefined)
          : await loginWithEmail(email, password);
      if (result.ok) {
        return;
      }
      if (result.status === 409 && result.provider) {
        setConflictProvider(result.provider);
        return;
      }
      if (result.status === 401) {
        setErrorMessage('Invalid email or password');
        return;
      }
      setErrorMessage('Something went wrong. Please try again.');
    } catch (err) {
      setErrorMessage('Network error. Please try again.');
    } finally {
      setEmailLoading(false);
    }
  };

  const toggleMode = () => {
    setMode((m) => (m === 'login' ? 'register' : 'login'));
    setErrorMessage(null);
    setConflictProvider(null);
  };

  const conflictBanner = conflictProvider ? (
    <View
      accessibilityRole="alert"
      testID="conflict-banner"
      className="w-full rounded-sm border border-codex-border bg-codex-surface px-4 py-3"
    >
      <Text className="font-sans text-sm text-codex-text">
        {`This email is registered with ${providerLabel(conflictProvider)}. Use the ${providerLabel(conflictProvider)} button below to sign in.`}
      </Text>
    </View>
  ) : null;

  const errorBanner = errorMessage ? (
    <View
      accessibilityRole="alert"
      testID="error-banner"
      className="w-full rounded-sm border border-codex-border bg-codex-surface px-4 py-3"
    >
      <Text className="font-sans text-sm text-codex-text">{errorMessage}</Text>
    </View>
  ) : null;

  const highlightGoogle = conflictProvider === 'google';
  const highlightGithub = conflictProvider === 'github';

  return (
    <View className="flex-1 items-center justify-center bg-codex-bg px-6">
      <View className="mb-10 items-center">
        <Text className="font-serif text-5xl text-codex-text">Sacrifice</Text>
        <Text className="mt-3 font-serif-italic text-base text-codex-muted">
          Commit to your goals. Put money on the line.
        </Text>
      </View>

      <View className="w-full max-w-sm gap-3">
        {conflictBanner}
        {errorBanner}

        <TextInput
          testID="email-input"
          placeholder="Email"
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="email-address"
          value={email}
          onChangeText={setEmail}
          className="w-full rounded-sm border border-codex-border bg-codex-surface px-4 py-3 font-sans text-base text-codex-text"
          placeholderTextColor="#85796A"
        />
        <TextInput
          testID="password-input"
          placeholder="Password"
          secureTextEntry
          autoCapitalize="none"
          autoCorrect={false}
          value={password}
          onChangeText={setPassword}
          className="w-full rounded-sm border border-codex-border bg-codex-surface px-4 py-3 font-sans text-base text-codex-text"
          placeholderTextColor="#85796A"
        />
        {mode === 'register' ? (
          <TextInput
            testID="display-name-input"
            placeholder="Display name (optional)"
            autoCapitalize="words"
            value={displayName}
            onChangeText={setDisplayName}
            className="w-full rounded-sm border border-codex-border bg-codex-surface px-4 py-3 font-sans text-base text-codex-text"
            placeholderTextColor="#85796A"
          />
        ) : null}

        <Pressable
          testID="email-submit"
          className={`flex-row items-center justify-center rounded-sm bg-codex-dark py-3.5 px-6 ${emailLoading ? 'opacity-50' : ''}`}
          onPress={handleSubmitEmail}
          disabled={emailLoading}
        >
          {emailLoading ? (
            <ActivityIndicator size="small" color="#FFFFFF" />
          ) : (
            <Text className="font-sans-medium text-base text-codex-bg">
              {mode === 'register' ? 'Create account' : 'Continue with email'}
            </Text>
          )}
        </Pressable>

        <Pressable testID="mode-toggle" onPress={toggleMode}>
          <Text className="text-center font-sans text-sm text-codex-muted">
            {mode === 'login'
              ? 'Need an account? Sign up'
              : 'Already have an account? Log in'}
          </Text>
        </Pressable>

        <View className="my-2 flex-row items-center">
          <View className="h-px flex-1 bg-codex-border" />
          <Text className="mx-3 font-sans text-xs uppercase tracking-wider text-codex-muted">
            or
          </Text>
          <View className="h-px flex-1 bg-codex-border" />
        </View>

        <Pressable
          testID="google-button"
          className={`flex-row items-center justify-center rounded-sm border ${highlightGoogle ? 'border-codex-text bg-codex-bg' : 'border-codex-border bg-codex-surface'} py-3.5 px-6 ${googleLoading ? 'opacity-50' : ''}`}
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
          testID="github-button"
          className={`flex-row items-center justify-center rounded-sm py-3.5 px-6 ${highlightGithub ? 'bg-codex-text' : 'bg-codex-dark'} ${githubLoading ? 'opacity-50' : ''}`}
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
