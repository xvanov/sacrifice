import { auth } from './auth';
import { getApiBaseUrl } from '../config';
import type { Charity, DashboardHistoryItem, DashboardStats, Goal, Notification } from '../types';

export interface GoalTypeInfo {
  name: string;
  description: string;
  sample_prompts: string[];
  criteria_schema: Record<string, unknown>;
}

export interface GoalTypesResponse {
  goal_types: GoalTypeInfo[];
}

// API base is resolved per-call via getApiBaseUrl() from config.ts so web
// derives it from the page host (keeps OAuth on a single host) while native
// uses the baked EXPO_PUBLIC_API_URL.

interface ApiResponse<T> {
  status?: number;
  data?: T;
  error?: string;
}

async function request<T>(
  endpoint: string,
  options: RequestInit = {},
): Promise<ApiResponse<T>> {
  try {
    const url = `${getApiBaseUrl()}${endpoint}`;
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...(options.headers as Record<string, string>),
    };
    const token = auth.getToken();
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    const response = await fetch(url, {
      ...options,
      headers,
    });

    if (!response.ok) {
      if (response.status === 401) {
        auth.notifySessionExpired();
      }
      const errorBody = await response.text();
      // Surface the status and any JSON body so callers can render
      // structured error payloads (e.g. the chat 502 retry-card flow,
      // which hydrates messages returned in the 502 body).
      // Only the chat 502 retry-card contract returns a structured body
      // that callers consume; populating data on other error statuses
      // misroutes callers that branch on data-before-error.
      let errorData: T | undefined;
      if (response.status === 502) {
        try {
          errorData = JSON.parse(errorBody) as T;
        } catch {
          errorData = undefined;
        }
      }
      return { status: response.status, data: errorData, error: `HTTP ${response.status}: ${errorBody}` };
    }

    const data = await response.json();
    return { status: response.status, data };
  } catch (err) {
    return { error: err instanceof Error ? err.message : 'Unknown error' };
  }
}

function getDeviceTimezone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    return undefined;
  }
}

