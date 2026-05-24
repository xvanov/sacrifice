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
import { DatePickerField } from '../components/DatePickerField';
import { TimePickerField } from '../components/TimePickerField';
import { api } from '../services/api';
import { useAuth } from '../hooks/useAuth';
import { useNavigation } from '../hooks/useNavigation';

type GoalType = 'youtube_video' | 'api_endpoint' | 'dev_sandbox' | 'github_repo';

interface FormState {
  title: string;
  deadlineDate: Date;
  deadlineTime: Date;
  pledge_amount: string;
  goal_type: GoalType;
  charity_id: string;
  charity_name: string;
  charity_results: Array<{ id: string; name: string; stripe_connect_id: string }>;
  charity_searching: boolean;

  youtube_min_duration: string;
  youtube_video_description: string;

  api_url: string;
  api_method: string;
  api_headers: string;
  api_expected_status: string;
  api_expected_body: string;

  sandbox_repo_url: string;
  sandbox_branch: string;
  sandbox_test_command: string;
  sandbox_goal_description: string;

  github_repo_url: string;
  github_branch: string;
  github_file_path: string;
}

interface ValidationErrors {
  [key: string]: string;
}

function defaultDeadlineDate(): Date {
  const d = new Date();
  d.setDate(d.getDate() + 7);
  d.setHours(23, 59, 0, 0);
  return d;
}

function defaultDeadlineTime(): Date {
  const d = new Date();
  d.setHours(23, 59, 0, 0);
  return d;
}

const INITIAL_FORM: FormState = {
  title: '',
  deadlineDate: defaultDeadlineDate(),
  deadlineTime: defaultDeadlineTime(),
  pledge_amount: '',
  goal_type: 'youtube_video',
  charity_id: '',
  charity_name: '',
  charity_results: [],
  charity_searching: false,

  youtube_min_duration: '',
  youtube_video_description: '',

  api_url: '',
  api_method: 'GET',
  api_headers: '',
  api_expected_status: '200',
  api_expected_body: '{}',

  sandbox_repo_url: '',
  sandbox_branch: 'main',
  sandbox_test_command: '',
  sandbox_goal_description: '',

  github_repo_url: '',
  github_branch: 'main',
  github_file_path: '',
};

function combineDateAndTime(date: Date, time: Date): Date {
  return new Date(
    date.getFullYear(),
    date.getMonth(),
    date.getDate(),
    time.getHours(),
    time.getMinutes(),
    time.getSeconds(),
  );
}

type GoalTypeMeta = {
  label: string;
  value: GoalType;
  description: string;
};

const GOAL_TYPES: GoalTypeMeta[] = [
  { label: 'Video', value: 'youtube_video', description: 'Recorded testimony' },
  { label: 'API', value: 'api_endpoint', description: 'Living endpoint' },
  { label: 'Sandbox', value: 'dev_sandbox', description: 'Code repository' },
  { label: 'GitHub', value: 'github_repo', description: 'Repository conditions' },
];

const GOAL_TYPE_LABEL: Record<GoalType, string> = {
  youtube_video: 'Video',
  api_endpoint: 'API',
  dev_sandbox: 'Sandbox',
  github_repo: 'GitHub',
};

