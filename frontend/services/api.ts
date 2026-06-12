import { auth } from './auth';
import type { DashboardHistoryItem, DashboardStats, Goal, Notification } from '../types';

export interface GoalTypeInfo {
  name: string;
  description: string;
  sample_prompts: string[];
  criteria_schema: Record<string, unknown>;
}

export interface GoalTypesResponse {
  goal_types: GoalTypeInfo[];
}

const API_BASE_URL = process.env.EXPO_PUBLIC_API_URL || 'http://localhost:8000';

interface ApiResponse<T> {
  data?: T;
  error?: string;
}

async function request<T>(
  endpoint: string,
  options: RequestInit = {},
): Promise<ApiResponse<T>> {
  try {
    const url = `${API_BASE_URL}${endpoint}`;
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
        auth.removeToken();
      }
      const errorBody = await response.text();
      return { error: `HTTP ${response.status}: ${errorBody}` };
    }

    const data = await response.json();
    return { data };
  } catch (err) {
    return { error: err instanceof Error ? err.message : 'Unknown error' };
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
  searchCharities: (query: string) =>
    api.get<Array<{ id: string; name: string; stripe_connect_id: string }>>(
      `/api/charities/search?q=${encodeURIComponent(query)}`,
    ),
  submitProof: (goalId: string, body: { youtube_url: string }) =>
    api.post<{ submission_id: string }>(`/api/goals/${goalId}/submit-proof`, body),
  submitApiEndpointProof: (goalId: string, body: {
    url: string;
    method?: string;
    headers?: Record<string, string>;
    expected_status?: number;
    expected_body_schema?: Record<string, unknown>;
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

  createChatSession: () =>
    api.post<{ session_id: string; messages: Array<{ role: string; content: string; action: unknown }>; status: string }>(
      '/api/chat/sessions', {}
    ),

  sendChatMessage: (sessionId: string, content: string) =>
    api.post<{
      messages: Array<{ role: string; content: string; action: unknown }>;
      draft_goal: Record<string, unknown> | null;
    }>(`/api/chat/sessions/${sessionId}/messages`, { content }),

  requestNewGoalType: (sessionId: string, body: {
    prompt_summary: string;
    goal_payload_draft: Record<string, unknown>;
    chat_history?: Array<{ role: string; content: string }>;
  }) => api.post<unknown>(`/api/chat/sessions/${sessionId}/request-new-goal-type`, body),
};