export const api = {
  get: <T>(endpoint: string) => request<T>(endpoint, { method: 'GET' }),
  post: <T>(endpoint: string, body: unknown) =>
    request<T>(endpoint, { method: 'POST', body: JSON.stringify(body) }),
  put: <T>(endpoint: string, body: unknown) =>
    request<T>(endpoint, { method: 'PUT', body: JSON.stringify(body) }),
  delete: <T>(endpoint: string) => request<T>(endpoint, { method: 'DELETE' }),

  health: () => api.get<{ status: string }>('/api/health'),
  getGoals: (status?: string) =>
    api.get<Goal[]>(status ? `/api/goals?status=${status}` : '/api/goals'),
  getGoal: (id: string) =>
    api.get<Goal>(`/api/goals/${id}`),
  createGoal: (body: unknown) =>
    api.post<{ id: string }>('/api/goals', body),
  updateGoal: (id: string, body: Record<string, unknown>) =>
    api.put<Goal>(`/api/goals/${id}`, body),
  searchCharities: (query = '') =>
    api.get<Charity[]>(
      `/api/charities/search?q=${encodeURIComponent(query)}`,
    ),
  createCharity: (body: { name: string; email: string }) =>
    api.post<{ id: string; name: string; onboarding_url: string }>('/api/charities', body),
  lookupCharity: (id: string) =>
    api.get<Charity>(`/api/charities/lookup?id=${encodeURIComponent(id)}`),
  submitProof: (goalId: string, body: { youtube_url: string }) =>
    api.post<{ submission_id: string }>(`/api/goals/${goalId}/submit-proof`, body),

  submitMediaProof: async (
    goalId: string,
    file: { uri: string; fileName?: string; type?: string },
    proofMetadata?: Record<string, unknown>,
  ): Promise<ApiResponse<{ submission_id: string; verification_status: string }>> => {
    try {
      const url = `${getApiBaseUrl()}/api/goals/${goalId}/submit-proof`;
      const formData = new FormData();

      const fileName = file.fileName || 'proof.mp4';
      const mimeType = file.type || 'video/mp4';
      formData.append('file', { uri: file.uri, name: fileName, type: mimeType } as any);

      if (proofMetadata) {
        formData.append('proof_metadata', JSON.stringify(proofMetadata));
      }

      const token = auth.getToken();
      const headers: Record<string, string> = {};
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }

      const response = await fetch(url, {
        method: 'POST',
        headers,
        body: formData,
      });

      if (!response.ok) {
        if (response.status === 401) {
          auth.notifySessionExpired();
        }
        const errorBody = await response.text();
        return { status: response.status, error: `HTTP ${response.status}: ${errorBody}` };
      }

      const data = await response.json();
      return { status: response.status, data };
    } catch (err) {
      return { error: err instanceof Error ? err.message : 'Unknown error' };
    }
  },
  submitApiEndpointProof: (goalId: string, body: {
    url: string;
    method?: string;
    headers?: Record<string, string>;
    expected_status?: number;
    expected_body_schema?: Record<string, unknown>;
  }) => api.post<{ submission_id: string }>(`/api/goals/${goalId}/submit-proof`, body),
  submitGeolocationProof: (goalId: string, body: {
    latitude: number;
    longitude: number;
    accuracy_m?: number;
  }) => api.post<{ submission_id: string }>(`/api/goals/${goalId}/submit-proof`, body),
  submitDevSandboxProof: (goalId: string, body: {
    repo_url: string;
    branch?: string;
    test_command?: string;
    language?: string;
    env_vars?: Record<string, string>;
  }) => api.post<{ submission_id: string }>(`/api/goals/${goalId}/submit-proof`, body),
  getDashboardStats: () =>
    api.get<DashboardStats>('/api/dashboard/stats'),
  getDashboardHistory: () =>
    api.get<DashboardHistoryItem[]>('/api/dashboard/history'),
  getNotifications: () =>
    api.get<Notification[]>('/api/notifications'),
  getUnreadCount: () =>
    api.get<{ unread_count: number }>('/api/notifications/unread-count'),
  markNotificationRead: (id: string) =>
    api.put<{ status: string }>(`/api/notifications/${id}/read`, {}),
  markAllNotificationsRead: () =>
    api.put<{ status: string }>('/api/notifications/read-all', {}),

  getVerificationStatus: (goalId: string) =>
    api.get<{
      submission_id: string;
      verification_status: string;
      verification_details: Record<string, unknown> | null;
    }>(`/api/goals/${goalId}/verification-status`),

  getPaymentConfig: () =>
    api.get<{ publishable_key: string }>('/api/payment/config'),

  createSetupIntent: () =>
    api.post<{ client_secret: string }>('/api/payment/setup-intent', {}),

  getPaymentMethods: () =>
    api.get<Array<{
      id: string;
      card: { last4: string; brand: string; exp_month: number; exp_year: number };
      billing_name: string;
    }>>('/api/payment/methods'),

  deletePaymentMethod: (id: string) =>
    api.delete<{ id: string; detached: boolean }>(`/api/payment/methods/${id}`),

  submitGithubProof: (goalId: string, body: {
    repo_url: string;
    branch?: string;
  }) => api.post<{ submission_id: string }>(`/api/goals/${goalId}/submit-proof`, body),

  listGoalTypes: () => api.get<GoalTypesResponse>('/api/goal-types'),

  // Chat session APIs
  createChatSession: () =>
    api.post<ChatSessionResponse>('/api/chat/sessions', {}),

  sendChatMessage: (sessionId: string, content: string) =>
    api.post<ChatMessageResponse>(`/api/chat/sessions/${sessionId}/messages`, {
      content,
      // Deadlines the user types are meant in THEIR timezone.
      timezone: getDeviceTimezone(),
    }),

  createGoalFromChat: (sessionId: string, goalPayload: Record<string, unknown>) =>
    api.post<{ goal_id: string; status: string }>(`/api/chat/sessions/${sessionId}/create-goal`, { goal_payload: goalPayload }),

  requestNewGoalType: (sessionId: string, body: Record<string, unknown>) =>
    api.post<Record<string, unknown>>(`/api/chat/sessions/${sessionId}/request-new-goal-type`, body),

  getGenerationStatus: (sessionId: string) =>
    api.get<GenerationStatus>(`/api/chat/sessions/${sessionId}/generation-status`),

  // Media upload (AC4.2, AC4.3)
  uploadVideo: async (
    file: { uri: string; fileName?: string; type?: string },
    durationSeconds: number,
    goalId?: string,
  ): Promise<ApiResponse<{ upload_id: string; sha256: string; size_bytes: number; duration_seconds: number; mime_type: string }>> => {
    try {
      const url = `${getApiBaseUrl()}/api/uploads/video`;
      const formData = new FormData();

      // On native, expo-camera returns a file:// URI. On web, we may get a Blob.
      // Build the file part: try to use the URI as a blob for web, or use the
      // { uri, name, type } form for React Native's FormData polyfill.
      const fileName = file.fileName || 'recording.mp4';
      const mimeType = file.type || 'video/mp4';

      formData.append('file', {
        uri: file.uri,
        name: fileName,
        type: mimeType,
      } as any);
      formData.append('duration_seconds', String(durationSeconds));
      if (goalId) {
        formData.append('goal_id', goalId);
      }

      const token = auth.getToken();
      const headers: Record<string, string> = {};
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }
      // Don't set Content-Type — browser/runtime sets it with boundary

      const response = await fetch(url, {
        method: 'POST',
        headers,
        body: formData,
      });

      if (!response.ok) {
        if (response.status === 401) {
          auth.notifySessionExpired();
        }
        const errorBody = await response.text();
        return { status: response.status, error: `HTTP ${response.status}: ${errorBody}` };
      }

      const data = await response.json();
      return { status: response.status, data };
    } catch (err) {
      return { error: err instanceof Error ? err.message : 'Unknown error' };
    }
  },
};

export interface GenerationStatus {
  direction_id: string;
  status: 'queued' | 'in_progress' | 'pr_open' | 'pr_merged' | 'rejected' | string;
  pr_url?: string | null;
  summary?: string | null;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  action: ChatAction | null;
}

export type ChatAction =
  | { type: 'match_proposed'; goal_type: string; confidence: number; missing_criteria: string[] }
  | { type: 'no_match'; suggested_action: 'generate_new_goal_type' }
  | { type: 'awaiting_input'; field: string; prompt: string }
  | { type: 'ready_to_create'; goal_payload: Record<string, unknown> };

export interface ChatSessionResponse {
  session_id: string;
  messages: ChatMessage[];
  status: string;
}

export interface ChatMessageResponse {
  messages: ChatMessage[];
  draft_goal?: Record<string, unknown>;
}
