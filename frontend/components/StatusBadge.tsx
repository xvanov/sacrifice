import { Text, View } from 'react-native';

const STATUS_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  verified: { bg: 'bg-codex-accent', text: 'text-codex-surface', label: 'Verified' },
  failed: { bg: 'bg-codex-dark', text: 'text-codex-bg', label: 'Failed' },
  active: { bg: 'bg-codex-dark-light', text: 'text-codex-bg', label: 'Active' },
  draft: { bg: 'bg-codex-border', text: 'text-codex-muted', label: 'Draft' },
  pending_review: { bg: 'bg-codex-dark-light', text: 'text-codex-bg', label: 'Pending Review' },
};

interface Props {
  status: string;
  testID?: string;
}

export function StatusBadge({ status, testID }: Props) {
  const style = STATUS_STYLES[status] || { bg: 'bg-codex-border', text: 'text-codex-muted', label: status };
  return (
    <View testID={testID} className={`rounded-sm px-2.5 py-0.5 ${style.bg}`}>
      <Text className={`font-sans text-[10px] tracking-wider ${style.text}`}>
        {style.label}
      </Text>
    </View>
  );
}

export function statusLabel(status: string): string {
  return status.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}
