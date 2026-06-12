import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  Text,
  TextInput,
  View,
} from 'react-native';
import { CodexHeader } from '../components/CodexHeader';
import { CodexFooter } from '../components/CodexFooter';
import { api, type ChatAction as ApiChatAction, type ChatMessage as ApiChatMessage } from '../services/api';
import { useNavigation } from '../hooks/useNavigation';

export const CHAT_GOAL_CREATE_SESSION_STORAGE_KEY = 'sacrifice_chat_goal_create_session';

interface ChatMessage extends ApiChatMessage {
  id: string;
  timestamp: number;
}

interface StoredChatSession {
  session_id: string;
  messages: ApiChatMessage[];
  draft_goal: Record<string, unknown> | null;
}

function getLocalStorage(): Storage | null {
  if (typeof localStorage === 'undefined') {
    return null;
  }

  try {
    return localStorage;
  } catch {
    return null;
  }
}

async function readStoredChatSession(): Promise<StoredChatSession | null> {
  const webStorage = getLocalStorage();

  if (webStorage) {
    try {
      const rawValue = webStorage.getItem(CHAT_GOAL_CREATE_SESSION_STORAGE_KEY);
      if (!rawValue) {
        return null;
      }

      const parsed = JSON.parse(rawValue) as Partial<StoredChatSession>;
      if (typeof parsed.session_id === 'string' && Array.isArray(parsed.messages)) {
        return {
          session_id: parsed.session_id,
          messages: parsed.messages as ApiChatMessage[],
          draft_goal: (parsed.draft_goal as Record<string, unknown> | null | undefined) ?? null,
        };
      }
    } catch {
      webStorage.removeItem(CHAT_GOAL_CREATE_SESSION_STORAGE_KEY);
    }

    return null;
  }

  try {
    const SecureStore = require('expo-secure-store');
    const rawValue = await SecureStore.getItemAsync(CHAT_GOAL_CREATE_SESSION_STORAGE_KEY);
    if (!rawValue) {
      return null;
    }

    const parsed = JSON.parse(rawValue) as Partial<StoredChatSession>;
    if (typeof parsed.session_id === 'string' && Array.isArray(parsed.messages)) {
      return {
        session_id: parsed.session_id,
        messages: parsed.messages as ApiChatMessage[],
        draft_goal: (parsed.draft_goal as Record<string, unknown> | null | undefined) ?? null,
      };
    }
  } catch {
    return null;
  }

  return null;
}

async function persistStoredChatSession(session: StoredChatSession): Promise<void> {
  const serialized = JSON.stringify(session);
  const webStorage = getLocalStorage();

  if (webStorage) {
    try {
      webStorage.setItem(CHAT_GOAL_CREATE_SESSION_STORAGE_KEY, serialized);
    } catch {
      // Ignore storage write failures.
    }
    return;
  }

  try {
    const SecureStore = require('expo-secure-store');
    await SecureStore.setItemAsync(CHAT_GOAL_CREATE_SESSION_STORAGE_KEY, serialized);
  } catch {
    // Ignore storage write failures.
  }
}

function hydrateMessages(nextMessages: ApiChatMessage[], idPrefix: string): ChatMessage[] {
  const baseTimestamp = Date.now();
  return nextMessages.map((message, index) => ({
    id: `${idPrefix}-${baseTimestamp}-${index}`,
    role: message.role,
    content: message.content,
    action: message.action,
    timestamp: baseTimestamp + index,
  }));
}

function serializeMessages(messages: ChatMessage[]): ApiChatMessage[] {
  return messages.map(({ role, content, action }) => ({ role, content, action }));
}

function findLastUserMessage(messages: ApiChatMessage[]): string {
  return [...messages].reverse().find((message) => message.role === 'user')?.content ?? '';
}

