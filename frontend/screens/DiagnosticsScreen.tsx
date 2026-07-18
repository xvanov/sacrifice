import { useEffect, useState, useCallback } from 'react';
import { ActivityIndicator, Platform, Pressable, ScrollView, Text, View } from 'react-native';
import { getApiBaseUrl } from '../config';
import { CodexHeader } from '../components/CodexHeader';
import { CodexButton } from '../components/CodexButton';
import { useNavigation } from '../hooks/useNavigation';

/**
 * On-device diagnostics screen (AC6).
 *
 * Dev builds only. Shows:
 *   - Resolved API URL
 *   - Backend /api/health status
 *   - Platform / OS
 *   - App version
 */

// eslint-disable-next-line @typescript-eslint/no-require-imports
const APP_VERSION = require('../app.json').expo.version || 'unknown';

interface RowProps {
  label: string;
  value: string;
  ok?: boolean;
}

function Row({ label, value, ok }: RowProps) {
  return (
    <View className="mb-3 border-b border-codex-border pb-2">
      <Text className="font-sans-medium text-xs uppercase tracking-wider text-codex-muted">
        {label}
      </Text>
      <Text
        className={`font-mono text-sm mt-0.5 ${
          ok === undefined ? 'text-codex-text' : ok ? 'text-green-700' : 'text-codex-accent'
        }`}
        selectable
      >
        {value}
      </Text>
    </View>
  );
}

export default function DiagnosticsScreen() {
  const { goBack } = useNavigation();
  const [healthStatus, setHealthStatus] = useState<string>('Checking...');
  const [healthOk, setHealthOk] = useState<boolean | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  const apiUrl = getApiBaseUrl();

  const checkHealth = useCallback(async () => {
    setHealthStatus('Checking...');
    setHealthOk(undefined);
    setError(null);
    try {
      const res = await fetch(`${apiUrl}/api/health`, {
        method: 'GET',
        headers: { Accept: 'application/json' },
      });
      if (res.ok) {
        const body = await res.json().catch(() => null);
        setHealthStatus(body ? JSON.stringify(body) : 'OK (no body)');
        setHealthOk(true);
      } else {
        setHealthStatus(`HTTP ${res.status}: ${res.statusText}`);
        setHealthOk(false);
      }
    } catch (e: any) {
      setHealthStatus(`Error: ${e?.message || 'Unknown error'}`);
      setHealthOk(false);
      setError(e?.message || 'Health check failed');
    }
  }, [apiUrl]);

  useEffect(() => {
    checkHealth();
  }, [checkHealth]);

  return (
    <View className="flex-1 bg-codex-bg">
      <CodexHeader pageNumber="D" />
      <View className="flex-row items-center px-6 pb-2 pt-3">
        <Pressable onPress={goBack} className="mr-3 p-1">
          <Text className="font-serif text-2xl text-codex-muted">{'←'}</Text>
        </Pressable>
        <Text className="flex-1 font-serif-italic text-lg text-codex-text">Diagnostics</Text>
      </View>

      <ScrollView className="flex-1 px-4" contentContainerStyle={{ paddingBottom: 32 }}>
        {/* API URL (AC6.1) */}
        <Row label="API Base URL" value={apiUrl} />

        {/* Health status (AC6.2) */}
        <View className="mb-3 border-b border-codex-border pb-2">
          <Text className="font-sans-medium text-xs uppercase tracking-wider text-codex-muted">
            Backend /api/health
          </Text>
          {healthOk === undefined ? (
            <View className="mt-1 flex-row items-center gap-2">
              <ActivityIndicator size="small" color="#8A2A1C" />
              <Text className="font-mono text-sm text-codex-muted">{healthStatus}</Text>
            </View>
          ) : (
            <Text
              className={`font-mono text-sm mt-0.5 ${
                healthOk ? 'text-green-700' : 'text-codex-accent'
              }`}
              selectable
            >
              {healthStatus}
            </Text>
          )}
        </View>

        {/* Platform / OS (AC6.3) */}
        <Row label="Platform" value={String(Platform.OS)} />
        <Row label="OS Version" value={String(Platform.Version ?? 'unknown')} />
        <Row label="React Native" value={String(Platform.constants?.reactNativeVersion ?? 'unknown')} />

        {/* App version (AC6.4) */}
        <Row label="App Version" value={APP_VERSION} />

        {/* Error detail */}
        {error && (
          <View className="mt-2 rounded-sm border border-codex-accent bg-codex-accent-light p-3">
            <Text className="font-sans-bold text-sm text-codex-accent">Connection Error</Text>
            <Text className="mt-1 font-sans text-sm text-codex-text-secondary">{error}</Text>
          </View>
        )}

        {/* Retry button */}
        <View className="mt-6">
          <CodexButton testID="diagnostics-retry" onPress={checkHealth} variant="secondary">
            Run health check again
          </CodexButton>
        </View>
      </ScrollView>
    </View>
  );
}