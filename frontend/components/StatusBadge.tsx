import { Text, View } from 'react-native';

// Single source of truth for how machine states become human copy + color.
// tone: good (green) · bad (oxblood) · progress (gold) · ongoing (ink) · idle (grey)
type Tone = 'good' | 'bad' | 'progress' | 'ongoing' | 'idle';

const TONE_STYLES: Record<Tone, { bg: string; text: string }> = {
  good: { bg: 'bg-codex-success', text: 'text-white' },
  bad: { bg: 'bg-codex-accent', text: 'text-white' },
  progress: { bg: 'bg-codex-warn', text: 'text-white' },
  ongoing: { bg: 'bg-codex-dark-light', text: 'text-codex-bg' },
  idle: { bg: 'bg-codex-border', text: 'text-codex-muted' },
};

const STATUS_META: Record<string, { label: string; tone: Tone }> = {
  draft: { label: 'Draft', tone: 'idle' },
  awaiting_goal_type: { label: 'Building verifier', tone: 'progress' },
  active: { label: 'Active', tone: 'ongoing' },
  pending_review: { label: 'Under review', tone: 'progress' },
  verified: { label: 'Verified', tone: 'good' },
  failed: { label: 'Failed', tone: 'bad' },
  payment_failed: { label: 'Payment failed', tone: 'bad' },
  cancelled: { label: 'Cancelled', tone: 'idle' },
};

function humanize(raw: string): string {
  return raw.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Human label for a goal/verification status. Never returns an underscored enum. */
export function statusLabel(status: string): string {
  return STATUS_META[status]?.label ?? humanize(status);
}

const FALLBACK_TYPE_LABELS: Record<string, { full: string; short: string }> = {
  youtube_video: { full: 'YouTube Video', short: 'YouTube' },
  api_endpoint: { full: 'API Endpoint', short: 'API' },
  dev_sandbox: { full: 'Dev Sandbox', short: 'Sandbox' },
  github_repo: { full: 'GitHub Repo', short: 'GitHub' },
  geolocation: { full: 'Location Check-in', short: 'Location' },
  __generated__: { full: 'Custom (being built)', short: 'Custom' },
};

/**
 * Dynamic override for goal-type labels loaded from /api/goal-types.
 * When set, these take priority over FALLBACK_TYPE_LABELS.
 */
let _dynamicLabels: { full: Record<string, string>; short: Record<string, string> } | null = null;

/** Replace the dynamic label overrides (called by consumers that load /api/goal-types). */
export function setDynamicTypeLabels(labels: { full: Record<string, string>; short: Record<string, string> } | null): void {
  _dynamicLabels = labels;
}

/** Full human label for a goal type (used on detail views). */
export function typeLabel(t: string): string {
  if (_dynamicLabels?.full[t]) return _dynamicLabels.full[t];
  return FALLBACK_TYPE_LABELS[t]?.full ?? humanize(t);
}

/** Compact human label for a goal type (used on cards/lists). */
export function typeLabelShort(t: string): string {
  if (_dynamicLabels?.short[t]) return _dynamicLabels.short[t];
  return FALLBACK_TYPE_LABELS[t]?.short ?? humanize(t);
}

interface Props {
  status: string;
  testID?: string;
}

export function StatusBadge({ status, testID }: Props) {
  const meta = STATUS_META[status];
  const tone = TONE_STYLES[meta?.tone ?? 'idle'];
  const label = meta?.label ?? humanize(status);
  return (
    <View testID={testID} className={`self-start rounded-full px-2.5 py-1 ${tone.bg}`}>
      <Text className={`font-sans-medium text-[10px] uppercase tracking-[0.08em] ${tone.text}`}>
        {label}
      </Text>
    </View>
  );
}
