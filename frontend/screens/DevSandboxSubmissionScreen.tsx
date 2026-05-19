import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  Text,
  TextInput,
  View,
} from 'react-native';
import { api } from '../services/api';
import { useNavigation } from '../hooks/useNavigation';
import type { Goal } from '../types';

interface Props {
  goalId: string;
}

interface EnvVarRow {
  key: string;
  value: string;
}

function humanDate(iso: string): string {
  return new Date(iso).toLocaleString();
}

export default function DevSandboxSubmissionScreen({ goalId }: Props) {
  const { navigate, goBack } = useNavigation();
  const [goal, setGoal] = useState<Goal | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [repoUrl, setRepoUrl] = useState('');
  const [branch, setBranch] = useState('');
  const [testCommand, setTestCommand] = useState('');
  const [language, setLanguage] = useState('');
  const [envVars, setEnvVars] = useState<EnvVarRow[]>([]);

  const [submitting, setSubmitting] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [verificationStatus, setVerificationStatus] = useState<string | null>(null);
  const [verificationDetails, setVerificationDetails] = useState<Record<string, unknown> | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchGoal = useCallback(async () => {
    setLoading(true);
    setError(null);
    const result = await api.getGoal(goalId);
    if (result.data) {
      setGoal(result.data);
      const cd = result.data.criteria?.criteria_data || {};
      setRepoUrl((cd.repo_url as string) || '');
      setBranch((cd.branch as string) || 'main');
      setTestCommand((cd.test_command as string) || 'python -m pytest -v');
      setLanguage((cd.language as string) || '');
      const ev = cd.env_vars as Record<string, string> | undefined;
      if (ev && typeof ev === 'object') {
        setEnvVars(Object.entries(ev).map(([k, v]) => ({ key: k, value: String(v) })));
      }
    } else {
      setError(result.error || 'Failed to load goal');
    }
    setLoading(false);
  }, [goalId]);

  useEffect(() => {
    fetchGoal();
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, [fetchGoal]);

  useEffect(() => {
    if (verificationStatus === 'verified' || verificationStatus === 'failed') {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    }
  }, [verificationStatus]);

  const startPolling = useCallback(() => {
    if (pollingRef.current) clearInterval(pollingRef.current);
    pollingRef.current = setInterval(async () => {
      const result = await api.getVerificationStatus(goalId);
      if (result.data) {
        setVerificationStatus(result.data.verification_status);
        setVerificationDetails(result.data.verification_details);
      }
    }, 3000);
  }, [goalId]);

  const handleSubmit = async () => {
    setApiError(null);
    setSubmitting(true);
    setVerificationStatus(null);
    setVerificationDetails(null);

    const envVarsObj: Record<string, string> = {};
    envVars.forEach((row) => {
      if (row.key.trim()) envVarsObj[row.key.trim()] = row.value;
    });

    const result = await api.submitDevSandboxProof(goalId, {
      repo_url: repoUrl,
      branch,
      test_command: testCommand,
      language: language || undefined,
      env_vars: Object.keys(envVarsObj).length > 0 ? envVarsObj : undefined,
    });

    setSubmitting(false);

    if (result.data) {
      setVerificationStatus('pending');
      startPolling();
    } else {
      setApiError(result.error || 'Submission failed');
    }
  };

  const handleRetry = () => {
    setVerificationStatus(null);
    setVerificationDetails(null);
    setApiError(null);
  };

  const isDeadlinePassed = goal && new Date(goal.deadline) < new Date();

  const isTerminal = verificationStatus === 'verified' || verificationStatus === 'failed';

  if (loading && !goal) {
    return (
      <View className="flex-1 bg-white px-4 pt-6" testID="dev-sandbox-loading">
        <View className="mb-6 h-7 w-3/4 rounded bg-gray-200" />
        <View className="mb-4 h-20 rounded-2xl bg-gray-100" />
        <View className="mb-3 h-4 w-1/3 rounded bg-gray-200" />
        <View className="mb-3 h-10 w-full rounded-2xl bg-gray-100" />
      </View>
    );
  }

  if (error || !goal) {
    return (
      <View className="flex-1 bg-white">
        <View className="flex-row items-center px-4 pt-14 pb-2">
          <Pressable onPress={goBack} className="mr-3 p-1">
            <Text className="text-2xl text-gray-600">{'<'}</Text>
          </Pressable>
          <Text className="text-xl font-bold text-gray-900 flex-1">Error</Text>
        </View>
        <View className="flex-1 items-center justify-center px-6">
          <Text className="mb-2 text-lg text-red-500">{error || 'Goal not found'}</Text>
          <Pressable
            className="rounded-xl bg-indigo-600 px-6 py-3"
            onPress={() => navigate({ name: 'home' })}
          >
            <Text className="text-base font-semibold text-white">Go Home</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  return (
    <View className="flex-1 bg-white">
      <View className="flex-row items-center px-4 pt-14 pb-2">
        <Pressable onPress={goBack} className="mr-3 p-1">
          <Text className="text-2xl text-gray-600">{'<'}</Text>
        </Pressable>
        <Text className="text-xl font-bold text-gray-900 flex-1" numberOfLines={1}>
          Dev Sandbox Proof
        </Text>
      </View>

      <ScrollView className="flex-1 px-4" showsVerticalScrollIndicator={false}>
        <View className="mb-4 rounded-2xl border border-gray-200 p-4">
          <Text className="text-lg font-bold text-gray-900">{goal.title}</Text>
          <Text className="mt-1 text-sm text-gray-600">{goal.description}</Text>
          <Text className="mt-2 text-xs text-gray-400">Deadline: {humanDate(goal.deadline)}</Text>
          {!!goal.criteria?.criteria_data.goal_description && (
            <Text className="mt-2 text-sm text-gray-600">
              Goal: {String(goal.criteria.criteria_data.goal_description)}
            </Text>
          )}
        </View>

        {isDeadlinePassed && !isTerminal && (
          <View
            className="mb-4 rounded-2xl border border-red-200 bg-red-50 p-4"
            testID="deadline-passed-message"
          >
            <Text className="text-sm font-medium text-red-700">
              Deadline has passed. You can no longer submit proof for this goal.
            </Text>
          </View>
        )}

        {isTerminal && verificationDetails ? (
          <View className="mb-6">
            {verificationStatus === 'verified' ? (
              <View
                className="mb-4 rounded-2xl border border-green-200 bg-green-50 p-4"
                testID="verification-verified"
              >
                <View className="items-center mb-3">
                  <Text testID="verification-icon-passed" className="text-4xl">✓</Text>
                  <Text className="mt-2 text-lg font-bold text-green-700">Verified</Text>
                </View>

                <View className="rounded-xl bg-white p-3 mb-3">
                  <Text testID="tests-passed-check" className="text-base font-medium text-green-700">
                    ✓ Tests Passed
                  </Text>
                </View>

                <View className="rounded-xl bg-white p-3 mb-3">
                  <Text testID="code-authentic-check" className="text-base font-medium text-green-700">
                    ✓ Code Authentic
                  </Text>
                </View>

                {(verificationDetails.llm_reasoning as string) && (
                  <View testID="llm-reasoning-section" className="rounded-xl bg-white p-3 mb-3">
                    <Text className="text-xs font-medium uppercase tracking-wide text-gray-400 mb-1">
                      LLM Reasoning
                    </Text>
                    <Text className="text-sm text-gray-700">
                      {String(verificationDetails.llm_reasoning)}
                    </Text>
                  </View>
                )}
              </View>
            ) : (
              <View
                className="mb-4 rounded-2xl border border-red-200 bg-red-50 p-4"
                testID="verification-failed"
              >
                <View className="items-center mb-3">
                  <Text className="text-4xl">✗</Text>
                  <Text className="mt-2 text-lg font-bold text-red-700">Failed</Text>
                </View>

                {verificationDetails.stage === 'clone' && (
                  <View testID="failed-stage-clone" className="rounded-xl bg-white p-3 mb-3">
                    <Text className="text-base font-medium text-red-700">Clone Failed</Text>
                    <Text className="text-sm text-gray-600 mt-1">
                      {String(verificationDetails.error || 'Could not clone repository')}
                    </Text>
                  </View>
                )}

                {verificationDetails.stage === 'install' && (
                  <View testID="failed-stage-install" className="rounded-xl bg-white p-3 mb-3">
                    <Text className="text-base font-medium text-red-700">Install Failed</Text>
                    <Text className="text-sm text-gray-600 mt-1">
                      {String(verificationDetails.error || 'Dependency installation failed')}
                    </Text>
                  </View>
                )}

                {verificationDetails.stage === 'test' && (
                  <View testID="failed-stage-test" className="rounded-xl bg-white p-3 mb-3">
                    <Text className="text-base font-medium text-red-700">Test Failed</Text>
                    <Text className="text-sm text-gray-600 mt-1">
                      Exit code: {String(verificationDetails.exit_code ?? 'N/A')}
                    </Text>
                  </View>
                )}

                {(!!verificationDetails.stdout || !!verificationDetails.stderr) && (
                  <View testID="test-output-section" className="rounded-xl bg-white p-3 mb-3">
                    <Text className="text-xs font-medium uppercase tracking-wide text-gray-400 mb-1">
                      Test Output
                    </Text>
                    <ScrollView
                      testID="test-output-scroll"
                      className="max-h-40"
                      scrollEnabled={true}
                    >
                      <Text className="text-xs font-mono text-gray-700">
                        {(() => {
                          const sout = verificationDetails.stdout;
                          const serr = verificationDetails.stderr;
                          let out = '';
                          if (sout) out += `stdout:\n${String(sout)}\n`;
                          if (serr) out += `stderr:\n${String(serr)}`;
                          return out;
                        })()}
                      </Text>
                    </ScrollView>
                  </View>
                )}

                {(verificationDetails.llm_reasoning as string) && (
                  <View testID="llm-reasoning-section" className="rounded-xl bg-white p-3 mb-3">
                    <Text className="text-xs font-medium uppercase tracking-wide text-gray-400 mb-1">
                      LLM Reasoning
                    </Text>
                    <Text className="text-sm text-gray-700">
                      {String(verificationDetails.llm_reasoning)}
                    </Text>
                  </View>
                )}

                <Pressable
                  testID="retry-button"
                  className="rounded-xl bg-indigo-600 px-6 py-3 mt-2"
                  onPress={handleRetry}
                >
                  <Text className="text-center text-base font-semibold text-white">Retry</Text>
                </Pressable>
              </View>
            )}
          </View>
        ) : submitting || verificationStatus === 'pending' ? (
          <View className="mb-6 items-center py-8" testID="submission-loading">
            <ActivityIndicator size="large" color="#4F46E5" />
            <Text className="mt-4 text-sm text-gray-500">Processing verification...</Text>
            {verificationStatus === 'pending' && (
              <View testID="verification-pending" className="mt-2">
                <Text className="text-xs text-gray-400">Verification in progress</Text>
              </View>
            )}
          </View>
        ) : (
          <>
            {!isDeadlinePassed && (
              <View className="mb-6">
                <View className="mb-4">
                  <Text className="text-sm font-medium text-gray-700 mb-1">Repo URL</Text>
                  <TextInput
                    testID="repo-url-input"
                    className="border border-gray-300 rounded-xl px-4 py-3 text-base"
                    value={repoUrl}
                    onChangeText={setRepoUrl}
                    placeholder="https://github.com/user/repo.git"
                    autoCapitalize="none"
                    autoCorrect={false}
                  />
                </View>

                <View className="mb-4">
                  <Text className="text-sm font-medium text-gray-700 mb-1">Branch</Text>
                  <TextInput
                    testID="branch-input"
                    className="border border-gray-300 rounded-xl px-4 py-3 text-base"
                    value={branch}
                    onChangeText={setBranch}
                    placeholder="main"
                    autoCapitalize="none"
                    autoCorrect={false}
                  />
                </View>

                <View className="mb-4">
                  <Text className="text-sm font-medium text-gray-700 mb-1">Test Command</Text>
                  <TextInput
                    testID="test-command-input"
                    className="border border-gray-300 rounded-xl px-4 py-3 text-base"
                    value={testCommand}
                    onChangeText={setTestCommand}
                    placeholder="python -m pytest -v"
                    autoCapitalize="none"
                    autoCorrect={false}
                  />
                </View>

                <View className="mb-4">
                  <Text className="text-sm font-medium text-gray-700 mb-1">Language</Text>
                  <TextInput
                    testID="language-input"
                    className="border border-gray-300 rounded-xl px-4 py-3 text-base"
                    value={language}
                    onChangeText={setLanguage}
                    placeholder="python"
                    autoCapitalize="none"
                    autoCorrect={false}
                  />
                </View>

                <View testID="env-vars-section" className="mb-4">
                  <Text className="text-sm font-medium text-gray-700 mb-1">Environment Variables</Text>
                  {envVars.map((row, index) => (
                    <View key={index} testID={`env-var-row-${index}`} className="flex-row items-center mb-2">
                      <TextInput
                        testID={`env-var-key-${index}`}
                        className="flex-1 border border-gray-300 rounded-xl px-3 py-2 text-sm mr-2"
                        value={row.key}
                        onChangeText={(text) => {
                          const updated = [...envVars];
                          updated[index] = { ...updated[index], key: text };
                          setEnvVars(updated);
                        }}
                        placeholder="KEY"
                        autoCapitalize="none"
                        autoCorrect={false}
                      />
                      <TextInput
                        testID={`env-var-value-${index}`}
                        className="flex-1 border border-gray-300 rounded-xl px-3 py-2 text-sm mr-2"
                        value={row.value}
                        onChangeText={(text) => {
                          const updated = [...envVars];
                          updated[index] = { ...updated[index], value: text };
                          setEnvVars(updated);
                        }}
                        placeholder="value"
                        autoCapitalize="none"
                        autoCorrect={false}
                      />
                      <Pressable
                        testID={`remove-env-var-${index}`}
                        onPress={() => {
                          const updated = envVars.filter((_, i) => i !== index);
                          setEnvVars(updated);
                        }}
                        className="p-2"
                      >
                        <Text className="text-red-500 text-lg">×</Text>
                      </Pressable>
                    </View>
                  ))}
                  <Pressable
                    testID="add-env-var-button"
                    onPress={() => setEnvVars([...envVars, { key: '', value: '' }])}
                    className="border border-dashed border-gray-300 rounded-xl py-3 items-center"
                  >
                    <Text className="text-sm text-indigo-600">+ Add Environment Variable</Text>
                  </Pressable>
                </View>

                {apiError && (
                  <View className="mb-4 rounded-xl bg-red-50 border border-red-200 p-3">
                    <Text className="text-sm text-red-600">{apiError}</Text>
                  </View>
                )}

                <Pressable
                  testID="submit-proof-button"
                  className="rounded-xl bg-indigo-600 px-6 py-4 items-center"
                  onPress={handleSubmit}
                >
                  <Text className="text-base font-semibold text-white">
                    {submitting ? 'Submitting...' : 'Submit Proof'}
                  </Text>
                </Pressable>
              </View>
            )}
          </>
        )}
      </ScrollView>
    </View>
  );
}
