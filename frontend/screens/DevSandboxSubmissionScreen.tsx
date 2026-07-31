import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  Text,
  TextInput,
  View,
} from 'react-native';
import { CodexHeader } from '../components/CodexHeader';
import { CodexCard } from '../components/CodexCard';
import { CodexButton } from '../components/CodexButton';
import { CodexInput } from '../components/CodexInput';
import { CodexFooter } from '../components/CodexFooter';
import { SectionHeading } from '../components/SectionHeading';
import { formatDateTime } from '../utils/format';
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
  // Deliberately never seeded from the loaded goal: the API does not return the
  // stored token (it is encrypted at rest) and a secret must not be rendered
  // back into an input even if a future response were to include one.
  const [githubToken, setGithubToken] = useState('');

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

  // Both goal types land on this screen, but only dev_sandbox clones the repo and
  // runs a command inside a container. github_repo is checked entirely through
  // the GitHub API, so a test invocation, a language and container env vars are
  // not just unused there — showing them implies the submission does something it
  // does not. Everything else (repo, branch, optional token, polling, verdict) is
  // genuinely shared, which is why this is a flag and not a second screen.
  const isSandbox = goal?.goal_type !== 'github_repo';

  const handleSubmit = async () => {
    setApiError(null);
    setSubmitting(true);
    setVerificationStatus(null);
    setVerificationDetails(null);

    const envVarsObj: Record<string, string> = {};
    envVars.forEach((row) => {
      if (row.key.trim()) envVarsObj[row.key.trim()] = row.value;
    });

    const trimmedToken = githubToken.trim();

    const result = await api.submitDevSandboxProof(goalId, {
      repo_url: repoUrl,
      branch,
      // Omitted entirely for github_repo, which never runs a command: sending a
      // test invocation it ignores is what made this screen look like it applied.
      ...(isSandbox
        ? {
            test_command: testCommand,
            language: language || undefined,
            env_vars: Object.keys(envVarsObj).length > 0 ? envVarsObj : undefined,
          }
        : {}),
      // Absent, not empty-string, when the user left it blank — a public repo
      // must reach the backend with no credential at all.
      ...(trimmedToken ? { github_token: trimmedToken } : {}),
    });

    setSubmitting(false);

    // Drop the secret from component state as soon as it has been sent, whatever
    // the outcome: it is single-use, and holding it lets a later re-render or a
    // state dump surface it.
    setGithubToken('');

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
      <View className="flex-1 bg-codex-bg" testID="dev-sandbox-loading">
        <CodexHeader pageNumber="IV" totalPages="IV" />
        <View className="flex-row items-center px-6 pb-2 pt-3">
          <Pressable onPress={goBack} className="mr-3 p-1">
            <Text className="font-serif text-2xl text-codex-muted">{'←'}</Text>
          </Pressable>
          <Text className="font-serif-italic text-lg text-codex-text">Dev Sandbox Proof</Text>
        </View>
        <View className="px-6 pt-3">
          <View className="mb-6 h-7 w-3/4 rounded-sm bg-codex-border" />
          <View className="mb-4 h-20 rounded-sm bg-codex-surface" />
          <View className="mb-3 h-4 w-1/3 rounded-sm bg-codex-border" />
          <View className="mb-3 h-10 w-full rounded-sm bg-codex-surface" />
        </View>
      </View>
    );
  }

  if (error || !goal) {
    return (
      <View className="flex-1 bg-codex-bg">
        <CodexHeader pageNumber="IV" totalPages="IV" />
        <View className="flex-row items-center px-6 pb-2 pt-3">
          <Pressable onPress={goBack} className="mr-3 p-1">
            <Text className="font-serif text-2xl text-codex-muted">{'←'}</Text>
          </Pressable>
          <Text className="flex-1 font-serif-italic text-lg text-codex-text">Error</Text>
        </View>
        <View className="flex-1 items-center justify-center px-6">
          <Text className="mb-2 font-sans text-lg text-codex-accent">{error || 'Goal not found'}</Text>
          <CodexButton onPress={() => navigate({ name: 'home' })}>
            Go Home
          </CodexButton>
        </View>
      </View>
    );
  }

  return (
    <View className="flex-1 bg-codex-bg">
      <CodexHeader pageNumber="IV" totalPages="IV" />
      <View className="flex-row items-center px-6 pb-2 pt-3">
        <Pressable onPress={goBack} className="mr-3 p-1">
          <Text className="font-serif text-2xl text-codex-muted">{'←'}</Text>
        </Pressable>
        <Text className="flex-1 font-serif-italic text-lg text-codex-text" numberOfLines={1}>
          {isSandbox ? 'Dev Sandbox Proof' : 'Repository Proof'}
        </Text>
      </View>

      <ScrollView className="flex-1 px-6" showsVerticalScrollIndicator={false}>
        <SectionHeading
          number="The Witness — Code"
          title=""
          subtitle={
            isSandbox
              ? 'Submit your repository for judgment. Your code will be cloned, tests run, and authenticity verified.'
              : 'Submit your repository for judgment. Its commits, files and pull requests will be checked against your criteria.'
          }
        />

        <CodexCard className="mb-4 p-4">
          <Text className="font-serif text-lg text-codex-text">{goal.title}</Text>
          <Text className="mt-1 font-sans text-sm text-codex-muted">{goal.description}</Text>
          <Text className="mt-2 font-sans text-xs text-codex-muted">Deadline: {formatDateTime(goal.deadline)}</Text>
          {!!goal.criteria?.criteria_data.goal_description && (
            <Text className="mt-2 font-sans text-sm text-codex-muted">
              Goal: {String(goal.criteria.criteria_data.goal_description)}
            </Text>
          )}
        </CodexCard>

        {isDeadlinePassed && !isTerminal && (
          <CodexCard testID="deadline-passed-message" className="mb-4 border-codex-accent bg-codex-surface p-4">
            <Text className="font-sans text-sm text-codex-accent">
              Deadline has passed. You can no longer submit proof for this goal.
            </Text>
          </CodexCard>
        )}

        {isTerminal && verificationDetails ? (
          <View className="mb-6">
            {verificationStatus === 'verified' ? (
              <CodexCard testID="verification-verified" className="mb-4 border-codex-border p-4">
                <View className="mb-3 items-center">
                  <Text className="font-serif text-2xl text-codex-text">Verdict: True</Text>
                  <Text className="mt-1 font-sans text-sm text-codex-muted">Code verified</Text>
                </View>

                <CodexCard className="mb-3 bg-codex-bg p-3">
                  <Text testID="tests-passed-check" className="font-sans text-base text-codex-accent">
                    ✓ Tests Passed
                  </Text>
                </CodexCard>

                <CodexCard className="mb-3 bg-codex-bg p-3">
                  <Text testID="code-authentic-check" className="font-sans text-base text-codex-accent">
                    ✓ Code Authentic
                  </Text>
                </CodexCard>

                {(verificationDetails.llm_reasoning as string) && (
                  <CodexCard testID="llm-reasoning-section" className="bg-codex-bg p-3">
                    <Text className="mb-1 font-sans text-xs uppercase tracking-wider text-codex-muted">
                      LLM Reasoning
                    </Text>
                    <Text className="font-serif-italic text-sm text-codex-text">
                      {String(verificationDetails.llm_reasoning)}
                    </Text>
                  </CodexCard>
                )}
              </CodexCard>
            ) : (
              <CodexCard testID="verification-failed" className="mb-4 border-codex-accent p-4">
                <View className="mb-3 items-center">
                  <Text className="font-serif text-2xl text-codex-accent">Verdict: False</Text>
                  <Text className="mt-1 font-sans text-sm text-codex-muted">Verification failed</Text>
                </View>

                {verificationDetails.stage === 'clone' && (
                  <CodexCard testID="failed-stage-clone" className="mb-3 bg-codex-bg p-3">
                    <Text className="font-sans text-base text-codex-accent">Clone Failed</Text>
                    <Text className="mt-1 font-sans text-sm text-codex-muted">
                      {String(verificationDetails.error || 'Could not clone repository')}
                    </Text>
                  </CodexCard>
                )}

                {verificationDetails.stage === 'install' && (
                  <CodexCard testID="failed-stage-install" className="mb-3 bg-codex-bg p-3">
                    <Text className="font-sans text-base text-codex-accent">Install Failed</Text>
                    <Text className="mt-1 font-sans text-sm text-codex-muted">
                      {String(verificationDetails.error || 'Dependency installation failed')}
                    </Text>
                  </CodexCard>
                )}

                {verificationDetails.stage === 'test' && (
                  <CodexCard testID="failed-stage-test" className="mb-3 bg-codex-bg p-3">
                    <Text className="font-sans text-base text-codex-accent">Test Failed</Text>
                    <Text className="mt-1 font-sans text-sm text-codex-muted">
                      Exit code: {String(verificationDetails.exit_code ?? 'N/A')}
                    </Text>
                  </CodexCard>
                )}

                {verificationDetails.stage === 'sandbox' && (
                  <CodexCard testID="failed-stage-sandbox" className="mb-3 bg-codex-bg p-3">
                    <Text className="font-sans text-base text-codex-accent">
                      Sandbox Error
                    </Text>
                    <Text className="mt-1 font-sans text-sm text-codex-muted">
                      {String(
                        verificationDetails.error ||
                          'The sandbox could not run your tests. This is on our side — your pledge was not charged. Please retry.',
                      )}
                    </Text>
                  </CodexCard>
                )}

                {verificationDetails.stage === 'validation' && (
                  <CodexCard testID="failed-stage-validation" className="mb-3 bg-codex-bg p-3">
                    <Text className="font-sans text-base text-codex-accent">
                      Invalid Submission
                    </Text>
                    <Text className="mt-1 font-sans text-sm text-codex-muted">
                      {String(verificationDetails.error || 'Your test command could not be parsed.')}
                    </Text>
                  </CodexCard>
                )}

                {/* Any stage we do not have a card for (e.g. `unknown`) still has
                    to show its reason — a blank failure panel is unreadable, and
                    the user may have just been charged. */}
                {!['clone', 'install', 'test', 'sandbox', 'validation'].includes(
                  String(verificationDetails.stage),
                ) && (
                  <CodexCard testID="failed-stage-other" className="mb-3 bg-codex-bg p-3">
                    <Text className="font-sans text-base text-codex-accent">
                      Verification Error
                    </Text>
                    <Text className="mt-1 font-sans text-sm text-codex-muted">
                      {String(
                        verificationDetails.error ||
                          'Verification could not be completed. Please retry.',
                      )}
                    </Text>
                  </CodexCard>
                )}

                {(!!verificationDetails.stdout || !!verificationDetails.stderr) && (
                  <CodexCard testID="test-output-section" className="mb-3 bg-codex-bg p-3">
                    <Text className="mb-1 font-sans text-xs uppercase tracking-wider text-codex-muted">
                      Test Output
                    </Text>
                    <ScrollView testID="test-output-scroll" className="max-h-40" scrollEnabled={true}>
                      <Text className="font-mono text-xs text-codex-text">
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
                  </CodexCard>
                )}

                {(verificationDetails.llm_reasoning as string) && (
                  <CodexCard testID="llm-reasoning-section" className="mb-3 bg-codex-bg p-3">
                    <Text className="mb-1 font-sans text-xs uppercase tracking-wider text-codex-muted">
                      LLM Reasoning
                    </Text>
                    <Text className="font-serif-italic text-sm text-codex-text">
                      {String(verificationDetails.llm_reasoning)}
                    </Text>
                  </CodexCard>
                )}

                <CodexButton testID="retry-button" onPress={handleRetry} variant="secondary" className="mt-2">
                  Retry
                </CodexButton>
              </CodexCard>
            )}
          </View>
        ) : submitting || verificationStatus === 'pending' ? (
          <View className="mb-6 items-center py-8" testID="submission-loading">
            <ActivityIndicator size="large" color="#8A2A1C" />
            <Text className="mt-4 font-sans text-sm text-codex-muted">Processing verification...</Text>
            {verificationStatus === 'pending' && (
              <View testID="verification-pending" className="mt-2">
                <Text className="font-sans text-xs text-codex-muted">Verification in progress</Text>
              </View>
            )}
          </View>
        ) : (
          <>
            {!isDeadlinePassed && (
              <View className="mb-6">
                <CodexInput
                  testID="repo-url-input"
                  label="Repository to be examined"
                  value={repoUrl}
                  onChangeText={setRepoUrl}
                  placeholder="https://github.com/user/repo.git"
                  monospace
                />

                <CodexInput
                  testID="branch-input"
                  label="Branch"
                  value={branch}
                  onChangeText={setBranch}
                  placeholder="main"
                  monospace
                />

                <CodexInput
                  testID="github-token-input"
                  label="Access token (optional)"
                  value={githubToken}
                  onChangeText={setGithubToken}
                  placeholder="Only needed for a private repository"
                  secureTextEntry
                  monospace
                />
                <View testID="github-token-help" className="-mt-2 mb-4">
                  <Text className="font-sans text-xs text-codex-muted">
                    Leave this empty for a public repository. For a private one, paste a
                    GitHub personal access token with the <Text className="font-mono">repo</Text>{' '}
                    scope — that is the minimum needed to read it. It is stored encrypted,
                    used only to verify this goal, and never shown again.
                  </Text>
                </View>

                {isSandbox && (
                  <>
                    <CodexInput
                      testID="test-command-input"
                      label="Test invocation"
                      value={testCommand}
                      onChangeText={setTestCommand}
                      placeholder="python -m pytest -v"
                      monospace
                    />

                    <CodexInput
                      testID="language-input"
                      label="Language"
                      value={language}
                      onChangeText={setLanguage}
                      placeholder="python"
                    />
                  </>
                )}

                {isSandbox && (
                <View testID="env-vars-section" className="mb-4">
                  <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">
                    Environment Variables
                  </Text>
                  {envVars.map((row, index) => (
                    <View key={index} testID={`env-var-row-${index}`} className="mb-2 flex-row items-center">
                      <TextInput
                        testID={`env-var-key-${index}`}
                        className="mr-2 flex-1 rounded-sm border border-codex-border bg-codex-surface px-3 py-2 font-mono text-sm text-codex-text"
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
                        className="mr-2 flex-1 rounded-sm border border-codex-border bg-codex-surface px-3 py-2 font-mono text-sm text-codex-text"
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
                        className="rounded-sm bg-codex-accent px-2 py-1"
                      >
                        <Text className="font-sans text-sm text-codex-surface">✕</Text>
                      </Pressable>
                    </View>
                  ))}
                  <Pressable
                    testID="add-env-var-button"
                    onPress={() => setEnvVars([...envVars, { key: '', value: '' }])}
                    className="items-center rounded-sm border border-dashed border-codex-border bg-codex-surface py-3"
                  >
                    <Text className="font-sans text-xs uppercase tracking-wider text-codex-accent">+ Add Variable</Text>
                  </Pressable>
                </View>
                )}

                {apiError && (
                  <CodexCard className="mb-4 border-codex-accent bg-codex-surface p-3">
                    <Text className="font-sans text-sm text-codex-accent">{apiError}</Text>
                  </CodexCard>
                )}

                <CodexButton
                  testID="submit-proof-button"
                  onPress={handleSubmit}
                  className="mb-6"
                >
                  {submitting ? 'Submitting...' : 'Submit for Judgement ↳'}
                </CodexButton>
              </View>
            )}
          </>
        )}
      </ScrollView>

      <CodexFooter />
    </View>
  );
}