export default function GoalCreateScreen() {
  const { navigate } = useNavigation();
  const { user } = useAuth();
  const [form, setForm] = useState<FormState>(INITIAL_FORM);
  const [errors, setErrors] = useState<ValidationErrors>({});
  const [apiError, setApiError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [hasPaymentMethod, setHasPaymentMethod] = useState<boolean | null>(null);
  const charityTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (charityTimeoutRef.current) clearTimeout(charityTimeoutRef.current);
    };
  }, []);

  useEffect(() => {
    api.getPaymentMethods().then((result) => {
      setHasPaymentMethod(!!(result.data && result.data.length > 0));
    });
  }, []);

  const FIELD_TO_ERROR_KEY: Record<string, string> = {
    title: 'title-input',
    pledge_amount: 'pledge-amount-input',
    youtube_min_duration: 'min-duration-input',
    api_url: 'api-url-input',
    sandbox_repo_url: 'sandbox-repo-url-input',
    sandbox_test_command: 'sandbox-test-command-input',
    github_repo_url: 'github-repo-url-input',
  };

  const updateField = useCallback(<K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setErrors((prev) => {
      const next = { ...prev };
      const errorKey = FIELD_TO_ERROR_KEY[key as string] || (key as string);
      delete next[errorKey];
      return next;
    });
    setApiError(null);
  }, []);

  const searchCharities = useCallback(async (query: string) => {
    updateField('charity_searching', true);
    const result = await api.searchCharities(query);
    if (result.data) {
      updateField('charity_results', result.data);
    }
    updateField('charity_searching', false);
  }, [updateField]);

  useEffect(() => {
    searchCharities('');
  }, [searchCharities]);

  const handleCharitySearch = useCallback((text: string) => {
    updateField('charity_name', text);
    updateField('charity_id', '');
    if (charityTimeoutRef.current) clearTimeout(charityTimeoutRef.current);
    charityTimeoutRef.current = setTimeout(() => searchCharities(text), 300);
  }, [updateField, searchCharities]);

  const selectCharity = useCallback((charity: { id: string; name: string; stripe_connect_id: string }) => {
    updateField('charity_name', charity.name);
    updateField('charity_id', charity.stripe_connect_id);
    updateField('charity_results', []);
  }, [updateField]);

  const validate = useCallback((): boolean => {
    const newErrors: ValidationErrors = {};
    if (!form.title.trim()) newErrors['title-input'] = 'Title is required';
    if (!form.pledge_amount.trim()) newErrors['pledge-amount-input'] = 'Pledge amount is required';
    else {
      const amount = parseFloat(form.pledge_amount);
      if (isNaN(amount) || amount <= 0) {
        newErrors['pledge-amount-input'] = 'Must be a positive amount';
      }
    }
    if (form.goal_type === 'youtube_video') {
      const dur = Number(form.youtube_min_duration);
      if (!form.youtube_min_duration.trim() || isNaN(dur) || dur < 0) {
        newErrors['min-duration-input'] = 'Minimum duration is required';
      }
    }
    if (form.goal_type === 'api_endpoint' && !form.api_url.trim()) {
      newErrors['api-url-input'] = 'API URL is required';
    }
    if (form.goal_type === 'dev_sandbox') {
      if (!form.sandbox_repo_url.trim()) newErrors['sandbox-repo-url-input'] = 'Repo URL is required';
      if (!form.sandbox_test_command.trim()) newErrors['sandbox-test-command-input'] = 'Test command is required';
    }
    if (form.goal_type === 'github_repo' && !form.github_repo_url.trim()) {
      newErrors['github-repo-url-input'] = 'Repo URL is required';
    }
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  }, [form]);

  const handleSubmit = useCallback(async () => {
    setApiError(null);
    if (!validate()) return;

    setSubmitting(true);
    try {
      let criteria: Record<string, unknown> = {};
      if (form.goal_type === 'youtube_video') {
        criteria = {
          min_duration_seconds: Number(form.youtube_min_duration),
          video_description: form.youtube_video_description,
        };
      } else if (form.goal_type === 'api_endpoint') {
        let parsedBody: Record<string, unknown> = {};
        try { parsedBody = JSON.parse(form.api_expected_body || '{}'); } catch { parsedBody = {}; }
        let parsedHeaders: Record<string, string> = {};
        try { parsedHeaders = JSON.parse(form.api_headers || '{}'); } catch { parsedHeaders = {}; }
        criteria = {
          url: form.api_url,
          method: form.api_method,
          headers: parsedHeaders,
          expected_status: Number(form.api_expected_status) || 200,
          expected_body_schema: parsedBody,
        };
      } else if (form.goal_type === 'dev_sandbox') {
        criteria = {
          repo_url: form.sandbox_repo_url,
          branch: form.sandbox_branch,
          test_command: form.sandbox_test_command,
          goal_description: form.sandbox_goal_description,
        };
      } else if (form.goal_type === 'github_repo') {
        criteria = {
          repo_url: form.github_repo_url,
          branch: form.github_branch,
          file_path: form.github_file_path,
        };
      }

      const deadline = combineDateAndTime(form.deadlineDate, form.deadlineTime);

      const payload = {
        title: form.title.trim(),
        deadline: deadline.toISOString(),
        pledge_amount: Math.round(parseFloat(form.pledge_amount) * 100),
        goal_type: form.goal_type,
        criteria,
        charity_id: form.charity_id || null,
      };

      const result = await api.createGoal(payload);

      if (result.data && result.data.id) {
        navigate({ name: 'goal-detail', goalId: result.data.id });
      } else if (result.error) {
        try {
          const parsed = JSON.parse(result.error.replace(/^HTTP \d+: /, ''));
          if (parsed.detail) {
            const msgs = parsed.detail
              .map((d: { msg?: string }) => d.msg)
              .filter(Boolean);
            if (msgs.length > 0) setApiError(msgs.join(', '));
            else setApiError('Validation failed');
          } else {
            setApiError(result.error);
          }
        } catch {
          setApiError(result.error);
        }
      }
    } catch (err) {
      setApiError(err instanceof Error ? err.message : 'Submission failed');
    } finally {
      setSubmitting(false);
    }
  }, [form, validate, navigate]);

  const renderDatePicker = () => (
    <View className="mb-4 flex-row gap-2">
      <View className="flex-1">
        <DatePickerField
          value={form.deadlineDate}
          onChange={(d) => updateField('deadlineDate', d)}
          error={errors['deadline-date-input']}
        />
      </View>
      <View className="flex-1">
        <TimePickerField
          value={form.deadlineTime}
          onChange={(d) => updateField('deadlineTime', d)}
          error={errors['deadline-time-input']}
        />
      </View>
    </View>
  );

  return (
    <View className="flex-1 bg-codex-bg">
      <CodexHeader pageNumber="I" totalPages="IV" />
      <View className="flex-row items-center px-6 pb-2 pt-3">
        <Pressable onPress={() => navigate({ name: 'home' })} className="p-1">
          <Text className="font-serif text-2xl text-codex-muted">{'←'}</Text>
        </Pressable>
      </View>

      <ScrollView className="flex-1 px-6" keyboardShouldPersistTaps="handled">
        <CodexCard className="mb-6 p-4">
          <CodexInput
            testID="title-input"
            label="What you will make"
            value={form.title}
            onChangeText={(t) => updateField('title', t)}
            placeholder="A brief description, for the witness"
            error={errors['title-input']}
          />

          {renderDatePicker()}

          <CodexInput
            testID="pledge-amount-input"
            label="Amount at stake"
            value={form.pledge_amount}
            onChangeText={(t) => updateField('pledge_amount', t)}
            placeholder="50.00"
            keyboardType="numeric"
            error={errors['pledge-amount-input']}
          />
        </CodexCard>

        <CodexCard className="mb-6 p-4">
          <View className="mb-4">
            <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">
              Verification type
            </Text>
            <View className="flex-row gap-2">
              {GOAL_TYPES.map((gt) => (
                <Pressable
                  key={gt.value}
                  className={`flex-1 rounded-sm px-3 py-2.5 ${form.goal_type === gt.value ? 'bg-codex-accent' : 'border border-codex-border bg-codex-surface'}`}
                  onPress={() => updateField('goal_type', gt.value)}
                >
                  <Text
                    className={`text-center font-sans text-xs uppercase tracking-wider ${form.goal_type === gt.value ? 'text-codex-surface' : 'text-codex-muted'}`}
                  >
                    {gt.label}
                  </Text>
                </Pressable>
              ))}
            </View>
          </View>

          {form.goal_type === 'youtube_video' && (
            <View>
              <CodexInput
                testID="min-duration-input"
                label="Minimum duration (seconds)"
                value={form.youtube_min_duration}
                onChangeText={(t) => updateField('youtube_min_duration', t)}
                placeholder="300"
                keyboardType="numeric"
                error={errors['min-duration-input']}
              />
              <CodexInput
                testID="video-description-input"
                label="Video description"
                value={form.youtube_video_description}
                onChangeText={(t) => updateField('youtube_video_description', t)}
                placeholder="Describe what the video should cover..."
                multiline
              />
            </View>
          )}

          {form.goal_type === 'api_endpoint' && (
            <View>
              <CodexInput
                testID="api-url-input"
                label="URL"
                value={form.api_url}
                onChangeText={(t) => updateField('api_url', t)}
                placeholder="https://example.com/api/health"
                error={errors['api-url-input']}
              />
              <CodexInput
                testID="api-method-input"
                label="HTTP Method"
                value={form.api_method}
                onChangeText={(t) => updateField('api_method', t)}
                placeholder="GET"
              />
              <CodexInput
                testID="api-headers-input"
                label="Headers (JSON)"
                value={form.api_headers}
                onChangeText={(t) => updateField('api_headers', t)}
                placeholder='{"Authorization": "Bearer ..."}'
                multiline
              />
              <CodexInput
                testID="api-expected-status-input"
                label="Expected status code"
                value={form.api_expected_status}
                onChangeText={(t) => updateField('api_expected_status', t)}
                placeholder="200"
                keyboardType="numeric"
              />
              <CodexInput
                testID="api-expected-body-input"
                label="Expected body schema (JSON)"
                value={form.api_expected_body}
                onChangeText={(t) => updateField('api_expected_body', t)}
                placeholder='{"type": "object"}'
                multiline
              />
            </View>
          )}

          {form.goal_type === 'dev_sandbox' && (
            <View>
              <CodexInput
                testID="sandbox-repo-url-input"
                label="Git repo URL"
                value={form.sandbox_repo_url}
                onChangeText={(t) => updateField('sandbox_repo_url', t)}
                placeholder="https://github.com/user/repo.git"
                error={errors['sandbox-repo-url-input']}
                monospace
              />
              <CodexInput
                testID="sandbox-branch-input"
                label="Branch"
                value={form.sandbox_branch}
                onChangeText={(t) => updateField('sandbox_branch', t)}
                placeholder="main"
                monospace
              />
              <CodexInput
                testID="sandbox-test-command-input"
                label="Test command"
                value={form.sandbox_test_command}
                onChangeText={(t) => updateField('sandbox_test_command', t)}
                placeholder="pytest tests/ -v"
                error={errors['sandbox-test-command-input']}
                monospace
              />
              <CodexInput
                testID="sandbox-goal-description-input"
                label="Goal description for LLM"
                value={form.sandbox_goal_description}
                onChangeText={(t) => updateField('sandbox_goal_description', t)}
                placeholder="Describe what the code should do..."
                multiline
              />
            </View>
          )}

          {form.goal_type === 'github_repo' && (
            <View>
              <CodexInput
                testID="github-repo-url-input"
                label="GitHub repo URL"
                value={form.github_repo_url}
                onChangeText={(t) => updateField('github_repo_url', t)}
                placeholder="https://github.com/user/repo"
                error={errors['github-repo-url-input']}
                monospace
              />
              <CodexInput
                testID="github-branch-input"
                label="Branch"
                value={form.github_branch}
                onChangeText={(t) => updateField('github_branch', t)}
                placeholder="main"
                monospace
              />
              <CodexInput
                testID="github-file-path-input"
                label="File path to verify (optional)"
                value={form.github_file_path}
                onChangeText={(t) => updateField('github_file_path', t)}
                placeholder="src/main.py"
                monospace
              />
            </View>
          )}
        </CodexCard>

        <CodexCard className="mb-6 p-4">
          <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">
            Charity
          </Text>
          <TextInput
            testID="charity-search-input"
            className="rounded-sm border border-codex-border bg-codex-surface px-4 py-3 font-sans text-base text-codex-text"
            value={form.charity_name}
            onChangeText={handleCharitySearch}
            placeholder="Search for a charity..."
            placeholderTextColor="#85796A"
          />
          {form.charity_searching && (
            <ActivityIndicator size="small" color="#8A2A1C" className="mt-2" />
          )}
          {form.charity_results.length > 0 && form.charity_name === '' && (
            <View className="mt-1 max-h-48 rounded-sm border border-codex-border bg-codex-surface">
              <ScrollView>
                {form.charity_results.map((c) => (
                  <Pressable
                    key={c.id}
                    className={`px-4 py-3 ${form.charity_id === c.stripe_connect_id ? 'bg-codex-bg' : ''}`}
                    onPress={() => selectCharity(c)}
                  >
                    <Text className="font-sans text-sm text-codex-text">{c.name}</Text>
                  </Pressable>
                ))}
              </ScrollView>
            </View>
          )}
          {form.charity_results.length > 0 && form.charity_name !== '' && (
            <View className="mt-1 max-h-48 rounded-sm border border-codex-border bg-codex-surface">
              <ScrollView>
                {form.charity_results.map((c) => (
                  <Pressable
                    key={c.id}
                    className={`px-4 py-3 ${form.charity_id === c.stripe_connect_id ? 'bg-codex-bg' : ''}`}
                    onPress={() => selectCharity(c)}
                  >
                    <Text className="font-sans text-sm text-codex-text">{c.name}</Text>
                  </Pressable>
                ))}
              </ScrollView>
            </View>
          )}
          {form.charity_name && !form.charity_searching && form.charity_results.length === 0 && (
            <Text className="mt-1 font-sans text-xs text-codex-muted">No charities found</Text>
          )}
        </CodexCard>

        {apiError && (
          <View className="mb-4 rounded-sm border border-codex-accent bg-codex-surface p-3">
            <Text className="font-sans text-sm text-codex-accent">{apiError}</Text>
          </View>
        )}

        {hasPaymentMethod === false && (
          <View className="mb-4 rounded-sm border border-codex-border bg-codex-bg p-3">
            <Text className="font-sans text-xs text-codex-muted">
              No payment method on file. Your goal will be created but no pledge will be charged until a card is added.
            </Text>
          </View>
        )}

        <View className="flex-row items-center justify-end pb-6">
          <CodexButton
            testID="submit-goal-button"
            onPress={handleSubmit}
            loading={submitting}
            disabled={submitting}
          >
            Set Goal
          </CodexButton>
        </View>
      </ScrollView>

      <CodexFooter />
    </View>
  );
}
