import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  Text,
  TextInput,
  View,
} from 'react-native';
import { CodexHeader } from '../components/CodexHeader';
import { api } from '../services/api';
import { useNavigation } from '../hooks/useNavigation';
import type { ChatMessage, ChatAction } from '../types';

const SESSION_STORAGE_KEY = 'chat_session_id';

function ActionCard({
  action,
  onUseThis,
  onTryAnotherApproach,
  onYesBuildIt,
  onLetMeRephrase,
  onCreateGoal,
  onEdit,
}: {
  action: NonNullable<ChatAction>;
  onUseThis: () => void;
  onTryAnotherApproach: () => void;
  onYesBuildIt: () => void;
  onLetMeRephrase: () => void;
  onCreateGoal: () => void;
  onEdit: () => void;
}) {
  switch (action.type) {
    case 'match_proposed': {
      const goalTypeLabel = action.goal_type.replace(/_/g, ' ');
      return (
        <View className="mt-2 rounded-sm border border-codex-border bg-codex-surface p-4">
          <Text className="mb-1 font-sans-medium text-sm uppercase tracking-wider text-codex-muted">
            Use this goal type
          </Text>
          <Text className="mb-2 font-serif text-base text-codex-text">
            Looks like this is a {goalTypeLabel} goal
          </Text>
          {action.missing_criteria.length > 0 && (
            <Text className="mb-3 font-sans text-xs text-codex-muted">
              Missing: {action.missing_criteria.join(', ')}
            </Text>
          )}
          <View className="flex-row gap-2">
            <Pressable
              className="flex-1 items-center rounded-sm bg-codex-accent px-4 py-2.5"
              onPress={onUseThis}
              accessibilityRole="button"
              accessibilityLabel="Use this"
            >
              <Text className="font-sans-medium text-sm text-codex-surface">Use this</Text>
            </Pressable>
            <Pressable
              className="flex-1 items-center rounded-sm border border-codex-border px-4 py-2.5"
              onPress={onTryAnotherApproach}
              accessibilityRole="button"
              accessibilityLabel="Try another approach"
            >
              <Text className="font-sans text-sm text-codex-muted">Try another approach</Text>
            </Pressable>
          </View>
        </View>
      );
    }

    case 'no_match':
      return (
        <View className="mt-2 rounded-sm border border-codex-border bg-codex-surface p-4">
          <Text className="mb-1 font-sans-medium text-sm uppercase tracking-wider text-codex-muted">
            Build a new goal type
          </Text>
          <Text className="mb-3 font-serif text-base text-codex-text">
            I don't have a built-in way to verify that yet.
          </Text>
          <View className="flex-row gap-2">
            <Pressable
              className="flex-1 items-center rounded-sm bg-codex-accent px-4 py-2.5"
              onPress={onYesBuildIt}
              accessibilityRole="button"
              accessibilityLabel="Yes, build it"
            >
              <Text className="font-sans-medium text-sm text-codex-surface">Yes, build it</Text>
            </Pressable>
            <Pressable
              className="flex-1 items-center rounded-sm border border-codex-border px-4 py-2.5"
              onPress={onLetMeRephrase}
              accessibilityRole="button"
              accessibilityLabel="Let me rephrase"
            >
              <Text className="font-sans text-sm text-codex-muted">Let me rephrase</Text>
            </Pressable>
          </View>
        </View>
      );

    case 'awaiting_input':
      return (
        <View className="mt-2 rounded-sm border border-codex-border bg-codex-surface p-4">
          <Text className="font-sans-medium text-sm uppercase tracking-wider text-codex-muted">
            Awaiting input
          </Text>
          <Text className="mt-1 font-sans text-sm text-codex-text">{action.prompt}</Text>
        </View>
      );

    case 'ready_to_create':
      return (
        <View className="mt-2 rounded-sm border border-codex-border bg-codex-surface p-4">
          <Text className="mb-1 font-sans-medium text-sm uppercase tracking-wider text-codex-muted">
            Final review
          </Text>
          <Text className="mb-3 font-serif text-base text-codex-text">
            {typeof action.goal_payload?.title === 'string'
              ? (action.goal_payload.title as string)
              : 'Your goal is ready'}
          </Text>
          <View className="flex-row gap-2">
            <Pressable
              className="flex-1 items-center rounded-sm bg-codex-accent px-4 py-2.5"
              onPress={onCreateGoal}
              accessibilityRole="button"
              accessibilityLabel="Create goal"
            >
              <Text className="font-sans-medium text-sm text-codex-surface">Create goal</Text>
            </Pressable>
            <Pressable
              className="flex-1 items-center rounded-sm border border-codex-border px-4 py-2.5"
              onPress={onEdit}
              accessibilityRole="button"
              accessibilityLabel="Edit"
            >
              <Text className="font-sans text-sm text-codex-muted">Edit</Text>
            </Pressable>
          </View>
        </View>
      );

    default:
      return null;
  }
}

