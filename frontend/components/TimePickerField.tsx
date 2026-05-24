import { useCallback, useMemo, useState } from 'react';
import { FlatList, Modal, Platform, Pressable, Text, TextInput, View } from 'react-native';
import { Portal } from './Portal';

interface Props {
  value: Date;
  onChange: (date: Date) => void;
  error?: string | null;
}

const HOURS = Array.from({ length: 24 }, (_, i) => {
  const h = i.toString().padStart(2, '0');
  return `${h}:00`;
});

const OVERLAY_STYLE = {
  position: 'fixed' as const,
  top: 0,
  left: 0,
  right: 0,
  bottom: 0,
  zIndex: 99999,
  alignItems: 'center' as const,
  justifyContent: 'center' as const,
  backgroundColor: 'rgba(0,0,0,0.2)',
};

function formatTime(date: Date): string {
  const h = date.getHours().toString().padStart(2, '0');
  const m = date.getMinutes().toString().padStart(2, '0');
  return `${h}:${m}`;
}

function hourToDate(hourStr: string, base: Date): Date {
  const h = parseInt(hourStr.split(':')[0], 10);
  const d = new Date(base);
  d.setHours(h, 0, 0, 0);
  return d;
}

export function TimePickerField({ value, onChange, error }: Props) {
  const [open, setOpen] = useState(false);
  const [inputText, setInputText] = useState(formatTime(value));
  const [searchFilter, setSearchFilter] = useState('');

  function handleFocus() {
    setInputText('');
    setSearchFilter('');
    setOpen(true);
  }

  const filteredHours = useMemo(() => {
    if (!searchFilter) return HOURS;
    const q = searchFilter.toLowerCase();
    return HOURS.filter((h) => h.includes(q));
  }, [searchFilter]);

  const handleTextChange = useCallback((text: string) => {
    setInputText(text);
    setSearchFilter(text);
    const match = text.match(/^(\d{1,2})/);
    if (match) {
      const h = parseInt(match[1], 10);
      if (h >= 0 && h <= 23) {
        const d = new Date(value);
        d.setHours(h, 0, 0, 0);
        onChange(d);
      }
    }
  }, [value, onChange]);

  const selectHour = useCallback((hourStr: string) => {
    const d = hourToDate(hourStr, value);
    onChange(d);
    setInputText(formatTime(d));
    setSearchFilter('');
    setOpen(false);
  }, [value, onChange]);

  const isSelectedHour = (hourStr: string) => {
    const h = parseInt(hourStr.split(':')[0], 10);
    return value.getHours() === h;
  };

  const dropdown = (
    <View style={{ width: 160 }} className="max-h-64 rounded-sm border border-codex-border bg-codex-surface shadow-sm">
      <FlatList
        data={filteredHours}
        keyExtractor={(item) => item}
        renderItem={({ item }) => (
          <Pressable
            onPress={() => selectHour(item)}
            className={`px-4 py-2.5 ${isSelectedHour(item) ? 'bg-codex-accent' : 'active:bg-codex-bg'}`}
          >
            <Text
              className={`font-sans text-sm ${isSelectedHour(item) ? 'text-codex-surface' : 'text-codex-text'}`}
            >
              {item}
            </Text>
          </Pressable>
        )}
        showsVerticalScrollIndicator={true}
        keyboardShouldPersistTaps="handled"
      />
    </View>
  );

  const overlayBackdrop = Platform.OS === 'web' ? (
    <Portal>
      <View style={OVERLAY_STYLE as any}>
        <Pressable
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
          }}
          onPress={() => setOpen(false)}
        />
        <View style={{ zIndex: 1 }}>
          <Pressable onPress={() => {}}>
            {dropdown}
          </Pressable>
        </View>
      </View>
    </Portal>
  ) : null;

  const nativeModal = Platform.OS !== 'web' ? (
    <Modal transparent animationType="fade" onRequestClose={() => setOpen(false)}>
      <Pressable className="flex-1 items-center justify-center bg-black/30 px-6" onPress={() => setOpen(false)}>
        <Pressable onPress={() => {}}>
          {dropdown}
        </Pressable>
      </Pressable>
    </Modal>
  ) : null;

  return (
    <View className="relative mb-4">
      <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">
        Time
      </Text>
      <TextInput
        testID="deadline-time-input"
        className={`rounded-sm border ${error ? 'border-codex-accent' : 'border-codex-border'} bg-codex-surface px-4 py-3 font-sans text-base text-codex-text`}
        value={inputText}
        onChangeText={handleTextChange}
        onFocus={handleFocus}
        placeholder="HH:00"
        placeholderTextColor="#85796A"
        keyboardType="numeric"
      />
      {open && (Platform.OS === 'web' ? overlayBackdrop : nativeModal)}
      {error && (
        <Text className="mt-1 font-sans text-xs text-codex-accent">{error}</Text>
      )}
    </View>
  );
}
