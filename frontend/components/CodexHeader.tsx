import { Image, Text, View } from 'react-native';

const LOGO = require('../assets/logo.png');

interface Props {
  pageNumber?: string;
  totalPages?: string;
}

export function CodexHeader({ pageNumber, totalPages }: Props) {
  return (
    <View className="border-b border-codex-border bg-codex-bg px-6 pb-3 pt-14">
      <View className="flex-row items-center justify-between">
        <Image source={LOGO} style={{ width: 100, height: 24 }} resizeMode="contain" />
        {pageNumber && (
          <Text className="font-sans text-[10px] uppercase tracking-[0.15em] text-codex-muted">
            {pageNumber}{totalPages ? ` · of ${totalPages}` : ''}
          </Text>
        )}
      </View>
    </View>
  );
}