export default function ChatGoalCreateScreen() {
  const { goBack } = useNavigation();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputText, setInputText] = useState('');
  const [sending, setSending] = useState(false);
  const [initializing, setInitializing] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUserMessage, setLastUserMessage] = useState('');
  const [draftGoal, setDraftGoal] = useState<Record<string, unknown> | null>(null);
  const [retryMessageId, setRetryMessageId] = useState<string | null>(null);
  const flatListRef = useRef<FlatList>(null);
  const isMounted = useRef(true);

  useEffect(() => {
    return () => {
      isMounted.current = false;
    };
  }, []);

  const initializeSession = useCallback(async () => {
    setInitializing(true);
    setError(null);
    setRetryMessageId(null);

    const storedSession = await readStoredChatSession();
    if (!isMounted.current) {
      return;
    }

    if (storedSession && storedSession.messages.length > 0) {
      setSessionId(storedSession.session_id);
      setMessages(hydrateMessages(storedSession.messages, 'msg-resume'));
      setDraftGoal(storedSession.draft_goal);
      setLastUserMessage(findLastUserMessage(storedSession.messages));
      setInitializing(false);
      return;
    }

    setSessionId(null);
    setMessages([]);
    setDraftGoal(null);
    setLastUserMessage('');

    const result = await api.createChatSession();
    if (!isMounted.current) {
      return;
    }

    if (result.data) {
      setSessionId(result.data.session_id);
      setMessages(hydrateMessages(result.data.messages, 'msg-init'));
      setDraftGoal(null);
      void persistStoredChatSession({
        session_id: result.data.session_id,
        messages: result.data.messages,
        draft_goal: null,
      });
    } else {
      setError(result.error || 'Failed to create chat session');
    }

    setInitializing(false);
  }, []);

  useEffect(() => {
    void initializeSession();
  }, [initializeSession]);

  const sendMessage = useCallback(async (content: string) => {
    if (!sessionId || !content.trim() || sending) {
      return;
    }

    const previousMessages = messages;
    const trimmed = content.trim();
    setInputText('');
    setSending(true);
    setError(null);
    setLastUserMessage(trimmed);

    const optimisticUserMessage: ChatMessage = {
      id: `msg-${Date.now()}`,
      role: 'user',
      content: trimmed,
      action: null,
      timestamp: Date.now(),
    };
    setMessages([...previousMessages, optimisticUserMessage]);

    const result = await api.sendChatMessage(sessionId, trimmed);
    if (!isMounted.current) {
      return;
    }

    if (result.status === 502 && result.data) {
      const nextMessages = hydrateMessages(result.data.messages, 'msg-retry');
      const lastMessage = nextMessages[nextMessages.length - 1];
      const nextDraftGoal = result.data.draft_goal ?? null;
      setMessages(nextMessages);
      setDraftGoal(nextDraftGoal);
      setRetryMessageId(lastMessage?.role === 'assistant' ? lastMessage.id : null);
      setError(null);
      void persistStoredChatSession({
        session_id: sessionId,
        messages: result.data.messages,
        draft_goal: nextDraftGoal,
      });
    } else if (result.error) {
      setError(result.error);
      setRetryMessageId(null);
      setMessages(previousMessages);
    } else if (result.data) {
      const nextMessages = hydrateMessages(result.data.messages, 'msg-send');
      const nextDraftGoal = result.data.draft_goal ?? null;
      setMessages(nextMessages);
      setDraftGoal(nextDraftGoal);
      setRetryMessageId(null);
      setLastUserMessage(findLastUserMessage(result.data.messages));
      setError(null);
      void persistStoredChatSession({
        session_id: sessionId,
        messages: result.data.messages,
        draft_goal: nextDraftGoal,
      });
    }

    setSending(false);
  }, [messages, sending, sessionId]);

  const handleRetry = useCallback(() => {
    if (lastUserMessage) {
      void sendMessage(lastUserMessage);
    }
  }, [lastUserMessage, sendMessage]);

  const handleSend = useCallback(() => {
    void sendMessage(inputText);
  }, [inputText, sendMessage]);

  const handleUseThisGoalType = useCallback((goalType: string) => {
    void sendMessage(`Use this goal type: ${goalType}`);
  }, [sendMessage]);

  const handleRequestBuild = useCallback(async () => {
    if (!sessionId) {
      return;
    }

    const chatHistory = messages.map((message) => ({
      role: message.role,
      content: message.content,
    }));
    const promptSummary = [...messages].reverse().find((message) => message.role === 'user')?.content
      || lastUserMessage
      || 'New goal type';

    const result = await api.requestNewGoalType(sessionId, {
      prompt_summary: promptSummary,
      goal_payload_draft: draftGoal || {},
      chat_history: chatHistory,
    });

    if (result.error) {
      const assistantMessage: ChatMessage = {
        id: `msg-${Date.now()}`,
        role: 'assistant',
        content: result.status === 501
          ? "Goal-type generation isn't enabled yet — coming in D010."
          : `Failed to request new goal type: ${result.error}`,
        action: null,
        timestamp: Date.now(),
      };
      const nextMessages = [...messages, assistantMessage];
      setMessages(nextMessages);
      void persistStoredChatSession({
        session_id: sessionId,
        messages: serializeMessages(nextMessages),
        draft_goal: draftGoal,
      });
    }
  }, [draftGoal, lastUserMessage, messages, sessionId]);

  const renderMessage = useCallback(({ item }: { item: ChatMessage }) => {
    const isUser = item.role === 'user';
    const action = item.action as ApiChatAction | null;

    return (
      <View className="mb-3 px-4">
        <Text
          className={`mb-0.5 font-sans-medium text-xs uppercase tracking-wider ${isUser ? 'text-right text-codex-accent' : 'text-left text-codex-muted'}`}
        >
          {isUser ? 'You' : 'Assistant'}
        </Text>

        <View
          className={`rounded-sm border p-3 ${isUser ? 'border-codex-border bg-codex-surface' : 'border-codex-border bg-codex-bg'}`}
        >
          <Text className="font-sans text-sm text-codex-text">{item.content}</Text>
        </View>

        {action?.type === 'match_proposed' && (
          <View
            testID={`match-proposed-card-${action.goal_type}`}
            className="mt-2 rounded-sm border border-codex-accent bg-codex-surface p-3"
          >
            <Text className="font-sans-bold text-sm text-codex-accent">Use this goal type</Text>
            <Text className="mt-1 font-sans text-sm text-codex-text">
              Matched type: {action.goal_type}
            </Text>
            <Text className="mt-1 font-sans text-xs text-codex-muted">
              Confidence: {(action.confidence * 100).toFixed(0)}%
            </Text>
            {action.missing_criteria.length > 0 && (
              <Text className="mt-1 font-sans text-xs text-codex-text">
                Missing: {action.missing_criteria.join(', ')}
              </Text>
            )}
            <View className="mt-2 flex-row gap-2">
              <Pressable
                testID="use-this-goal-type"
                className="rounded-sm bg-codex-accent px-3 py-2"
                onPress={() => handleUseThisGoalType(action.goal_type)}
              >
                <Text className="font-sans-medium text-sm text-codex-surface">Use this</Text>
              </Pressable>
            </View>
          </View>
        )}

        {action?.type === 'no_match' && (
          <View
            testID="build-new-goal-type-card"
            className="mt-2 rounded-sm border border-codex-border bg-codex-surface p-3"
          >
            <Text className="font-sans-bold text-sm text-codex-text">Build a new goal type</Text>
            <Text className="mt-1 font-sans text-xs text-codex-muted">
              I don't have a built-in way to verify that yet. Want me to build a new goal type for it?
            </Text>
            <View className="mt-2 flex-row gap-2">
              <Pressable
                testID="yes-build-it"
                className="rounded-sm bg-codex-accent px-3 py-2"
                onPress={() => {
                  void handleRequestBuild();
                }}
              >
                <Text className="font-sans-medium text-sm text-codex-surface">Yes, build it</Text>
              </Pressable>
              <Pressable className="rounded-sm border border-codex-border px-3 py-2">
                <Text className="font-sans-medium text-sm text-codex-text">Let me rephrase</Text>
              </Pressable>
            </View>
          </View>
        )}

        {action?.type === 'awaiting_input' && (
          <View
            testID={`awaiting-input-${action.field}`}
            className="mt-2 rounded-sm border border-codex-border bg-codex-surface p-3"
          >
            <Text className="font-sans-bold text-sm text-codex-text">Awaiting input</Text>
            <Text className="mt-1 font-sans text-xs text-codex-muted">{action.prompt}</Text>
          </View>
        )}

        {retryMessageId === item.id && (
          <View className="mt-2 rounded-sm border border-codex-accent bg-codex-surface p-3">
            <Text className="font-sans text-xs text-codex-muted">
              I'm having trouble — want to try again?
            </Text>
            <Pressable
              testID="retry-button"
              className="mt-2 rounded-sm bg-codex-accent px-3 py-2"
              onPress={handleRetry}
            >
              <Text className="font-sans-medium text-sm text-codex-surface">Retry</Text>
            </Pressable>
          </View>
        )}
      </View>
    );
  }, [handleRequestBuild, handleRetry, handleUseThisGoalType, retryMessageId]);

  const canSend = inputText.trim().length > 0 && !sending;

  if (initializing) {
    return (
      <View className="flex-1 bg-codex-bg">
        <CodexHeader />
        <View className="flex-1 items-center justify-center">
          <ActivityIndicator size="large" color="#8A2A1C" />
          <Text className="mt-4 font-sans text-sm text-codex-muted">Starting chat...</Text>
        </View>
        <CodexFooter />
      </View>
    );
  }

  if (error && !sessionId) {
    return (
      <View className="flex-1 bg-codex-bg">
        <CodexHeader />
        <View className="flex-1 items-center justify-center px-6">
          <Text className="mb-2 font-sans text-lg text-codex-accent">Failed to start chat</Text>
          <Text className="mb-6 font-sans text-sm text-codex-muted">{error}</Text>
          <Pressable
            className="rounded-sm bg-codex-accent px-6 py-3"
            onPress={() => {
              void initializeSession();
            }}
          >
            <Text className="font-sans-medium text-base text-codex-surface">Retry</Text>
          </Pressable>
        </View>
        <CodexFooter />
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      className="flex-1 bg-codex-bg"
      testID="chat-goal-create-screen"
    >
      <CodexHeader />

      <View className="border-b border-codex-border px-4 py-2">
        <Pressable onPress={goBack} testID="back-to-home">
          <Text className="font-sans text-sm text-codex-accent">&larr; Back to Home</Text>
        </Pressable>
      </View>

      <FlatList
        ref={flatListRef}
        testID="chat-message-list"
        className="flex-1"
        data={messages}
        keyExtractor={(item) => item.id}
        renderItem={renderMessage}
        contentContainerStyle={{ paddingVertical: 12 }}
        onContentSizeChange={() => {
          if (flatListRef.current && messages.length > 0) {
            flatListRef.current.scrollToEnd({ animated: true });
          }
        }}
      />

      {sending && (
        <View className="px-4 py-2">
          <Text className="font-sans text-xs text-codex-muted">Assistant is thinking...</Text>
        </View>
      )}

      <View className="flex-row items-center gap-2 border-t border-codex-border bg-codex-surface px-4 py-3">
        <TextInput
          testID="chat-input"
          className="flex-1 rounded-sm border border-codex-border bg-codex-bg px-4 py-2.5 font-sans text-sm text-codex-text"
          value={inputText}
          onChangeText={setInputText}
          onSubmitEditing={canSend ? handleSend : undefined}
          placeholder="Tell me what you want to do..."
          placeholderTextColor="#85796A"
          editable={!sending}
          returnKeyType="send"
        />
        <Pressable
          testID="send-button"
          className={`rounded-sm px-5 py-2.5 ${canSend ? 'bg-codex-accent' : 'bg-codex-border'}`}
          onPress={handleSend}
          disabled={!canSend}
        >
          <Text className={`font-sans-medium text-sm ${canSend ? 'text-codex-surface' : 'text-codex-muted'}`}>
            Send
          </Text>
        </Pressable>
      </View>

      <CodexFooter />
    </KeyboardAvoidingView>
  );
}
