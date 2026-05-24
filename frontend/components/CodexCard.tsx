import type { ReactNode } from 'react';
import { View } from 'react-native';

interface Props {
  children: ReactNode;
  className?: string;
  testID?: string;
}

export function CodexCard({ children, className = '', testID }: Props) {
  return (
    <View testID={testID} className={`rounded-sm border border-codex-border bg-codex-surface ${className}`}>
      {children}
    </View>
  );
}
