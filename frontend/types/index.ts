export interface User {
  id: string;
  email: string;
  display_name: string;
  avatar_url: string | null;
  auth_provider: 'google' | 'github' | 'email';
  created_at: string;
  updated_at: string;
}

export type GoalType = string;
export type GoalStatus =
  | 'draft'
  | 'awaiting_goal_type'
  | 'active'
  | 'pending_review'
  | 'verified'
  | 'failed'
  | 'payment_failed'
  | 'cancelled';
export type Recurrence = 'none' | 'daily' | 'weekly' | 'monthly';

export interface Goal {
  id: string;
  user_id: string;
  title: string;
  description: string;
  goal_type: GoalType;
  pledge_amount: number;
  currency: string;
  deadline: string;
  timezone: string;
  recurrence: Recurrence;
  status: GoalStatus;
  charity_id: string;
  criteria: GoalCriteriaResponse | null;
  created_at: string;
  updated_at: string;
}

export interface GoalCriteriaResponse {
  criteria_type: string;
  criteria_data: Record<string, unknown>;
}

export interface HealthResponse {
  status: string;
}

export interface GoalCriteriaYouTube {
  min_duration_seconds: number;
  video_description: string;
}

export interface GoalCriteriaApiEndpoint {
  method: string;
  url: string;
  headers: Record<string, string>;
  expected_status: number;
  expected_body_schema: Record<string, unknown>;
}

export interface GoalCriteriaDevSandbox {
  repo_url: string;
  branch: string;
  test_command: string;
  language: string;
  env_vars: Record<string, string>;
  goal_description: string;
}

export interface GoalCriteriaGitHub {
  repo_url: string;
  branch: string;
}

export type GoalCriteriaPayload =
  | GoalCriteriaYouTube
  | GoalCriteriaApiEndpoint
  | GoalCriteriaDevSandbox
  | GoalCriteriaGitHub;

export interface GoalCreatePayload {
  title: string;
  description?: string;
  deadline: string;
  pledge_amount: number;
  goal_type: GoalType;
  criteria: GoalCriteriaPayload;
  charity_id: string;
  timezone?: string;
  recurrence?: Recurrence;
  currency?: string;
}

export interface Charity {
  id: string;
  name: string;
  description?: string;
  location?: string;
  // 'stripe' = Connect account (needs onboarding); 'everyorg' = public
  // nonprofit via Every.org (no onboarding needed).
  source?: 'stripe' | 'everyorg' | string;
  stripe_connect_id?: string;
}

export interface ProofSubmissionResponse {
  submission_id: string;
  goal_id: string;
  submitted_at: string;
  verification_status: string;
  verification_details: Record<string, unknown> | null;
}

export interface VerificationStatusResponse {
  submission_id: string;
  verification_status: string;
  verification_details: Record<string, unknown> | null;
}

export interface ApiEndpointProofSubmission {
  url: string;
  method: string;
  headers?: Record<string, string>;
  expected_status?: number;
  expected_body_schema?: Record<string, unknown>;
}

export interface DevSandboxProofSubmission {
  repo_url: string;
  branch: string;
  test_command: string;
  language?: string;
  env_vars?: Record<string, string>;
}

export interface DashboardStats {
  total_goals: number;
  completed_count: number;
  failed_count: number;
  success_rate: number;
  total_pledged: number;
  total_donated: number;
  total_saved: number;
}

export interface DashboardHistoryItem {
  id: string;
  title: string;
  status: GoalStatus;
  goal_type: GoalType;
  pledge_amount: number;
  deadline: string;
  created_at: string;
}

export interface Notification {
  id: string;
  user_id: string;
  goal_id: string | null;
  type: string;
  title: string;
  body: string | null;
  read: boolean;
  created_at: string;
}

export interface ApiEndpointTemplate {
  url: string;
  method: string;
  headers: { key: string; value: string }[];
  expected_status: string;
  expected_body_schema: string;
}
