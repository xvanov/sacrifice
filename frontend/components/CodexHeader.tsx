import type { ReactNode } from 'react';
import { Image, Platform, ScrollView, Text, View } from 'react-native';

const LOGO = require('../assets/logo.png');

interface Props {
  pageNumber?: string;
  totalPages?: string;
  /** Inline controls rendered on the same line as the brand (e.g. the home
   * screen's nav chips) so the top of the app is a single row. */
  children?: ReactNode;
}

export function CodexHeader({ pageNumber, totalPages, children }: Props) {
  return (
    <View
      // The tall top padding exists for the native status bar / notch; on web
      // it just made a huge empty band over a tiny logo.
      className={`border-b border-codex-border bg-codex-bg px-4 pb-2.5 ${
        Platform.OS === 'web' ? 'pt-3' : 'pt-14'
      }`}
    >
      <View className="flex-row items-center justify-between gap-3">
        <View className="flex-row items-center gap-2">
          <Image source={LOGO} style={{ width: 26, height: 26 }} resizeMode="contain" />
          <Text className="font-serif text-xl leading-none text-codex-text">Sacrifice</Text>
        </View>
        {children && (
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            className="flex-1"
            contentContainerStyle={{ gap: 6, alignItems: 'center', flexGrow: 1 }}
          >
            {children}
          </ScrollView>
        )}
        {pageNumber && (
          <Text className="font-sans text-[10px] uppercase tracking-[0.15em] text-codex-muted">
            {pageNumber}{totalPages ? ` · of ${totalPages}` : ''}
          </Text>
        )}
      </View>
    </View>
  );
}
