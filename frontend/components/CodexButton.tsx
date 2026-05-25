import type { ReactNode } from 'react';
import { ActivityIndicator, Pressable, Text } from 'react-native';

interface Props {
  onPress: () => void;
  children: ReactNode;
  variant?: 'primary' | 'secondary' | 'ghost';
  disabled?: boolean;
  loading?: boolean;
  className?: string;
  testID?: string;
}

export function CodexButton({
  onPress,
  children,
  variant = 'primary',
  disabled = false,
  loading = false,
  className = '',
  testID,
}: Props) {
  const baseClass = 'items-center rounded-sm px-6 py-3.5';
  const variantClass = {
    primary: 'bg-codex-accent active:bg-codex-accent-light',
    secondary: 'border border-codex-border bg-codex-surface active:bg-codex-bg',
    ghost: 'active:bg-codex-bg',
  }[variant];
  const disabledClass = disabled ? 'opacity-50' : '';
  const textColor = variant === 'primary' ? 'text-codex-surface' : 'text-codex-text';

  return (
    <Pressable
      testID={testID}
      className={`${baseClass} ${variantClass} ${disabledClass} ${className}`}
      onPress={onPress}
      disabled={disabled || loading}
    >
      {loading ? (
        <ActivityIndicator size="small" color={variant === 'primary' ? '#FFFFFF' : '#0D0B08'} />
      ) : (
        <Text className={`font-sans-medium text-base ${textColor}`}>{children}</Text>
      )}
    </Pressable>
  );
}
