import { auth } from './auth';
import type { Goal } from '../types';

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
  getVerificationStatus: (goalId: string) =>
    api.get<{
      submission_id: string;
      verification_status: string;
      verification_details: Record<string, unknown> | null;
    }>(`/api/goals/${goalId}/verification-status`),
};
