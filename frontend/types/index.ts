export interface User {
  id: string;
  email: string;
  display_name: string;
  avatar_url: string | null;
  auth_provider: 'google' | 'github';
  created_at: string;
  updated_at: string;
}

export type GoalType = 'youtube_video' | 'api_endpoint' | 'dev_sandbox';
export type GoalStatus = 'draft' | 'active' | 'pending_review' | 'verified' | 'failed' | 'cancelled';
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

export type GoalCriteriaPayload =
  | GoalCriteriaYouTube
  | GoalCriteriaApiEndpoint
  | GoalCriteriaDevSandbox;

export interface GoalCreatePayload {
  title: string;
  description: string;
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
  stripe_connect_id: string;
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
