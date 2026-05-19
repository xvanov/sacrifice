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
  created_at: string;
  updated_at: string;
}

export interface HealthResponse {
  status: string;
}
