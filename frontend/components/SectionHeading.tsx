import { Text, View } from 'react-native';

interface Props {
  number?: string;
  title: string;
  subtitle?: string;
  className?: string;
}

export function SectionHeading({ number, title, subtitle, className = '' }: Props) {
  return (
    <View className={`mb-6 ${className}`}>
      {number && (
        <Text className="font-sans text-[10px] uppercase tracking-[0.2em] text-codex-accent">
          {number}
        </Text>
      )}
      <Text className="font-serif text-2xl leading-snug text-codex-text">
        {title}
      </Text>
      {subtitle && (
        <Text className="mt-1 font-sans text-sm leading-relaxed text-codex-muted">
          {subtitle}
        </Text>
      )}
    </View>
  );
}
