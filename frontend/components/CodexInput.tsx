import { Text, TextInput, View } from 'react-native';

interface Props {
  label?: string;
  value: string;
  onChangeText: (text: string) => void;
  placeholder?: string;
  error?: string | null;
  multiline?: boolean;
  keyboardType?: 'default' | 'numeric';
  editable?: boolean;
  autoCapitalize?: 'none' | 'sentences' | 'words' | 'characters';
  autoCorrect?: boolean;
  monospace?: boolean;
  testID?: string;
  rows?: number;
  /**
   * Mask the value and keep it out of every system-level store: no keyboard
   * learning, no autofill, no password-manager capture. For credentials only.
   */
  secureTextEntry?: boolean;
}

export function CodexInput({
  label,
  value,
  onChangeText,
  placeholder,
  error,
  multiline,
  keyboardType = 'default',
  editable = true,
  autoCapitalize = 'none',
  autoCorrect = false,
  monospace,
  testID,
  rows,
  secureTextEntry = false,
}: Props) {
  return (
    <View className="mb-4">
      {label && (
        <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">
          {label}
        </Text>
      )}
      <TextInput
        testID={testID}
        className={`rounded-sm border ${error ? 'border-codex-accent' : 'border-codex-border'} bg-codex-surface px-4 py-3 font-sans text-base text-codex-text ${multiline ? 'min-h-[80px] text-left' : ''} ${monospace ? 'font-mono' : ''}`}
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor="#85796A"
        keyboardType={keyboardType}
        multiline={multiline}
        editable={editable}
        autoCapitalize={autoCapitalize}
        autoCorrect={autoCorrect}
        secureTextEntry={secureTextEntry}
        // A PAT is not a password to be remembered: opting out of autofill and
        // of the keyboard's learning dictionary keeps it from being stored
        // anywhere outside this one submission.
        autoComplete={secureTextEntry ? 'off' : undefined}
        textContentType={secureTextEntry ? 'none' : undefined}
        spellCheck={secureTextEntry ? false : undefined}
        textAlignVertical={multiline ? 'top' : 'center'}
        numberOfLines={rows}
      />
      {error && (
        <Text testID={testID ? `${testID}-error` : undefined} className="mt-1 font-sans text-xs text-codex-accent">{error}</Text>
      )}
    </View>
  );
}