function ChatBubble({
  message,
  actionHandlers,
}: {
  message: ChatMessage;
  actionHandlers: ReturnType<typeof useActionHandlers>;
}) {
  const isUser = message.role === 'user';

  return (
    <View className={`mb-3 ${isUser ? 'items-end' : 'items-start'}`}>
      <View
        className={`max-w-[80%] rounded-sm px-4 py-3 ${
          isUser ? 'bg-codex-accent' : 'border border-codex-border bg-codex-surface'
        }`}
      >
        <Text className={`font-sans text-sm ${isUser ? 'text-codex-surface' : 'text-codex-text'}`}>
          {message.content}
        </Text>
      </View>
      {message.action && !isUser && (
        <View className="max-w-[80%]">
          <ActionCard action={message.action} {...actionHandlers} />
        </View>
      )}
    </View>
  );
}

function useActionHandlers(
  sessionId: string | null,
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>,
  setTyping: React.Dispatch<React.SetStateAction<boolean>>,
  navigate: ReturnType<typeof useNavigation>['navigate'],
) {
  const handleUseThis = useCallback(async () => {
    if (!sessionId) return;
    const result = await api.sendChatMessage(sessionId, 'Use this goal type');
    if (result.data) {
      setMessages(result.data.messages);
    }
  }, [sessionId, setMessages]);

  const handleTryAnotherApproach = useCallback(async () => {
    if (!sessionId) return;
    const result = await api.sendChatMessage(sessionId, 'Try another approach');
    if (result.data) {
      setMessages(result.data.messages);
    }
  }, [sessionId, setMessages]);

  const handleYesBuildIt = useCallback(async () => {
    if (!sessionId) return;
    setTyping(true);
    const lastUserMsg = 'Build a new goal type for this';
    const result = await api.requestNewGoalType(sessionId, lastUserMsg);
    setTyping(false);

    if (result.error) {
      const detail = result.error.includes('501')
        ? 'Goal-type generation is delivered in D010'
        : result.error;
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: detail, action: null },
      ]);
    } else if (result.data) {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: result.data?.detail || 'Not implemented', action: null },
      ]);
    }
  }, [sessionId, setMessages, setTyping]);

  const handleLetMeRephrase = useCallback(() => {
    // Just let the user type another message — no API call
  }, []);

  const handleCreateGoal = useCallback(async (action: NonNullable<ChatAction>) => {
    if (!sessionId || action.type !== 'ready_to_create') return;
    setTyping(true);
    const result = await api.createGoalFromChat(sessionId, action.goal_payload);
    setTyping(false);

    if (result.data) {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: `Goal created: ${result.data!.goal_id}`, action: null },
      ]);
      navigate({ name: 'goal-detail', goalId: result.data.goal_id });
    } else {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: `Failed to create goal: ${result.error}`, action: null },
      ]);
    }
  }, [sessionId, setMessages, setTyping, navigate]);

  const handleEdit = useCallback(async () => {
    if (!sessionId) return;
    const result = await api.sendChatMessage(sessionId, 'Edit');
    if (result.data) {
      setMessages(result.data.messages);
    }
  }, [sessionId, setMessages]);

  return {
    handleUseThis,
    handleTryAnotherApproach,
    handleYesBuildIt,
    handleLetMeRephrase,
    handleCreateGoal,
    handleEdit,
  };
}

