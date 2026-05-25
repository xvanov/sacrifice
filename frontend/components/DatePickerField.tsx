import { useCallback, useMemo, useState } from 'react';
import { Modal, Platform, Pressable, Text, TextInput, View } from 'react-native';
import { Portal } from './Portal';

interface Props {
  value: Date;
  onChange: (date: Date) => void;
  error?: string | null;
}

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

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

function daysInMonth(year: number, month: number): number {
  return new Date(year, month + 1, 0).getDate();
}

function firstDayOfMonth(year: number, month: number): number {
  return new Date(year, month, 1).getDay();
}

export function DatePickerField({ value, onChange, error }: Props) {
  const [open, setOpen] = useState(false);
  const [inputText, setInputText] = useState(formatIso(value));
  const [navYear, setNavYear] = useState(value.getFullYear());
  const [navMonth, setNavMonth] = useState(value.getMonth());

  function handleFocus() {
    setInputText(formatIso(value));
    setNavYear(value.getFullYear());
    setNavMonth(value.getMonth());
    setOpen(true);
  }

  const handleTextChange = useCallback((text: string) => {
    setInputText(text);
    const parsed = new Date(text);
    if (!isNaN(parsed.getTime())) {
      onChange(parsed);
      setNavYear(parsed.getFullYear());
      setNavMonth(parsed.getMonth());
    }
  }, [onChange]);

  const selectDay = useCallback((day: number) => {
    const d = new Date(navYear, navMonth, day);
    onChange(d);
    setInputText(formatIso(d));
    setOpen(false);
  }, [navYear, navMonth, onChange]);

  const prevMonth = useCallback(() => {
    setNavMonth((m) => {
      if (m === 0) { setNavYear((y) => y - 1); return 11; }
      return m - 1;
    });
  }, []);

  const nextMonth = useCallback(() => {
    setNavMonth((m) => {
      if (m === 11) { setNavYear((y) => y + 1); return 0; }
      return m + 1;
    });
  }, []);

  const calendarDays = useMemo(() => {
    const days: (number | null)[] = [];
    const total = daysInMonth(navYear, navMonth);
    const start = firstDayOfMonth(navYear, navMonth);
    for (let i = 0; i < start; i++) days.push(null);
    for (let d = 1; d <= total; d++) days.push(d);
    return days;
  }, [navYear, navMonth]);

  const isSelectedDay = (day: number) =>
    navYear === value.getFullYear() && navMonth === value.getMonth() && day === value.getDate();

  const calendar = (
    <View style={{ width: 288 }} className="rounded-sm border border-codex-border bg-codex-surface p-3">
      <View className="mb-2 flex-row items-center justify-between">
        <Pressable
          onPress={prevMonth}
          className="h-9 w-9 items-center justify-center rounded-sm active:bg-codex-bg"
          hitSlop={6}
        >
          <Text className="font-sans text-lg text-codex-accent">{'←'}</Text>
        </Pressable>
        <Text className="font-sans-medium text-sm text-codex-text">
          {MONTHS[navMonth]} {navYear}
        </Text>
        <Pressable
          onPress={nextMonth}
          className="h-9 w-9 items-center justify-center rounded-sm active:bg-codex-bg"
          hitSlop={6}
        >
          <Text className="font-sans text-lg text-codex-accent">{'→'}</Text>
        </Pressable>
      </View>
      <View className="flex-row flex-wrap">
        {WEEKDAYS.map((wd) => (
          <View key={wd} className="w-[14.28%] items-center py-1">
            <Text className="font-sans text-[10px] uppercase tracking-wider text-codex-muted">{wd}</Text>
          </View>
        ))}
      </View>
      <View className="flex-row flex-wrap">
        {calendarDays.map((day, i) => (
          <View key={i} className="w-[14.28%] items-center py-1">
            {day !== null ? (
              <Pressable
                onPress={() => selectDay(day)}
                className={`h-8 w-8 items-center justify-center rounded-sm ${
                  isSelectedDay(day) ? 'bg-codex-accent' : 'active:bg-codex-bg'
                }`}
              >
                <Text
                  className={`font-sans text-sm ${
                    isSelectedDay(day) ? 'text-codex-surface' : 'text-codex-text'
                  }`}
                >
                  {day}
                </Text>
              </Pressable>
            ) : null}
          </View>
        ))}
      </View>
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
            {calendar}
          </Pressable>
        </View>
      </View>
    </Portal>
  ) : null;

  const nativeModal = Platform.OS !== 'web' ? (
    <Modal transparent animationType="fade" onRequestClose={() => setOpen(false)}>
      <Pressable className="flex-1 items-center justify-center bg-black/30 px-6" onPress={() => setOpen(false)}>
        <Pressable onPress={() => {}}>
          {calendar}
        </Pressable>
      </Pressable>
    </Modal>
  ) : null;

  return (
    <View className="relative mb-4">
      <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">
        Deadline
      </Text>
      <TextInput
        testID="deadline-date-input"
        className={`rounded-sm border ${error ? 'border-codex-accent' : 'border-codex-border'} bg-codex-surface px-4 py-3 font-sans text-base text-codex-text`}
        value={inputText}
        onChangeText={handleTextChange}
        onFocus={handleFocus}
        placeholder="YYYY-MM-DD"
        placeholderTextColor="#85796A"
      />
      {open && (Platform.OS === 'web' ? overlayBackdrop : nativeModal)}
      {error && (
        <Text className="mt-1 font-sans text-xs text-codex-accent">{error}</Text>
      )}
    </View>
  );
}

function formatIso(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}
