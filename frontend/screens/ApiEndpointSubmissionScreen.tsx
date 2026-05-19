import { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, Text, TextInput, View } from 'react-native';
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

  const loadTemplates = useCallback(() => {
    try {
      const raw = localStorage.getItem(TEMPLATES_KEY);
      if (raw) {
        setSavedTemplates(JSON.parse(raw));
      }
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    loadTemplates();
  }, [loadTemplates]);

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
    localStorage.setItem(TEMPLATES_KEY, JSON.stringify(templates));
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
      <View className="flex-1 bg-white">
        <View className="flex-row items-center px-4 pt-14 pb-2">
          <Pressable onPress={goBack} className="mr-3 p-1">
            <Text className="text-2xl text-gray-600">{'<'}</Text>
          </Pressable>
          <Text className="text-xl font-bold text-gray-900">API Endpoint Proof</Text>
        </View>
        <View className="flex-1 items-center justify-center">
          <ActivityIndicator size="large" color="#4F46E5" />
        </View>
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
          <Text className="text-xl font-bold text-gray-900">Error</Text>
        </View>
        <View className="flex-1 items-center justify-center px-6">
          <Text className="mb-2 text-lg text-red-500">{error || 'Goal not found'}</Text>
          <Pressable
            testID="retry-button"
            className="rounded-xl bg-indigo-600 px-6 py-3"
            onPress={fetchGoal}
          >
            <Text className="text-base font-semibold text-white">Retry</Text>
          </Pressable>
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
    <View className="flex-1 bg-white">
      <View className="flex-row items-center px-4 pt-14 pb-2">
        <Pressable onPress={goBack} className="mr-3 p-1">
          <Text className="text-2xl text-gray-600">{'<'}</Text>
        </Pressable>
        <Text className="text-xl font-bold text-gray-900">API Endpoint Proof</Text>
      </View>

      <ScrollView className="flex-1 px-4" showsVerticalScrollIndicator={false}>
        <View className="mb-4 rounded-2xl border border-gray-200 p-4">
          <Text className="text-lg font-bold text-gray-900">{goal.title}</Text>
          <Text className="mt-1 text-sm text-gray-600">{goal.description || 'No description'}</Text>
          <Text className="mt-2 text-xs text-gray-400">
            Deadline: {humanDate(goal.deadline)}
          </Text>
        </View>

        {isDeadlinePassed ? (
          <View testID="deadline-passed-message" className="mb-4 rounded-xl bg-red-50 p-4">
            <Text className="text-sm font-semibold text-red-700">
              Deadline has passed — you can no longer submit proof.
            </Text>
          </View>
        ) : verificationStatus === 'verified' || verificationStatus === 'failed' ? null : (
          <View className="mb-4">
            <View className="mb-4">
              <Text className="mb-1 text-sm font-medium text-gray-700">URL</Text>
              <TextInput
                testID="endpoint-url-input"
                className="rounded-xl border border-gray-300 px-4 py-3 text-base text-gray-900"
                placeholder="https://api.example.com/health"
                value={url}
                onChangeText={(t) => { setUrl(t); if (urlError && !validateUrl(t)) setUrlError(null); }}
                editable={!submitting && verificationStatus !== 'pending'}
                autoCapitalize="none"
                autoCorrect={false}
              />
              {urlError && (
                <Text testID="url-validation-error" className="mt-1 text-sm text-red-500">{urlError}</Text>
              )}
            </View>

            <View className="mb-4">
              <Text className="mb-1 text-sm font-medium text-gray-700">HTTP Method</Text>
              <TextInput
                testID="endpoint-method-input"
                className="rounded-xl border border-gray-300 px-4 py-3 text-base text-gray-900"
                placeholder="GET"
                value={method}
                onChangeText={setMethod}
                editable={!submitting && verificationStatus !== 'pending'}
                autoCapitalize="characters"
              />
            </View>

            <View testID="headers-section" className="mb-4">
              <View className="mb-1 flex-row items-center justify-between">
                <Text className="text-sm font-medium text-gray-700">Headers</Text>
                <Pressable testID="add-header-button" onPress={addHeader}>
                  <Text className="text-sm font-semibold text-indigo-600">+ Add Header</Text>
                </Pressable>
              </View>
              {headers.map((h) => (
                <View key={h.id} testID={`header-row-${h.id}`} className="mb-2 flex-row items-center gap-2">
                  <TextInput
                    testID={`header-key-input-${h.id}`}
                    className="flex-1 rounded-xl border border-gray-300 px-3 py-2 text-sm text-gray-900"
                    placeholder="Key"
                    value={h.key}
                    onChangeText={(t) => updateHeaderKey(h.id, t)}
                    editable={!submitting && verificationStatus !== 'pending'}
                    autoCapitalize="none"
                  />
                  <TextInput
                    testID={`header-value-input-${h.id}`}
                    className="flex-1 rounded-xl border border-gray-300 px-3 py-2 text-sm text-gray-900"
                    placeholder="Value"
                    value={h.value}
                    onChangeText={(t) => updateHeaderValue(h.id, t)}
                    editable={!submitting && verificationStatus !== 'pending'}
                    autoCapitalize="none"
                  />
                  <Pressable
                    testID={`remove-header-${h.id}`}
                    onPress={() => removeHeader(h.id)}
                    className="rounded-lg bg-red-100 px-2 py-1"
                  >
                    <Text className="text-sm text-red-600" style={{ lineHeight: 20 }}>✕</Text>
                  </Pressable>
                </View>
              ))}
              {headers.length === 0 && (
                <Text className="text-xs text-gray-400">No custom headers</Text>
              )}
            </View>

            <View className="mb-4">
              <Text className="mb-1 text-sm font-medium text-gray-700">Expected Status Code</Text>
              <TextInput
                testID="expected-status-input"
                className="rounded-xl border border-gray-300 px-4 py-3 text-base text-gray-900"
                placeholder="200"
                value={expectedStatus}
                onChangeText={setExpectedStatus}
                editable={!submitting && verificationStatus !== 'pending'}
                keyboardType="numeric"
              />
            </View>

            <View className="mb-4">
              <Text className="mb-1 text-sm font-medium text-gray-700">Expected Body Schema (JSON)</Text>
              <TextInput
                testID="expected-body-schema-input"
                className="rounded-xl border border-gray-300 px-4 py-3 text-base text-gray-900 min-h-[80px]"
                placeholder='{"type": "object"}'
                value={expectedBodySchema}
                onChangeText={(t) => { setExpectedBodySchema(t); if (schemaError) setSchemaError(null); }}
                editable={!submitting && verificationStatus !== 'pending'}
                multiline
                textAlignVertical="top"
              />
              {schemaError && (
                <Text testID="schema-validation-error" className="mt-1 text-sm text-red-500">{schemaError}</Text>
              )}
            </View>

            {submitting ? (
              <View testID="submission-loading" className="items-center py-4">
                <ActivityIndicator size="large" color="#4F46E5" />
                <Text className="mt-2 text-sm text-gray-500">Submitting proof...</Text>
              </View>
            ) : verificationStatus === 'pending' ? (
              <View testID="verification-pending" className="items-center py-4">
                <ActivityIndicator size="large" color="#4F46E5" />
                <Text className="mt-2 text-sm text-gray-500">
                  Verifying your API endpoint...
                </Text>
              </View>
            ) : (
              <Pressable
                testID="submit-api-proof-button"
                className="mb-4 rounded-xl bg-indigo-600 px-6 py-4"
                onPress={handleSubmit}
              >
                <Text className="text-center text-base font-semibold text-white">
                  Submit Proof
                </Text>
              </Pressable>
            )}
            {apiError && (
              <Text testID="api-error" className="mb-2 text-sm text-red-500">{apiError}</Text>
            )}

            <View className="mb-6 rounded-xl border border-gray-200 p-4">
              <Text className="mb-2 text-sm font-medium text-gray-700">Templates</Text>
              <View className="mb-2 flex-row items-center gap-2">
                <TextInput
                  testID="template-name-input"
                  className="flex-1 rounded-xl border border-gray-300 px-3 py-2 text-sm text-gray-900"
                  placeholder="Template name"
                  value={templateName}
                  onChangeText={setTemplateName}
                />
                <Pressable testID="save-template-button" onPress={saveTemplate} className="rounded-xl bg-indigo-600 px-4 py-2">
                  <Text className="text-sm font-semibold text-white">Save</Text>
                </Pressable>
              </View>
              {templateSaved && (
                <Text testID="template-saved-message" className="mb-1 text-xs text-green-600">
                  Template saved!
                </Text>
              )}
              <Pressable testID="load-template-dropdown" onPress={() => setShowTemplates(!showTemplates)}>
                <Text className="text-sm font-medium text-indigo-600">
                  {showTemplates ? 'Hide' : 'Show'} saved templates ({Object.keys(savedTemplates).length})
                </Text>
              </Pressable>
              {showTemplates && Object.keys(savedTemplates).length > 0 && (
                <View className="mt-2 rounded-xl border border-gray-200 bg-white">
                  {Object.keys(savedTemplates).map((name) => (
                    <Pressable
                      key={name}
                      className="px-4 py-3"
                      onPress={() => loadTemplate(name)}
                    >
                      <Text className="text-sm text-gray-800">{name}</Text>
                    </Pressable>
                  ))}
                </View>
              )}
              {showTemplates && Object.keys(savedTemplates).length === 0 && (
                <Text className="mt-1 text-xs text-gray-400">No saved templates</Text>
              )}
            </View>
          </View>
        )}

        {verificationStatus === 'verified' && (
          <View testID="verification-verified" className="mb-4 rounded-xl bg-green-50 p-4">
            <View testID="verification-result" className="mb-3 items-center">
              <Text className="text-4xl">✅</Text>
              <Text className="mt-1 text-lg font-bold text-green-700">Endpoint Verified!</Text>
            </View>

            <View testID="request-details" className="mb-3 rounded-lg bg-white p-3">
              <Text className="mb-1 text-xs font-bold uppercase tracking-wide text-gray-500">Request Sent</Text>
              <Text className="text-xs text-gray-700">URL: {requestUrl}</Text>
              <Text className="text-xs text-gray-700">Method: {requestMethod}</Text>
              {requestHeaders && (
                <Text className="text-xs text-gray-700">
                  Headers: {JSON.stringify(requestHeaders)}
                </Text>
              )}
            </View>

            <View testID="status-result" className="mb-2 flex-row items-center">
              <Text testID="status-passed" className="mr-2 text-lg">✅</Text>
              <Text className="text-sm text-gray-700">
                Status: Expected {expectedSt}, Got {actualStatus}
              </Text>
            </View>

            {actualHeaders && (
              <View className="mb-2">
                <Text className="text-xs font-medium text-gray-500">Response Headers:</Text>
                <Text className="text-xs text-gray-600">{JSON.stringify(actualHeaders)}</Text>
              </View>
            )}

            {responseBodyPreview && (
              <View testID="response-body" className="mb-2">
                <Text className="text-xs font-medium text-gray-500">Response Body:</Text>
                <Text className="text-xs text-gray-600" selectable>{responseBodyPreview}</Text>
              </View>
            )}

            <View testID="schema-result" className="mb-1 flex-row items-center">
              <Text testID="schema-passed" className="mr-2 text-lg">✅</Text>
              <Text className="text-sm text-gray-700">Schema: Passed</Text>
            </View>
          </View>
        )}

        {verificationStatus === 'failed' && (
          <View testID="verification-failed" className="mb-4 rounded-xl bg-red-50 p-4">
            <View testID="verification-result" className="mb-3 items-center">
              <Text className="text-4xl">❌</Text>
              <Text className="mt-1 text-lg font-bold text-red-700">Verification Failed</Text>
            </View>

            {requestUrl && (
              <View testID="request-details" className="mb-3 rounded-lg bg-white p-3">
                <Text className="mb-1 text-xs font-bold uppercase tracking-wide text-gray-500">Request Sent</Text>
                <Text className="text-xs text-gray-700">URL: {requestUrl}</Text>
                <Text className="text-xs text-gray-700">Method: {requestMethod}</Text>
                {requestHeaders && (
                  <Text className="text-xs text-gray-700">
                    Headers: {JSON.stringify(requestHeaders)}
                  </Text>
                )}
              </View>
            )}

            <View testID="status-result" className="mb-2 flex-row items-center">
              {statusPassed ? (
                <>
                  <Text className="mr-2 text-lg">✅</Text>
                  <Text className="text-sm text-gray-700">Status: Matched</Text>
                </>
              ) : (
                <>
                  <Text testID="status-failed" className="mr-2 text-lg">❌</Text>
                  <Text className="text-sm text-gray-700">
                    Status: Expected {expectedSt}, Got {actualStatus}
                  </Text>
                </>
              )}
            </View>

            {statusFailure && (
              <Text className="mb-2 text-xs text-red-600">{statusFailure}</Text>
            )}

            {responseBodyPreview && (
              <View testID="response-body" className="mb-2">
                <Text className="text-xs font-medium text-gray-500">Response Body:</Text>
                <Text className="text-xs text-gray-600" selectable>{responseBodyPreview}</Text>
              </View>
            )}

            {schemaPassed !== undefined && (
              <View testID="schema-result" className="mb-1 flex-row items-center">
                <Text testID={schemaPassed ? 'schema-passed' : 'schema-failed'} className="mr-2 text-lg">
                  {schemaPassed ? '✅' : '❌'}
                </Text>
                <Text className="text-sm text-gray-700">
                  Schema: {schemaPassed ? 'Passed' : 'Failed'}
                </Text>
              </View>
            )}

            {schemaFailure && (
              <Text className="text-xs text-red-600">{schemaFailure}</Text>
            )}
          </View>
        )}
      </ScrollView>
    </View>
  );
}
