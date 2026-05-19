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
import { useAuth } from '../hooks/useAuth';
import { useNavigation } from '../hooks/useNavigation';

type GoalType = 'youtube_video' | 'api_endpoint' | 'dev_sandbox';

interface FormState {
  title: string;
  description: string;
  deadline: string;
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
}

interface ValidationErrors {
  [key: string]: string;
}

const INITIAL_FORM: FormState = {
  title: '',
  description: '',
  deadline: '',
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
};

export default function GoalCreateScreen() {
  const { navigate } = useNavigation();
  const { user } = useAuth();
  const [form, setForm] = useState<FormState>(INITIAL_FORM);
  const [errors, setErrors] = useState<ValidationErrors>({});
  const [apiError, setApiError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const charityTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (charityTimeoutRef.current) clearTimeout(charityTimeoutRef.current);
    };
  }, []);

  const FIELD_TO_ERROR_KEY: Record<string, string> = {
    title: 'title-input',
    deadline: 'deadline-input',
    pledge_amount: 'pledge-amount-input',
    youtube_min_duration: 'min-duration-input',
    api_url: 'api-url-input',
    sandbox_repo_url: 'sandbox-repo-url-input',
    sandbox_test_command: 'sandbox-test-command-input',
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
    if (!query.trim()) {
      updateField('charity_results', []);
      return;
    }
    updateField('charity_searching', true);
    const result = await api.searchCharities(query);
    if (result.data) {
      updateField('charity_results', result.data);
    }
    updateField('charity_searching', false);
  }, [updateField]);

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
    if (!form.deadline.trim()) newErrors['deadline-input'] = 'Deadline is required';
    if (!form.pledge_amount.trim()) newErrors['pledge-amount-input'] = 'Pledge amount is required';
    else if (isNaN(Number(form.pledge_amount)) || Number(form.pledge_amount) <= 0) {
      newErrors['pledge-amount-input'] = 'Must be a positive number';
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
      }

      const payload = {
        title: form.title.trim(),
        description: form.description.trim(),
        deadline: form.deadline,
        pledge_amount: Number(form.pledge_amount),
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

  const renderGoalTypeButton = (label: string, value: GoalType) => (
    <Pressable
      key={value}
      className={`flex-1 rounded-lg px-3 py-2.5 ${form.goal_type === value ? 'bg-indigo-600' : 'bg-gray-100'}`}
      onPress={() => updateField('goal_type', value)}
    >
      <Text
        className={`text-center text-xs font-medium ${form.goal_type === value ? 'text-white' : 'text-gray-700'}`}
      >
        {label}
      </Text>
    </Pressable>
  );

  const renderField = (
    testId: string,
    label: string,
    value: string,
    onChangeText: (text: string) => void,
    opts?: { multiline?: boolean; placeholder?: string; keyboardType?: 'default' | 'numeric'; rows?: number },
  ) => (
    <View className="mb-4">
      <Text className="mb-1.5 text-sm font-medium text-gray-700">{label}</Text>
      <TextInput
        testID={testId}
        className={`rounded-xl border ${errors[testId] ? 'border-red-400' : 'border-gray-300'} bg-white px-4 py-3 text-base text-gray-900 ${opts?.multiline ? 'min-h-[80px] text-left' : ''}`}
        value={value}
        onChangeText={onChangeText}
        placeholder={opts?.placeholder || ''}
        placeholderTextColor="#9CA3AF"
        keyboardType={opts?.keyboardType || 'default'}
        multiline={opts?.multiline}
        textAlignVertical={opts?.multiline ? 'top' : 'center'}
      />
      {errors[testId] && (
        <Text className="mt-1 text-xs text-red-500">{errors[testId]}</Text>
      )}
    </View>
  );

  return (
    <View className="flex-1 bg-white">
      <View className="flex-row items-center px-4 pt-14 pb-2">
        <Pressable onPress={() => navigate({ name: 'home' })} className="mr-3 p-1">
          <Text className="text-2xl text-gray-600">{'<'}</Text>
        </Pressable>
        <Text className="text-xl font-bold text-gray-900">Create Goal</Text>
      </View>

      <ScrollView className="flex-1 px-4" keyboardShouldPersistTaps="handled">
        {renderField('title-input', 'Title', form.title, (t) => updateField('title', t), {
          placeholder: 'What do you want to accomplish?',
        })}

        {renderField('description-input', 'Description', form.description, (t) => updateField('description', t), {
          placeholder: 'Describe your goal in detail...',
          multiline: true,
        })}

        {renderField('deadline-input', 'Deadline', form.deadline, (t) => updateField('deadline', t), {
          placeholder: '2026-06-01T00:00:00Z',
        })}

        {renderField('pledge-amount-input', 'Pledge Amount (cents)', form.pledge_amount, (t) => updateField('pledge_amount', t), {
          placeholder: '5000 ($50.00)',
          keyboardType: 'numeric',
        })}

        <Text className="mb-1.5 text-sm font-medium text-gray-700">Verification Type</Text>
        <View className="mb-4 flex-row gap-2">
          {renderGoalTypeButton('YouTube Video', 'youtube_video')}
          {renderGoalTypeButton('API Endpoint', 'api_endpoint')}
          {renderGoalTypeButton('Dev Sandbox', 'dev_sandbox')}
        </View>

        {/* YouTube sub-form */}
        {form.goal_type === 'youtube_video' && (
          <View className="mb-2 rounded-xl border border-indigo-100 bg-indigo-50 p-4">
            {renderField('min-duration-input', 'Minimum Duration (seconds)', form.youtube_min_duration, (t) => updateField('youtube_min_duration', t), {
              placeholder: '300',
              keyboardType: 'numeric',
            })}
            {renderField('video-description-input', 'Video Description', form.youtube_video_description, (t) => updateField('youtube_video_description', t), {
              placeholder: 'Describe what the video should cover...',
              multiline: true,
            })}
          </View>
        )}

        {/* API Endpoint sub-form */}
        {form.goal_type === 'api_endpoint' && (
          <View className="mb-2 rounded-xl border border-indigo-100 bg-indigo-50 p-4">
            {renderField('api-url-input', 'URL', form.api_url, (t) => updateField('api_url', t), {
              placeholder: 'https://example.com/api/health',
            })}
            {renderField('api-method-input', 'HTTP Method', form.api_method, (t) => updateField('api_method', t), {
              placeholder: 'GET',
            })}
            {renderField('api-headers-input', 'Headers (JSON)', form.api_headers, (t) => updateField('api_headers', t), {
              placeholder: '{"Authorization": "Bearer ..."}',
              multiline: true,
              rows: 2,
            })}
            {renderField('api-expected-status-input', 'Expected Status Code', form.api_expected_status, (t) => updateField('api_expected_status', t), {
              placeholder: '200',
              keyboardType: 'numeric',
            })}
            {renderField('api-expected-body-input', 'Expected Body Schema (JSON)', form.api_expected_body, (t) => updateField('api_expected_body', t), {
              placeholder: '{"type": "object"}',
              multiline: true,
              rows: 3,
            })}
          </View>
        )}

        {/* Dev Sandbox sub-form */}
        {form.goal_type === 'dev_sandbox' && (
          <View className="mb-2 rounded-xl border border-indigo-100 bg-indigo-50 p-4">
            {renderField('sandbox-repo-url-input', 'Git Repo URL', form.sandbox_repo_url, (t) => updateField('sandbox_repo_url', t), {
              placeholder: 'https://github.com/user/repo.git',
            })}
            {renderField('sandbox-branch-input', 'Branch', form.sandbox_branch, (t) => updateField('sandbox_branch', t), {
              placeholder: 'main',
            })}
            {renderField('sandbox-test-command-input', 'Test Command', form.sandbox_test_command, (t) => updateField('sandbox_test_command', t), {
              placeholder: 'pytest tests/ -v',
            })}
            {renderField('sandbox-goal-description-input', 'Goal Description for LLM', form.sandbox_goal_description, (t) => updateField('sandbox_goal_description', t), {
              placeholder: 'Describe what the code should do...',
              multiline: true,
            })}
          </View>
        )}

        {/* Charity Search */}
        <View className="mb-4">
          <Text className="mb-1.5 text-sm font-medium text-gray-700">Charity</Text>
          <TextInput
            testID="charity-search-input"
            className="rounded-xl border border-gray-300 bg-white px-4 py-3 text-base text-gray-900"
            value={form.charity_name}
            onChangeText={handleCharitySearch}
            placeholder="Search for a charity..."
            placeholderTextColor="#9CA3AF"
          />
          {form.charity_searching && (
            <ActivityIndicator size="small" color="#4F46E5" className="mt-2" />
          )}
          {form.charity_results.length > 0 && (
            <View className="mt-1 rounded-xl border border-gray-200 bg-white">
              {form.charity_results.map((c) => (
                <Pressable
                  key={c.id}
                  className={`px-4 py-3 ${form.charity_id === c.stripe_connect_id ? 'bg-indigo-50' : ''}`}
                  onPress={() => selectCharity(c)}
                >
                  <Text className="text-sm text-gray-800">{c.name}</Text>
                </Pressable>
              ))}
            </View>
          )}
          {form.charity_name && !form.charity_searching && form.charity_results.length === 0 && (
            <Text className="mt-1 text-xs text-gray-400">No charities found</Text>
          )}
        </View>

        {apiError && (
          <View className="mb-4 rounded-xl border border-red-200 bg-red-50 p-3">
            <Text className="text-sm text-red-700">{apiError}</Text>
          </View>
        )}

        <Pressable
          testID="submit-goal-button"
          className={`mb-10 items-center rounded-xl py-3.5 ${submitting ? 'bg-indigo-400' : 'bg-indigo-600'}`}
          onPress={handleSubmit}
          disabled={submitting}
        >
          {submitting ? (
            <ActivityIndicator size="small" color="#FFFFFF" />
          ) : (
            <Text className="text-base font-semibold text-white">Create Goal</Text>
          )}
        </Pressable>
      </ScrollView>
    </View>
  );
}