export default function ChatGoalCreateScreen() {
  const { navigate } = useNavigation();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [typing, setTyping] = useState(false);
  const [initializing, setInitializing] = useState(true);
  const flatListRef = useRef<FlatList>(null);

  const actionHandlers = useActionHandlers(sessionId, setMessages, setTyping, navigate);

  // Initialize or resume session
  useEffect(() => {
    (async () => {
      try {
        const storedSessionId = localStorage.getItem(SESSION_STORAGE_KEY);

        if (storedSessionId) {
          const result = await api.getChatSession(storedSessionId);
          if (result.data) {
            setSessionId(result.data.session_id);
            setMessages(result.data.messages);
            setInitializing(false);
            return;
          }
        }

        // Create new session
        const result = await api.createChatSession();
        if (result.data) {
          setSessionId(result.data.session_id);
          setMessages(result.data.messages);
          localStorage.setItem(SESSION_STORAGE_KEY, result.data.session_id);
        }
      } finally {
        setInitializing(false);
      }
    })();
  }, []);

  const isSendDisabled = inputValue.trim() === '';

  const handleSend = useCallback(async () => {
    if (isSendDisabled || !sessionId) return;

    const content = inputValue.trim();
    setInputValue('');
    setTyping(true);

    const userMsg: ChatMessage = { role: 'user', content, action: null };
    setMessages((prev) => [...prev, userMsg]);

    const result = await api.sendChatMessage(sessionId, content);
    setTyping(false);

    if (result.data) {
      setMessages(result.data.messages);
    } else {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: `Error: ${result.error}`, action: null },
      ]);
    }
  }, [sessionId, inputValue, isSendDisabled]);

  // Auto-scroll when messages change
  useEffect(() => {
    if (flatListRef.current && messages.length > 0) {
      setTimeout(() => {
        flatListRef.current?.scrollToEnd({ animated: true });
      }, 100);
    }
  }, [messages]);

  // Wrap action handlers that need access to the specific action
  const onCreateGoalWrapper = useCallback(
    (action: NonNullable<ChatAction>) => {
      actionHandlers.handleCreateGoal(action);
    },
    [actionHandlers],
  );

  const renderMessage = useCallback(
    ({ item }: { item: ChatMessage }) => (
      <ChatBubble
        message={item}
        actionHandlers={{
          ...actionHandlers,
          handleCreateGoal: () => onCreateGoalWrapper(item.action!),
        }}
      />
    ),
    [actionHandlers, onCreateGoalWrapper],
  );

  if (initializing) {
    return (
      <View className="flex-1 bg-codex-bg">
        <CodexHeader />
        <View className="flex-1 items-center justify-center">
          <ActivityIndicator size="large" color="#8A2A1C" />
          <Text className="mt-3 font-sans text-sm text-codex-muted">Loading chat...</Text>
        </View>
      </View>
    );
  }

  return (
    <View className="flex-1 bg-codex-bg">
      <CodexHeader />

      {/* Back navigation */}
      <View className="flex-row items-center border-b border-codex-border px-4 pb-2 pt-2">
        <Pressable onPress={() => navigate({ name: 'home' })} className="mr-3 p-1">
          <Text className="font-serif text-2xl text-codex-muted">{'←'}</Text>
        </Pressable>
        <Text className="font-serif-italic text-lg text-codex-text">Create Goal</Text>
      </View>

      {/* Message list */}
      <FlatList
        testID="chat-message-list"
        ref={flatListRef}
        className="flex-1 px-4 pt-3"
        data={messages}
        keyExtractor={(_item, index) => String(index)}
        renderItem={renderMessage}
        contentContainerStyle={{ paddingBottom: 8 }}
        showsVerticalScrollIndicator={false}
        ListEmptyComponent={
          <View className="flex-1 items-center justify-center py-10">
            <Text className="font-sans text-sm text-codex-muted">No messages yet</Text>
          </View>
        }
      />

      {/* Typing indicator */}
      {typing && (
        <View testID="chat-typing-indicator" className="flex-row items-center px-4 pb-1">
          <ActivityIndicator size="small" color="#8A2A1C" />
          <Text className="ml-2 font-sans text-xs text-codex-muted">Assistant is typing...</Text>
        </View>
      )}

      {/* Composer */}
      <View className="flex-row items-center border-t border-codex-border px-4 py-3">
        <TextInput
          testID="chat-input"
          className="flex-1 rounded-sm border border-codex-border bg-codex-surface px-4 py-3 font-sans text-base text-codex-text"
          value={inputValue}
          onChangeText={setInputValue}
          placeholder="Type your goal..."
          placeholderTextColor="#85796A"
          multiline
          editable={!typing}
          accessibilityLabel="Message"
        />
        <Pressable
          testID="chat-send-button"
          className={`ml-3 items-center rounded-sm px-4 py-3 ${isSendDisabled ? 'bg-codex-border opacity-50' : 'bg-codex-accent'}`}
          onPress={handleSend}
          disabled={isSendDisabled || typing}
          accessibilityRole="button"
          accessibilityLabel="Send"
        >
          <Text className="font-sans-medium text-sm text-codex-surface">Send</Text>
        </Pressable>
      </View>
    </View>
  );
}