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
import { api } from '../services/api';
import { useNavigation } from '../hooks/useNavigation';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  action: ChatAction | null;
  timestamp: number;
}

type ChatAction =
  | { type: 'match_proposed'; goal_type: string; confidence: number; missing_criteria: string[] }
  | { type: 'no_match'; suggested_action: string }
  | { type: 'retry' }
  | { type: 'awaiting_input'; field: string; prompt: string }
  | { type: 'ready_to_create'; goal_payload: Record<string, unknown> }
  | null;

export default function ChatGoalCreateScreen() {
  const { navigate, goBack } = useNavigation();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputText, setInputText] = useState('');
  const [sending, setSending] = useState(false);
  const [initializing, setInitializing] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUserMessage, setLastUserMessage] = useState<string>('');
  const [draftGoal, setDraftGoal] = useState<Record<string, unknown> | null>(null);
  const flatListRef = useRef<FlatList>(null);
  const isMounted = useRef(true);

  useEffect(() => {
    return () => { isMounted.current = false; };
  }, []);

  // Initialize session on mount
  useEffect(() => {
    (async () => {
      const result = await api.createChatSession();
      if (!isMounted.current) return;

      if (result.data) {
        setSessionId(result.data.session_id);
        const msgs: ChatMessage[] = result.data.messages.map((m, i) => ({
          id: `msg-${i}`,
          role: m.role as 'user' | 'assistant',
          content: m.content,
          action: (m.action as ChatAction) || null,
          timestamp: Date.now(),
        }));
        setMessages(msgs);
      } else {
        setError(result.error || 'Failed to create chat session');
      }
      setInitializing(false);
    })();
  }, []);

  const sendMessage = useCallback(async (content: string) => {
    if (!sessionId || !content.trim() || sending) return;

    const trimmed = content.trim();
    setInputText('');
    setSending(true);
    setError(null);
    setLastUserMessage(trimmed);

    // Optimistically add user message
    const userMsg: ChatMessage = {
      id: `msg-${Date.now()}`,
      role: 'user',
      content: trimmed,
      action: null,
      timestamp: Date.now(),
    };
    const withUser = [...messages, userMsg];
    setMessages(withUser);

    const result = await api.sendChatMessage(sessionId, trimmed);

    if (!isMounted.current) return;

    if (result.error) {
      // Check if 502 (retryable) — add retry card
      const isRetryable = result.error.includes('502');
      if (isRetryable) {
        const retryMsg: ChatMessage = {
          id: `msg-${Date.now() + 1}`,
          role: 'assistant',
          content: "I'm having trouble understanding right now — try again?",
          action: { type: 'retry' },
          timestamp: Date.now(),
        };
        setMessages([...withUser, retryMsg]);
      } else {
        setError(result.error);
        // Revert user message on non-retryable error
        setMessages(messages);
      }
    } else if (result.data) {
      // Server returns all messages; sync to client
      const serverMsgs: ChatMessage[] = result.data.messages.map((m, i) => ({
        id: `msg-srv-${i}`,
        role: m.role as 'user' | 'assistant',
        content: m.content,
        action: (m.action as ChatAction) || null,
        timestamp: Date.now(),
      }));
      setMessages(serverMsgs);
      setDraftGoal(result.data.draft_goal || null);
      setLastUserMessage('');
    }

    setSending(false);
  }, [sessionId, messages, sending]);

  const handleRetry = useCallback(() => {
    if (lastUserMessage) {
      sendMessage(lastUserMessage);
    }
  }, [lastUserMessage, sendMessage]);

  const handleSend = useCallback(() => {
    sendMessage(inputText);
  }, [inputText, sendMessage]);

  const handleRequestBuild = useCallback(async () => {
    if (!sessionId) return;

    const result = await api.requestNewGoalType(sessionId, {
      prompt_summary: lastUserMessage || 'New goal type',
      goal_payload_draft: draftGoal || {},
      chat_history: messages
        .filter(m => m.role === 'user' || m.role === 'assistant')
        .map(m => ({ role: m.role, content: m.content })),
    });

    if (result.error) {
      const isNotImplemented = result.error.includes('501');
      const stubMsg: ChatMessage = {
        id: `msg-${Date.now()}`,
        role: 'assistant',
        content: isNotImplemented
          ? "Goal-type generation isn't enabled yet — coming in D010."
          : `Failed to request new goal type: ${result.error}`,
        action: null,
        timestamp: Date.now(),
      };
      setMessages(prev => [...prev, stubMsg]);
    }
  }, [sessionId, lastUserMessage, draftGoal, messages]);

  const renderMessage = useCallback(({ item }: { item: ChatMessage }) => {
    const isUser = item.role === 'user';
    const action = item.action;

    return (
      <View className="mb-3 px-4">
        {/* Role label */}
        <Text className={`mb-0.5 font-sans-medium text-xs uppercase tracking-wider ${isUser ? 'text-right text-codex-accent' : 'text-left text-codex-muted'}`}>
          {isUser ? 'You' : 'Assistant'}
        </Text>

        {/* Message bubble */}
        <View className={`rounded-sm p-3 ${isUser ? 'bg-codex-surface border border-codex-border' : 'bg-codex-bg border border-codex-border'}`}>
          <Text className="font-sans text-sm text-codex-text">{item.content}</Text>
        </View>

        {/* Action cards */}
        {action && action.type === 'match_proposed' && (
          <View className="mt-2 rounded-sm border border-codex-accent bg-codex-surface p-3">
            <Text className="font-sans-bold text-sm text-codex-accent">
              Use this goal type: {action.goal_type}
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
                onPress={() => {
                  // For now: acknowledge the match — full conversation
                  // filling happens in D010.
                  const ackMsg: ChatMessage = {
                    id: `msg-${Date.now()}`,
                    role: 'user',
                    content: 'Use this goal type',
                    action: null,
                    timestamp: Date.now(),
                  };
                  setMessages(prev => [...prev, ackMsg]);
                }}
              >
                <Text className="font-sans-medium text-sm text-codex-surface">Use this</Text>
              </Pressable>
            </View>
          </View>
        )}

        {action && action.type === 'no_match' && (
          <View className="mt-2 rounded-sm border border-codex-border bg-codex-surface p-3">
            <Text className="font-sans-bold text-sm text-codex-text">
              I don't have a built-in way to verify that yet
            </Text>
            <Text className="mt-1 font-sans text-xs text-codex-muted">
              Want me to build a new goal type for it?
            </Text>
            <View className="mt-2 flex-row gap-2">
              <Pressable
                testID="yes-build-it"
                className="rounded-sm bg-codex-accent px-3 py-2"
                onPress={handleRequestBuild}
              >
                <Text className="font-sans-medium text-sm text-codex-surface">Yes, build it</Text>
              </Pressable>
              <Pressable
                className="rounded-sm border border-codex-border px-3 py-2"
                onPress={() => {
                  // Let user rephrase — do nothing, they type again
                }}
              >
                <Text className="font-sans-medium text-sm text-codex-text">Let me rephrase</Text>
              </Pressable>
            </View>
          </View>
        )}

        {action && action.type === 'retry' && (
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
  }, [handleRetry, handleRequestBuild]);

  const canSend = inputText.trim().length > 0 && !sending;

  if (initializing) {
    return (
      <View className="flex-1 bg-codex-bg">
        <CodexHeader />
        <View className="flex-1 items-center justify-center">
          <ActivityIndicator size="large" color="#8A2A1C" />
          <Text className="mt-4 font-sans text-sm text-codex-muted">
            Starting chat...
          </Text>
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
              setError(null);
              setInitializing(false);
            }}
          >
            <Text className="font-sans-medium text-base text-codex-surface">Go Back</Text>
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

      {/* Back button row */}
      <View className="border-b border-codex-border px-4 py-2">
        <Pressable onPress={goBack} testID="back-to-home">
          <Text className="font-sans text-sm text-codex-accent">&larr; Back to Home</Text>
        </Pressable>
      </View>

      {/* Message list */}
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

      {/* Typing indicator */}
      {sending && (
        <View className="px-4 py-2">
          <Text className="font-sans text-xs text-codex-muted">Assistant is thinking...</Text>
        </View>
      )}

      {/* Input bar */}
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