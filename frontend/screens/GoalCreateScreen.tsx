import { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  Text,
  TextInput,
  View,
} from 'react-native';
import { CodexButton } from '../components/CodexButton';
import { DatePickerField } from '../components/DatePickerField';
import {
  typeLabel,
  setDynamicTypeLabels,
} from '../components/StatusBadge';
import { api } from '../services/api';
import type { Charity } from '../types';

type GoalTypeOption = {
  name: string;
  description: string;
  criteria_schema: Record<string, unknown>;
};

type FieldErrors = Record<string, string>;

function buildLabels(types: GoalTypeOption[]) {
  const full: Record<string, string> = {};
  const short: Record<string, string> = {};
  for (const t of types) {
    short[t.name] = t.name
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase());
    full[t.name] = t.description;
  }
  return { full, short };
}

export default function GoalCreateScreen() {
  const [goalTypes, setGoalTypes] = useState<GoalTypeOption[]>([]);
  const [loadingTypes, setLoadingTypes] = useState(true);
  const [selectedType, setSelectedType] = useState('');

  // Form fields
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [deadline, setDeadline] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() + 7);
    return d;
  });
  const [pledgeAmount, setPledgeAmount] = useState('');
  const [charityId, setCharityId] = useState('');
  const [charities, setCharities] = useState<Charity[]>([]);
  const [charitySearch, setCharitySearch] = useState('');
  const [selectedCharityName, setSelectedCharityName] = useState('');
  const [searching, setSearching] = useState(false);

  // Conditional criteria
  const [youtubeDuration, setYoutubeDuration] = useState('');
  const [youtubeDesc, setYoutubeDesc] = useState('');
  const [apiUrl, setApiUrl] = useState('');
  const [apiMethod, setApiMethod] = useState('GET');
  const [apiHeaders, setApiHeaders] = useState('');
  const [apiExpectedStatus, setApiExpectedStatus] = useState('');
  const [apiExpectedBody, setApiExpectedBody] = useState('');
  const [sandboxRepoUrl, setSandboxRepoUrl] = useState('');
  const [sandboxBranch, setSandboxBranch] = useState('');
  const [sandboxTestCommand, setSandboxTestCommand] = useState('');
  const [sandboxGoalDesc, setSandboxGoalDesc] = useState('');
  const [githubRepoUrl, setGithubRepoUrl] = useState('');
  const [githubBranch, setGithubBranch] = useState('');

  const [submitting, setSubmitting] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [serverError, setServerError] = useState<string | null>(null);

  const fetchGoalTypes = useCallback(async () => {
    setLoadingTypes(true);
    try {
      const res = await api.listGoalTypes();
      if (res.data?.goal_types) {
        const types = res.data.goal_types;
        setGoalTypes(types);
        setDynamicTypeLabels(buildLabels(types));
      }
    } catch {
      // Non-critical — fallback labels from StatusBadge handle display.
    } finally {
      setLoadingTypes(false);
    }
  }, []);

  useEffect(() => {
    fetchGoalTypes();
  }, [fetchGoalTypes]);

  const searchCharities = useCallback(async (q: string) => {
    setCharitySearch(q);
    if (q.trim().length < 2) {
      setCharities([]);
      return;
    }
    setSearching(true);
    try {
      const res = await api.searchCharities(q.trim());
      if (res.data) setCharities(res.data);
    } catch {
      // Non-critical
    } finally {
      setSearching(false);
    }
  }, []);

  const selectCharity = useCallback((c: Charity) => {
    setCharityId(c.id);
    setSelectedCharityName(c.name);
    setCharities([]);
    setCharitySearch('');
  }, []);

  const validate = useCallback((): FieldErrors => {
    const errs: FieldErrors = {};
    if (!title.trim()) errs['title'] = 'Title is required';
    if (!deadline) errs['deadline'] = 'Deadline is required';
    if (!pledgeAmount || Number.isNaN(Number(pledgeAmount)) || Number(pledgeAmount) <= 0) {
      errs['pledge_amount'] = 'Pledge must be a positive number';
    }
    if (!selectedType) errs['goal_type'] = 'Select a goal type';
    if (selectedType === 'youtube_video') {
      if (!youtubeDuration || Number.isNaN(Number(youtubeDuration)) || Number(youtubeDuration) <= 0) {
        errs['youtube_duration'] = 'Valid duration is required';
      }
    }
    if (selectedType === 'api_endpoint') {
      if (!apiUrl.trim()) errs['api_url'] = 'URL is required';
    }
    if (selectedType === 'dev_sandbox') {
      if (!sandboxRepoUrl.trim()) errs['sandbox_repo_url'] = 'Repo URL is required';
    }
    if (selectedType === 'github_repo') {
      if (!githubRepoUrl.trim()) errs['github_repo_url'] = 'Repo URL is required';
    }
    return errs;
  }, [title, deadline, pledgeAmount, selectedType, youtubeDuration, apiUrl, sandboxRepoUrl, githubRepoUrl]);

  const buildCriteria = useCallback(() => {
    switch (selectedType) {
      case 'youtube_video':
        return {
          criteria_type: 'youtube_video',
          criteria_data: {
            min_duration_seconds: Number(youtubeDuration) || 30,
            video_description: youtubeDesc,
          },
        };
      case 'api_endpoint': {
        const headersObj: Record<string, string> = {};
        if (apiHeaders.trim()) {
          apiHeaders.split(',').forEach((h) => {
            const [k, v] = h.split(':').map((s) => s.trim());
            if (k && v) headersObj[k] = v;
          });
        }
        return {
          criteria_type: 'api_endpoint',
          criteria_data: {
            method: apiMethod || 'GET',
            url: apiUrl,
            headers: headersObj,
            expected_status: Number(apiExpectedStatus) || 200,
            expected_body_schema: apiExpectedBody
              ? (() => { try { return JSON.parse(apiExpectedBody); } catch { return {}; } })()
              : {},
          },
        };
      }
      case 'dev_sandbox':
        return {
          criteria_type: 'dev_sandbox',
          criteria_data: {
            repo_url: sandboxRepoUrl,
            branch: sandboxBranch || 'main',
            test_command: sandboxTestCommand,
            language: 'python',
            env_vars: {},
            goal_description: sandboxGoalDesc,
          },
        };
      case 'github_repo':
        return {
          criteria_type: 'github_repo',
          criteria_data: {
            repo_url: githubRepoUrl,
            branch: githubBranch || 'main',
          },
        };
      default:
        return { criteria_type: selectedType, criteria_data: {} };
    }
  }, [selectedType, youtubeDuration, youtubeDesc, apiUrl, apiMethod, apiHeaders, apiExpectedStatus, apiExpectedBody, sandboxRepoUrl, sandboxBranch, sandboxTestCommand, sandboxGoalDesc, githubRepoUrl, githubBranch]);

  const handleSubmit = useCallback(async () => {
    const errs = validate();
    setErrors(errs);
    if (Object.keys(errs).length > 0) return;

    setSubmitting(true);
    setServerError(null);

    const timezone = (() => {
      try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; }
      catch { return 'UTC'; }
    })();

    const payload = {
      title: title.trim(),
      description: description.trim() || undefined,
      deadline: deadline.toISOString(),
      pledge_amount: Math.round(Number(pledgeAmount) * 100),
      goal_type: selectedType,
      criteria: buildCriteria(),
      charity_id: charityId || undefined,
      timezone,
      recurrence: 'none' as const,
      currency: 'usd' as const,
    };

    try {
      const res = await api.createGoal(payload);
      if (res.error) {
        setServerError(res.error);
      } else {
        // Success — goal creation triggers navigation elsewhere via parent.
        // For now, clear form and show success message.
        setTitle('');
        setDescription('');
        setPledgeAmount('');
        setSelectedType('');
        setCharityId('');
        setSelectedCharityName('');
        setServerError(null);
      }
    } catch {
      setServerError('Failed to create goal. Please try again.');
    } finally {
      setSubmitting(false);
    }
  }, [validate, title, description, deadline, pledgeAmount, selectedType, buildCriteria, charityId]);

  const typeOptions = goalTypes;

  return (
    <KeyboardAvoidingView
      className="flex-1 bg-codex-bg"
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      testID="goal-create-screen"
    >
      <ScrollView
        className="flex-1 px-5 py-6"
        contentContainerStyle={{ paddingBottom: 40 }}
        keyboardShouldPersistTaps="handled"
      >
        {/* Header */}
        <Text className="mb-6 font-serif text-2xl font-bold text-codex-text">
          Create a New Goal
        </Text>

        {/* Goal Type Picker */}
        <Text className="mb-2 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">
          Goal Type
        </Text>
        {loadingTypes ? (
          <View className="mb-4 items-center rounded-sm border border-codex-border bg-codex-surface px-4 py-6">
            <ActivityIndicator size="small" color="#8A2A1C" />
            <Text className="mt-2 font-sans text-sm text-codex-muted">Loading goal types...</Text>
          </View>
        ) : typeOptions.length === 0 ? (
          <View className="mb-4 rounded-sm border border-codex-border bg-codex-surface px-4 py-3">
            <Text className="font-sans text-sm text-codex-muted">No goal types available.</Text>
          </View>
        ) : (
          <View className="mb-4 flex-row flex-wrap gap-2">
            {typeOptions.map((opt) => {
              const selected = selectedType === opt.name;
              const label = typeLabel(opt.name);
              return (
                <Pressable
                  key={opt.name}
                  testID={`goal-type-${opt.name}`}
                  onPress={() => setSelectedType(opt.name)}
                  className={`rounded-sm border px-4 py-3 ${
                    selected
                      ? 'border-codex-accent bg-codex-accent/10'
                      : 'border-codex-border bg-codex-surface'
                  }`}
                >
                  <Text
                    className={`font-sans-medium text-sm ${
                      selected ? 'text-codex-accent' : 'text-codex-text'
                    }`}
                  >
                    {label}
                  </Text>
                  {label !== opt.description && (
                    <Text className="mt-0.5 font-sans text-xs text-codex-muted">
                      {opt.description}
                    </Text>
                  )}
                </Pressable>
              );
            })}
          </View>
        )}
        {errors['goal_type'] && (
          <Text className="-mt-2 mb-4 font-sans text-xs text-codex-accent">{errors['goal_type']}</Text>
        )}

        {/* Title */}
        <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">
          Title
        </Text>
        <TextInput
          testID="title-input"
          className={`mb-4 rounded-sm border ${errors['title'] ? 'border-codex-accent' : 'border-codex-border'} bg-codex-surface px-4 py-3 font-sans text-base text-codex-text`}
          value={title}
          onChangeText={(t) => { setTitle(t); if (errors['title']) setErrors((prev) => { const n = { ...prev }; delete n['title']; return n; }); }}
          placeholder="e.g., Ship the walkthrough"
          placeholderTextColor="#85796A"
        />
        {errors['title'] && (
          <Text className="-mt-2 mb-4 font-sans text-xs text-codex-accent">{errors['title']}</Text>
        )}

        {/* Description */}
        <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">
          Description
        </Text>
        <TextInput
          testID="description-input"
          className="mb-4 rounded-sm border border-codex-border bg-codex-surface px-4 py-3 font-sans text-base text-codex-text"
          value={description}
          onChangeText={setDescription}
          placeholder="Optional — describe what you'll accomplish"
          placeholderTextColor="#85796A"
          multiline
          numberOfLines={3}
        />

        {/* Deadline */}
        <DatePickerField
          value={deadline}
          onChange={setDeadline}
          error={errors['deadline']}
        />

        {/* Pledge Amount */}
        <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">
          Pledge ($)
        </Text>
        <TextInput
          testID="pledge-input"
          className={`mb-4 rounded-sm border ${errors['pledge_amount'] ? 'border-codex-accent' : 'border-codex-border'} bg-codex-surface px-4 py-3 font-sans text-base text-codex-text`}
          value={pledgeAmount}
          onChangeText={(t) => { setPledgeAmount(t); if (errors['pledge_amount']) setErrors((prev) => { const n = { ...prev }; delete n['pledge_amount']; return n; }); }}
          placeholder="5.00"
          placeholderTextColor="#85796A"
          keyboardType="decimal-pad"
        />
        {errors['pledge_amount'] && (
          <Text className="-mt-2 mb-4 font-sans text-xs text-codex-accent">{errors['pledge_amount']}</Text>
        )}

        {/* Conditional criteria forms */}
        {selectedType === 'youtube_video' && (
          <View className="mb-4 rounded-sm border border-codex-border bg-codex-surface p-4">
            <Text className="mb-3 font-serif text-lg font-bold text-codex-text">YouTube Criteria</Text>
            <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">
              Minimum Duration (seconds)
            </Text>
            <TextInput
              testID="youtube-duration-input"
              className={`mb-3 rounded-sm border ${errors['youtube_duration'] ? 'border-codex-accent' : 'border-codex-border'} bg-codex-bg px-4 py-3 font-sans text-base text-codex-text`}
              value={youtubeDuration}
              onChangeText={setYoutubeDuration}
              placeholder="30"
              placeholderTextColor="#85796A"
              keyboardType="number-pad"
            />
            {errors['youtube_duration'] && (
              <Text className="-mt-1 mb-3 font-sans text-xs text-codex-accent">{errors['youtube_duration']}</Text>
            )}
            <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">
              Video Description
            </Text>
            <TextInput
              testID="youtube-desc-input"
              className="rounded-sm border border-codex-border bg-codex-bg px-4 py-3 font-sans text-base text-codex-text"
              value={youtubeDesc}
              onChangeText={setYoutubeDesc}
              placeholder="Describe what the video must contain"
              placeholderTextColor="#85796A"
            />
          </View>
        )}

        {selectedType === 'api_endpoint' && (
          <View className="mb-4 rounded-sm border border-codex-border bg-codex-surface p-4">
            <Text className="mb-3 font-serif text-lg font-bold text-codex-text">API Endpoint Criteria</Text>
            <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">URL</Text>
            <TextInput
              testID="api-url-input"
              className={`mb-3 rounded-sm border ${errors['api_url'] ? 'border-codex-accent' : 'border-codex-border'} bg-codex-bg px-4 py-3 font-sans text-base text-codex-text`}
              value={apiUrl}
              onChangeText={setApiUrl}
              placeholder="https://api.example.com/health"
              placeholderTextColor="#85796A"
              autoCapitalize="none"
            />
            {errors['api_url'] && (
              <Text className="-mt-1 mb-3 font-sans text-xs text-codex-accent">{errors['api_url']}</Text>
            )}
            <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">Method</Text>
            <TextInput
              testID="api-method-input"
              className="mb-3 rounded-sm border border-codex-border bg-codex-bg px-4 py-3 font-sans text-base text-codex-text"
              value={apiMethod}
              onChangeText={setApiMethod}
              placeholder="GET"
              placeholderTextColor="#85796A"
              autoCapitalize="characters"
            />
            <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">Headers (key:value, comma-separated)</Text>
            <TextInput
              testID="api-headers-input"
              className="mb-3 rounded-sm border border-codex-border bg-codex-bg px-4 py-3 font-sans text-base text-codex-text"
              value={apiHeaders}
              onChangeText={setApiHeaders}
              placeholder="Content-Type: application/json"
              placeholderTextColor="#85796A"
            />
            <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">Expected Status</Text>
            <TextInput
              testID="api-status-input"
              className="mb-3 rounded-sm border border-codex-border bg-codex-bg px-4 py-3 font-sans text-base text-codex-text"
              value={apiExpectedStatus}
              onChangeText={setApiExpectedStatus}
              placeholder="200"
              placeholderTextColor="#85796A"
              keyboardType="number-pad"
            />
            <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">Expected Body (JSON)</Text>
            <TextInput
              testID="api-body-input"
              className="rounded-sm border border-codex-border bg-codex-bg px-4 py-3 font-sans text-base text-codex-text"
              value={apiExpectedBody}
              onChangeText={setApiExpectedBody}
              placeholder='{"status": "ok"}'
              placeholderTextColor="#85796A"
              multiline
            />
          </View>
        )}

        {selectedType === 'dev_sandbox' && (
          <View className="mb-4 rounded-sm border border-codex-border bg-codex-surface p-4">
            <Text className="mb-3 font-serif text-lg font-bold text-codex-text">Dev Sandbox Criteria</Text>
            <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">Repo URL</Text>
            <TextInput
              testID="sandbox-repo-url-input"
              className={`mb-3 rounded-sm border ${errors['sandbox_repo_url'] ? 'border-codex-accent' : 'border-codex-border'} bg-codex-bg px-4 py-3 font-sans text-base text-codex-text`}
              value={sandboxRepoUrl}
              onChangeText={setSandboxRepoUrl}
              placeholder="https://github.com/owner/repo"
              placeholderTextColor="#85796A"
              autoCapitalize="none"
            />
            {errors['sandbox_repo_url'] && (
              <Text className="-mt-1 mb-3 font-sans text-xs text-codex-accent">{errors['sandbox_repo_url']}</Text>
            )}
            <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">Branch</Text>
            <TextInput
              testID="sandbox-branch-input"
              className="mb-3 rounded-sm border border-codex-border bg-codex-bg px-4 py-3 font-sans text-base text-codex-text"
              value={sandboxBranch}
              onChangeText={setSandboxBranch}
              placeholder="main"
              placeholderTextColor="#85796A"
            />
            <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">Test Command</Text>
            <TextInput
              testID="sandbox-test-command-input"
              className="mb-3 rounded-sm border border-codex-border bg-codex-bg px-4 py-3 font-sans text-base text-codex-text"
              value={sandboxTestCommand}
              onChangeText={setSandboxTestCommand}
              placeholder="pytest"
              placeholderTextColor="#85796A"
            />
            <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">Goal Description</Text>
            <TextInput
              testID="sandbox-goal-desc-input"
              className="rounded-sm border border-codex-border bg-codex-bg px-4 py-3 font-sans text-base text-codex-text"
              value={sandboxGoalDesc}
              onChangeText={setSandboxGoalDesc}
              placeholder="All tests must pass"
              placeholderTextColor="#85796A"
            />
          </View>
        )}

        {selectedType === 'github_repo' && (
          <View className="mb-4 rounded-sm border border-codex-border bg-codex-surface p-4">
            <Text className="mb-3 font-serif text-lg font-bold text-codex-text">GitHub Criteria</Text>
            <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">Repo URL</Text>
            <TextInput
              testID="github-repo-url-input"
              className={`mb-3 rounded-sm border ${errors['github_repo_url'] ? 'border-codex-accent' : 'border-codex-border'} bg-codex-bg px-4 py-3 font-sans text-base text-codex-text`}
              value={githubRepoUrl}
              onChangeText={setGithubRepoUrl}
              placeholder="https://github.com/owner/repo"
              placeholderTextColor="#85796A"
              autoCapitalize="none"
            />
            {errors['github_repo_url'] && (
              <Text className="-mt-1 mb-3 font-sans text-xs text-codex-accent">{errors['github_repo_url']}</Text>
            )}
            <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">Branch</Text>
            <TextInput
              testID="github-branch-input"
              className="rounded-sm border border-codex-border bg-codex-bg px-4 py-3 font-sans text-base text-codex-text"
              value={githubBranch}
              onChangeText={setGithubBranch}
              placeholder="main"
              placeholderTextColor="#85796A"
            />
          </View>
        )}

        {/* Charity Search */}
        <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">
          Charity (optional)
        </Text>
        {selectedCharityName ? (
          <View className="mb-4 rounded-sm border border-codex-border bg-codex-surface px-4 py-3">
            <View className="flex-row items-center justify-between">
              <Text className="font-sans text-base text-codex-text">{selectedCharityName}</Text>
              <Pressable
                onPress={() => { setCharityId(''); setSelectedCharityName(''); }}
                className="ml-2 rounded-sm px-2 py-1 active:bg-codex-bg"
              >
                <Text className="font-sans text-xs text-codex-accent">Change</Text>
              </Pressable>
            </View>
          </View>
        ) : (
          <View className="mb-4">
            <TextInput
              testID="charity-search-input"
              className="rounded-sm border border-codex-border bg-codex-surface px-4 py-3 font-sans text-base text-codex-text"
              value={charitySearch}
              onChangeText={searchCharities}
              placeholder="Search charities..."
              placeholderTextColor="#85796A"
            />
            {searching && (
              <View className="mt-1 items-center py-2">
                <ActivityIndicator size="small" color="#8A2A1C" />
              </View>
            )}
            {charities.length > 0 && (
              <View className="mt-1 rounded-sm border border-codex-border bg-codex-surface">
                {charities.map((c) => (
                  <Pressable
                    key={c.id}
                    testID={`charity-result-${c.id}`}
                    onPress={() => selectCharity(c)}
                    className="border-b border-codex-border px-4 py-3 last:border-b-0 active:bg-codex-bg"
                  >
                    <Text className="font-sans text-base text-codex-text">{c.name}</Text>
                    {c.description && (
                      <Text className="font-sans text-sm text-codex-muted">{c.description}</Text>
                    )}
                  </Pressable>
                ))}
              </View>
            )}
          </View>
        )}

        {/* Submit */}
        {serverError && (
          <View className="mb-4 rounded-sm border border-codex-accent bg-codex-accent/10 px-4 py-3">
            <Text className="font-sans text-sm text-codex-accent">{serverError}</Text>
          </View>
        )}

        <CodexButton
          testID="create-goal-button"
          onPress={handleSubmit}
          disabled={submitting || loadingTypes}
        >
          {submitting ? 'Creating...' : 'Create Goal'}
        </CodexButton>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}