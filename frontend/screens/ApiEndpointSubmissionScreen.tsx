import { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Platform, Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { CodexHeader } from '../components/CodexHeader';
import { CodexCard } from '../components/CodexCard';
import { CodexButton } from '../components/CodexButton';
import { CodexInput } from '../components/CodexInput';
import { CodexFooter } from '../components/CodexFooter';
import { SectionHeading } from '../components/SectionHeading';
import { api } from '../services/api';
import { useNavigation } from '../hooks/useNavigation';
import type { Goal } from '../types';

const TEMPLATES_KEY = 'api_endpoint_templates';

interface HeaderRow {
  id: string;
  key: string;
  value: string;
}

interface Props {
  goalId: string;
}

function humanDate(iso: string): string {
  return new Date(iso).toLocaleString();
}

function parseTemplateJson(raw: string): Record<string, unknown> | null {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

async function loadTemplates(): Promise<string | null> {
  if (Platform.OS === 'web') {
    try { return localStorage.getItem(TEMPLATES_KEY); } catch { return null; }
  }
  try {
    const SecureStore = require('expo-secure-store');
    return await SecureStore.getItemAsync(TEMPLATES_KEY);
  } catch { return null; }
}

async function saveTemplates(value: string): Promise<void> {
  if (Platform.OS === 'web') {
    try { localStorage.setItem(TEMPLATES_KEY, value); } catch {}
    return;
  }
  try {
    const SecureStore = require('expo-secure-store');
    await SecureStore.setItemAsync(TEMPLATES_KEY, value);
  } catch {}
}

export default function ApiEndpointSubmissionScreen({ goalId }: Props) {
  const { goBack } = useNavigation();
  const [goal, setGoal] = useState<Goal | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [url, setUrl] = useState('');
  const [method, setMethod] = useState('GET');
  const [headers, setHeaders] = useState<HeaderRow[]>([]);
  const [expectedStatus, setExpectedStatus] = useState('200');
  const [expectedBodySchema, setExpectedBodySchema] = useState('{}');

  const [urlError, setUrlError] = useState<string | null>(null);
  const [schemaError, setSchemaError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [verificationStatus, setVerificationStatus] = useState<string | null>(null);
  const [verificationDetails, setVerificationDetails] = useState<Record<string, unknown> | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const [templateName, setTemplateName] = useState('');
  const [savedTemplates, setSavedTemplates] = useState<Record<string, unknown>>({});
  const [templateSaved, setTemplateSaved] = useState(false);
  const [showTemplates, setShowTemplates] = useState(false);

  const headerIdCounter = useRef(0);

  useEffect(() => {
    (async () => {
      try {
        const raw = await loadTemplates();
        if (raw) {
          setSavedTemplates(JSON.parse(raw));
        }
      } catch {
        // ignore
      }
    })();
  }, []);

  const fetchGoal = useCallback(async () => {
    setLoading(true);
    setError(null);
    const result = await api.getGoal(goalId);
    if (result.data) {
      setGoal(result.data);
      const data = result.data.criteria?.criteria_data || {};
      setUrl((data.url as string) || '');
      setMethod((data.method as string) || 'GET');
      setExpectedStatus(String((data.expected_status as number) ?? 200));
      const bodySchema = data.expected_body_schema;
      setExpectedBodySchema(bodySchema ? JSON.stringify(bodySchema, null, 2) : '{}');
      const rawHeaders = data.headers as Record<string, string> | undefined;
      if (rawHeaders && typeof rawHeaders === 'object') {
        const rows: HeaderRow[] = Object.entries(rawHeaders).map(([k, v]) => ({
          id: String(++headerIdCounter.current),
          key: k,
          value: v,
        }));
        setHeaders(rows);
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

  const isDeadlinePassed = goal ? new Date(goal.deadline) < new Date() : false;

  const validateUrl = (u: string): string | null => {
    if (!u.trim()) return 'URL is required';
    try {
      new URL(u.trim());
    } catch {
      return 'Must be a valid URL';
    }
    return null;
  };

  const addHeader = useCallback(() => {
    const id = String(++headerIdCounter.current);
    setHeaders((prev) => [...prev, { id, key: '', value: '' }]);
  }, []);

  const updateHeaderKey = useCallback((id: string, key: string) => {
    setHeaders((prev) => prev.map((h) => (h.id === id ? { ...h, key } : h)));
  }, []);

  const updateHeaderValue = useCallback((id: string, value: string) => {
    setHeaders((prev) => prev.map((h) => (h.id === id ? { ...h, value } : h)));
  }, []);

  const removeHeader = useCallback((id: string) => {
    setHeaders((prev) => prev.filter((h) => h.id !== id));
  }, []);

  const startPolling = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
    pollingRef.current = setInterval(async () => {
      const result = await api.getVerificationStatus(goalId);
      if (result.data) {
        setVerificationStatus(result.data.verification_status);
        setVerificationDetails(result.data.verification_details);
        if (result.data.verification_status === 'verified' || result.data.verification_status === 'failed') {
          if (pollingRef.current) {
            clearInterval(pollingRef.current);
            pollingRef.current = null;
          }
        }
      }
    }, 3000);
  }, [goalId]);

  const headersToRecord = useCallback((): Record<string, string> => {
    const record: Record<string, string> = {};
    for (const h of headers) {
      if (h.key.trim()) {
        record[h.key.trim()] = h.value;
      }
    }
    return record;
  }, [headers]);

  const handleSubmit = async () => {
    const validationError = validateUrl(url);
    if (validationError) {
      setUrlError(validationError);
      return;
    }
    setUrlError(null);

    const schemaJson = parseTemplateJson(expectedBodySchema);
    if (expectedBodySchema.trim() && !schemaJson) {
      setSchemaError('Invalid JSON in body schema');
      return;
    }
    setSchemaError(null);

    setApiError(null);
    setSubmitting(true);
    setVerificationStatus('pending');

    const headerRecord = headersToRecord();

    const result = await api.submitApiEndpointProof(goalId, {
      url: url.trim(),
      method,
      headers: Object.keys(headerRecord).length > 0 ? headerRecord : undefined,
      expected_status: Number(expectedStatus) || 200,
      expected_body_schema: schemaJson || undefined,
    });

    if (result.error) {
      setApiError(result.error);
      setSubmitting(false);
      setVerificationStatus(null);
      return;
    }

    setSubmitting(false);
    setVerificationStatus('pending');
    startPolling();
  };

  const saveTemplate = () => {
    if (!templateName.trim()) return;
    const templates = { ...savedTemplates };
    templates[templateName.trim()] = {
      url,
      method,
      headers,
      expected_status: expectedStatus,
      expected_body_schema: expectedBodySchema,
    };
    void saveTemplates(JSON.stringify(templates));
    setSavedTemplates(templates);
    setTemplateSaved(true);
    setTimeout(() => setTemplateSaved(false), 2000);
  };

  const loadTemplate = (name: string) => {
    const t = savedTemplates[name] as Record<string, unknown> | undefined;
    if (!t) return;
    setUrl((t.url as string) || '');
    setMethod((t.method as string) || 'GET');
    setHeaders((t.headers as HeaderRow[]) || []);
    setExpectedStatus((t.expected_status as string) || '200');
    setExpectedBodySchema((t.expected_body_schema as string) || '{}');
    setShowTemplates(false);
  };

  if (loading) {
    return (
      <View className="flex-1 bg-codex-bg">
        <CodexHeader pageNumber="III" totalPages="IV" />
        <View className="flex-row items-center px-6 pb-2 pt-3">
          <Pressable onPress={goBack} className="mr-3 p-1">
            <Text className="font-serif text-2xl text-codex-muted">{'←'}</Text>
          </Pressable>
          <Text className="font-serif-italic text-lg text-codex-text">API Endpoint Proof</Text>
        </View>
        <View className="flex-1 items-center justify-center">
          <ActivityIndicator size="large" color="#8A2A1C" />
        </View>
      </View>
    );
  }

  if (error || !goal) {
    return (
      <View className="flex-1 bg-codex-bg">
        <CodexHeader pageNumber="III" totalPages="IV" />
        <View className="flex-row items-center px-6 pb-2 pt-3">
          <Pressable onPress={goBack} className="mr-3 p-1">
            <Text className="font-serif text-2xl text-codex-muted">{'←'}</Text>
          </Pressable>
          <Text className="font-serif-italic text-lg text-codex-text">Error</Text>
        </View>
        <View className="flex-1 items-center justify-center px-6">
          <Text className="mb-2 font-sans text-lg text-codex-accent">{error || 'Goal not found'}</Text>
          <CodexButton onPress={fetchGoal}>
            Retry
          </CodexButton>
        </View>
      </View>
    );
  }

  const details: Record<string, unknown> = verificationDetails || {};
  const requestUrl = details.request_url as string | undefined;
  const requestMethod = details.request_method as string | undefined;
  const expectedSt = details.expected_status as number | undefined;
  const actualStatus = details.actual_status as number | undefined;
  const actualHeaders = details.actual_headers as Record<string, string> | undefined;
  const responseBodyPreview = details.response_body_preview as string | undefined;
  const statusPassed = details.status_passed as boolean | undefined;
  const statusFailure = details.status_failure_reason as string | undefined;
  const schemaPassed = details.schema_passed as boolean | undefined;
  const schemaFailure = details.schema_failure_reason as string | undefined;
  const requestHeaders = details.request_headers as Record<string, string> | undefined;

  return (
    <View className="flex-1 bg-codex-bg">
      <CodexHeader pageNumber="III" totalPages="IV" />
      <View className="flex-row items-center px-6 pb-2 pt-3">
        <Pressable onPress={goBack} className="mr-3 p-1">
          <Text className="font-serif text-2xl text-codex-muted">{'←'}</Text>
        </Pressable>
        <Text className="font-serif-italic text-lg text-codex-text">API Endpoint Proof</Text>
      </View>

      <ScrollView className="flex-1 px-6" showsVerticalScrollIndicator={false}>
        <SectionHeading
          number="The Witness — API"
          title=""
          subtitle="Submit your API endpoint for judgment. The endpoint will be called and its response examined."
        />

        <CodexCard className="mb-4 p-4">
          <Text className="font-serif text-lg text-codex-text">{goal.title}</Text>
          <Text className="mt-1 font-sans text-sm text-codex-muted">{goal.description || 'No description'}</Text>
          <Text className="mt-2 font-sans text-xs text-codex-muted">
            Deadline: {humanDate(goal.deadline)}
          </Text>
        </CodexCard>

        {isDeadlinePassed ? (
          <View testID="deadline-passed-message" className="mb-4 rounded-sm border border-codex-accent bg-codex-surface p-4">
            <Text className="font-sans text-sm text-codex-accent">
              Deadline has passed — you can no longer submit proof.
            </Text>
          </View>
        ) : verificationStatus === 'verified' || verificationStatus === 'failed' ? null : (
          <View className="mb-4">
            <CodexInput
              testID="endpoint-url-input"
              label="URL"
              value={url}
              onChangeText={(t) => { setUrl(t); if (urlError && !validateUrl(t)) setUrlError(null); }}
              placeholder="https://api.example.com/health"
              editable={!submitting && verificationStatus !== 'pending'}
              error={urlError}
            />

            <CodexInput
              testID="endpoint-method-input"
              label="HTTP Method"
              value={method}
              onChangeText={setMethod}
              editable={!submitting && verificationStatus !== 'pending'}
              autoCapitalize="characters"
            />

            <View testID="headers-section" className="mb-4">
              <View className="mb-1.5 flex-row items-center justify-between">
                <Text className="font-sans-medium text-xs uppercase tracking-wider text-codex-muted">Headers</Text>
                <Pressable testID="add-header-button" onPress={addHeader}>
                  <Text className="font-sans text-xs uppercase tracking-wider text-codex-accent">+ Add</Text>
                </Pressable>
              </View>
              {headers.map((h) => (
                <View key={h.id} testID={`header-row-${h.id}`} className="mb-2 flex-row items-center gap-2">
                  <TextInput
                    testID={`header-key-input-${h.id}`}
                    className="flex-1 rounded-sm border border-codex-border bg-codex-surface px-3 py-2 font-mono text-sm text-codex-text"
                    placeholder="Key"
                    value={h.key}
                    onChangeText={(t) => updateHeaderKey(h.id, t)}
                    editable={!submitting && verificationStatus !== 'pending'}
                    autoCapitalize="none"
                  />
                  <TextInput
                    testID={`header-value-input-${h.id}`}
                    className="flex-1 rounded-sm border border-codex-border bg-codex-surface px-3 py-2 font-mono text-sm text-codex-text"
                    placeholder="Value"
                    value={h.value}
                    onChangeText={(t) => updateHeaderValue(h.id, t)}
                    editable={!submitting && verificationStatus !== 'pending'}
                    autoCapitalize="none"
                  />
                  <Pressable
                    testID={`remove-header-${h.id}`}
                    onPress={() => removeHeader(h.id)}
                    className="rounded-sm bg-codex-accent px-2 py-1"
                  >
                    <Text className="font-sans text-sm text-codex-surface" style={{ lineHeight: 20 }}>✕</Text>
                  </Pressable>
                </View>
              ))}
              {headers.length === 0 && (
                <Text className="font-sans text-xs text-codex-muted">No custom headers</Text>
              )}
            </View>

            <CodexInput
              testID="expected-status-input"
              label="Expected Status Code"
              value={expectedStatus}
              onChangeText={setExpectedStatus}
              editable={!submitting && verificationStatus !== 'pending'}
              keyboardType="numeric"
            />

            <CodexInput
              testID="expected-body-schema-input"
              label="Expected Body Schema (JSON)"
              value={expectedBodySchema}
              onChangeText={(t) => { setExpectedBodySchema(t); if (schemaError) setSchemaError(null); }}
              editable={!submitting && verificationStatus !== 'pending'}
              multiline
              error={schemaError}
            />

            {submitting ? (
              <View testID="submission-loading" className="items-center py-4">
                <ActivityIndicator size="large" color="#8A2A1C" />
                <Text className="mt-2 font-sans text-sm text-codex-muted">Submitting proof...</Text>
              </View>
            ) : verificationStatus === 'pending' ? (
              <View testID="verification-pending" className="items-center py-4">
                <ActivityIndicator size="large" color="#8A2A1C" />
                <Text className="mt-2 font-sans text-sm text-codex-muted">
                  Verifying your API endpoint...
                </Text>
              </View>
            ) : (
              <CodexButton
                testID="submit-api-proof-button"
                onPress={handleSubmit}
                className="mb-4"
              >
                Submit for Judgement ↳
              </CodexButton>
            )}
            {apiError && (
              <Text testID="api-error" className="mb-2 font-sans text-sm text-codex-accent">{apiError}</Text>
            )}

            <CodexCard className="mb-6 p-4">
              <Text className="mb-2 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">Templates</Text>
              <View className="mb-2 flex-row items-center gap-2">
                <TextInput
                  testID="template-name-input"
                  className="flex-1 rounded-sm border border-codex-border bg-codex-surface px-3 py-2 font-sans text-sm text-codex-text"
                  placeholder="Template name"
                  value={templateName}
                  onChangeText={setTemplateName}
                />
                <Pressable testID="save-template-button" onPress={saveTemplate} className="rounded-sm bg-codex-accent px-4 py-2">
                  <Text className="font-sans text-sm font-medium text-codex-surface">Save</Text>
                </Pressable>
              </View>
              {templateSaved && (
                <Text testID="template-saved-message" className="mb-1 font-sans text-xs text-codex-accent">
                  Template saved!
                </Text>
              )}
              <Pressable testID="load-template-dropdown" onPress={() => setShowTemplates(!showTemplates)}>
                <Text className="font-sans text-xs uppercase tracking-wider text-codex-accent">
                  {showTemplates ? 'Hide' : 'Show'} saved templates ({Object.keys(savedTemplates).length})
                </Text>
              </Pressable>
              {showTemplates && Object.keys(savedTemplates).length > 0 && (
                <View className="mt-2 rounded-sm border border-codex-border bg-codex-surface">
                  {Object.keys(savedTemplates).map((name) => (
                    <Pressable
                      key={name}
                      className="px-4 py-3"
                      onPress={() => loadTemplate(name)}
                    >
                      <Text className="font-sans text-sm text-codex-text">{name}</Text>
                    </Pressable>
                  ))}
                </View>
              )}
              {showTemplates && Object.keys(savedTemplates).length === 0 && (
                <Text className="mt-1 font-sans text-xs text-codex-muted">No saved templates</Text>
              )}
            </CodexCard>
          </View>
        )}

        {verificationStatus === 'verified' && (
          <View testID="verification-verified" className="mb-4 rounded-sm border border-codex-border bg-codex-surface p-4">
            <View testID="verification-result" className="mb-3 items-center">
              <Text className="font-serif text-2xl text-codex-text">Verdict: True</Text>
              <Text className="mt-1 font-sans text-sm text-codex-muted">Endpoint verified</Text>
            </View>

            <CodexCard testID="request-details" className="mb-3 bg-codex-bg p-3">
              <Text className="mb-1 font-sans text-xs uppercase tracking-wider text-codex-muted">Request Sent</Text>
              <Text className="font-mono text-xs text-codex-text">URL: {requestUrl}</Text>
              <Text className="font-mono text-xs text-codex-text">Method: {requestMethod}</Text>
              {requestHeaders && (
                <Text className="font-mono text-xs text-codex-text">
                  Headers: {JSON.stringify(requestHeaders)}
                </Text>
              )}
            </CodexCard>

            <View testID="status-result" className="mb-2 flex-row items-center">
              <Text className="mr-2 font-sans text-sm text-codex-text">✓ Status: Expected {expectedSt}, Got {actualStatus}</Text>
            </View>

            {actualHeaders && (
              <View className="mb-2">
                <Text className="font-sans text-xs text-codex-muted">Response Headers:</Text>
                <Text className="font-mono text-xs text-codex-text">{JSON.stringify(actualHeaders)}</Text>
              </View>
            )}

            {responseBodyPreview && (
              <View testID="response-body" className="mb-2">
                <Text className="font-sans text-xs text-codex-muted">Response Body:</Text>
                <Text className="font-mono text-xs text-codex-text" selectable>{responseBodyPreview}</Text>
              </View>
            )}

            <View testID="schema-result" className="flex-row items-center">
              <Text className="mr-2 font-sans text-sm text-codex-text">✓ Schema: Passed</Text>
            </View>
          </View>
        )}

        {verificationStatus === 'failed' && (
          <View testID="verification-failed" className="mb-4 rounded-sm border border-codex-accent bg-codex-surface p-4">
            <View testID="verification-result" className="mb-3 items-center">
              <Text className="font-serif text-2xl text-codex-accent">Verdict: False</Text>
              <Text className="mt-1 font-sans text-sm text-codex-muted">Verification failed</Text>
            </View>

            {requestUrl && (
              <CodexCard testID="request-details" className="mb-3 bg-codex-bg p-3">
                <Text className="mb-1 font-sans text-xs uppercase tracking-wider text-codex-muted">Request Sent</Text>
                <Text className="font-mono text-xs text-codex-text">URL: {requestUrl}</Text>
                <Text className="font-mono text-xs text-codex-text">Method: {requestMethod}</Text>
                {requestHeaders && (
                  <Text className="font-mono text-xs text-codex-text">
                    Headers: {JSON.stringify(requestHeaders)}
                  </Text>
                )}
              </CodexCard>
            )}

            <View testID="status-result" className="mb-2 flex-row items-center">
              {statusPassed ? (
                <Text className="mr-2 font-sans text-sm text-codex-text">✓ Status: Matched</Text>
              ) : (
                <Text className="mr-2 font-sans text-sm text-codex-accent">✗ Status: Expected {expectedSt}, Got {actualStatus}</Text>
              )}
            </View>

            {statusFailure && (
              <Text className="mb-2 font-sans text-xs text-codex-accent">{statusFailure}</Text>
            )}

            {responseBodyPreview && (
              <View testID="response-body" className="mb-2">
                <Text className="font-sans text-xs text-codex-muted">Response Body:</Text>
                <Text className="font-mono text-xs text-codex-text" selectable>{responseBodyPreview}</Text>
              </View>
            )}

            {schemaPassed !== undefined && (
              <View testID="schema-result" className="mb-1 flex-row items-center">
                <Text className="mr-2 font-sans text-sm text-codex-text">
                  {schemaPassed ? '✓' : '✗'} Schema: {schemaPassed ? 'Passed' : 'Failed'}
                </Text>
              </View>
            )}

            {schemaFailure && (
              <Text className="font-sans text-xs text-codex-accent">{schemaFailure}</Text>
            )}
          </View>
        )}
      </ScrollView>

      <CodexFooter />
    </View>
  );
}
