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
import { api } from '../services/api';
import { useNavigation } from '../hooks/useNavigation';

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  action: {
    type: string;
    goal_type?: string;
    confidence?: number;
    missing_criteria?: string[];
    suggested_action?: string;
    field?: string;
    prompt?: string;
    goal_payload?: Record<string, unknown>;
  } | null;
}

interface ChatSession {
  session_id: string;
  messages: ChatMessage[];
  status: string;
}

interface SendMessageResponse {
  messages: ChatMessage[];
  draft_goal?: Record<string, unknown>;
}

function ChatBubble({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === 'user';
  return (
    <View className={`mb-3 flex-row ${isUser ? 'justify-end' : 'justify-start'}`}>
      <View
        className={`max-w-[80%] rounded-sm px-4 py-3 ${
          isUser ? 'bg-codex-accent' : 'border border-codex-border bg-codex-surface'
        }`}
      >
        <Text className={`font-sans text-sm ${isUser ? 'text-codex-surface' : 'text-codex-text'}`}>
          {msg.content}
        </Text>
      </View>
    </View>
  );
}

function ActionCard({
  msg,
  onUseThis,
  onTryAnother,
  onBuildIt,
  onRephrase,
  onEdit,
  onCreateGoal,
  onProvideCriterion,
}: {
  msg: ChatMessage;
  onUseThis: () => void;
  onTryAnother: () => void;
  onBuildIt: () => void;
  onRephrase: () => void;
  onEdit: () => void;
  onCreateGoal: (payload: Record<string, unknown>) => void;
  onProvideCriterion: (field: string, prompt: string) => void;
}) {
  const action = msg.action;
  if (!action) return null;

  if (action.type === 'match_proposed') {
    return (
      <View className="mb-4 rounded-sm border border-codex-border bg-codex-surface p-4">
        <Text className="mb-1 font-serif text-base text-codex-text">
          Looks like this is a{' '}
          <Text className="font-sans-bold capitalize">{action.goal_type?.replace(/_/g, ' ')}</Text>{' '}
          goal
        </Text>
        {action.missing_criteria && action.missing_criteria.length > 0 && (
          <Text className="mb-3 font-sans text-xs text-codex-muted">
            Missing: {action.missing_criteria.join(', ')}
          </Text>
        )}
        <View className="flex-row gap-2">
          <Pressable
            className="rounded-sm bg-codex-accent px-4 py-2 active:bg-codex-accent-light"
            onPress={onUseThis}
          >
            <Text className="font-sans-medium text-sm text-codex-surface">Use this</Text>
          </Pressable>
          <Pressable
            className="rounded-sm border border-codex-border px-4 py-2 active:bg-codex-bg"
            onPress={onTryAnother}
          >
            <Text className="font-sans-medium text-sm text-codex-text">Try another approach</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  if (action.type === 'no_match') {
    return (
      <View className="mb-4 rounded-sm border border-codex-border bg-codex-surface p-4">
        <Text className="mb-1 font-serif text-base text-codex-text">
          I don't have a built-in way to verify that yet.
        </Text>
        <Text className="mb-3 font-sans text-xs text-codex-muted">
          Want me to build a new goal type for it?
        </Text>
        <View className="flex-row gap-2">
          <Pressable
            className="rounded-sm bg-codex-accent px-4 py-2 active:bg-codex-accent-light"
            onPress={onBuildIt}
          >
            <Text className="font-sans-medium text-sm text-codex-surface">Yes, build it</Text>
          </Pressable>
          <Pressable
            className="rounded-sm border border-codex-border px-4 py-2 active:bg-codex-bg"
            onPress={onRephrase}
          >
            <Text className="font-sans-medium text-sm text-codex-text">Let me rephrase</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  if (action.type === 'awaiting_input') {
    return (
      <View className="mb-4 rounded-sm border border-codex-border bg-codex-surface p-4">
        <Text className="font-serif text-base text-codex-text">{action.prompt || 'Please provide more information'}</Text>
      </View>
    );
  }

  if (action.type === 'ready_to_create') {
    const payload = action.goal_payload || {};
    return (
      <View className="mb-4 rounded-sm border border-codex-border bg-codex-surface p-4">
        <Text className="mb-2 font-serif text-base text-codex-text">Final review</Text>
        {payload.title && (
          <Text className="mb-1 font-sans text-sm text-codex-text">Title: {String(payload.title)}</Text>
        )}
        {payload.goal_type && (
          <Text className="mb-1 font-sans text-sm text-codex-muted">Type: {String(payload.goal_type)}</Text>
        )}
        <View className="mt-3 flex-row gap-2">
          <Pressable
            className="rounded-sm bg-codex-accent px-4 py-2 active:bg-codex-accent-light"
            onPress={() => onCreateGoal(payload)}
          >
            <Text className="font-sans-medium text-sm text-codex-surface">Create goal</Text>
          </Pressable>
          <Pressable
            className="rounded-sm border border-codex-border px-4 py-2 active:bg-codex-bg"
            onPress={onEdit}
          >
            <Text className="font-sans-medium text-sm text-codex-text">Edit</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  return null;
}

function TypingIndicator() {
  return (
    <View className="mb-3 flex-row justify-start">
      <View className="rounded-sm border border-codex-border bg-codex-surface px-4 py-3">
        <ActivityIndicator size="small" color="#8A2A1C" />
      </View>
    </View>
  );
}

export default function ChatGoalCreateScreen() {
  const { navigate } = useNavigation();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputText, setInputText] = useState('');
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draftGoal, setDraftGoal] = useState<Record<string, unknown> | null>(null);
  const flatListRef = useRef<FlatList<ChatMessage>>(null);

  // Create session on mount
  useEffect(() => {
    let cancelled = false;
    async function init() {
      setLoading(true);
      const result = await api.createChatSession();
      if (cancelled) return;
      if (result.data) {
        setSessionId(result.data.session_id);
        setMessages(result.data.messages);
      } else {
        setError(result.error || 'Failed to start chat session');
      }
      setLoading(false);
    }
    init();
    return () => { cancelled = true; };
  }, []);

  const sendMessage = useCallback(async () => {
    const text = inputText.trim();
    if (!text || !sessionId || sending) return;

    const userMsg: ChatMessage = { role: 'user', content: text, action: null };
    setMessages((prev) => [...prev, userMsg]);
    setInputText('');
    setSending(true);
    setError(null);

    const result = await api.sendChatMessage(sessionId, text);
    if (result.data) {
      setMessages((prev) => {
        // Replace the optimistically added user message with server messages
        // The server returns the full message list for the turn
        return result.data!.messages;
      });
      if (result.data.draft_goal) {
        setDraftGoal(result.data.draft_goal);
      }
    } else {
      setError(result.error || 'Failed to send message');
      // Remove the optimistic user message on error
      setMessages((prev) => prev.filter((m) => m !== userMsg));
    }
    setSending(false);
  }, [inputText, sessionId, sending]);

  const handleCreateGoal = useCallback(async (payload: Record<string, unknown>) => {
    if (!sessionId) return;
    setSending(true);
    const result = await api.createGoalFromChat(sessionId, payload);
    if (result.data) {
      navigate({ name: 'goal-detail', goalId: result.data.goal_id });
    } else {
      setError(result.error || 'Failed to create goal');
      setSending(false);
    }
  }, [sessionId, navigate]);

  const handleBuildNewType = useCallback(async () => {
    if (!sessionId) return;
    setSending(true);
    const result = await api.requestNewGoalType(sessionId, 'Build a new goal type');
    if (result.data) {
      // Stub path — D010 replaces with real flow. 501 means not implemented
    }
    // The 501 detail message gets surfaced as an assistant message
    const msg: ChatMessage = {
      role: 'assistant',
      content: result.error || 'Goal-type generation is delivered in D010',
      action: null,
    };
    setMessages((prev) => [...prev, msg]);
    setSending(false);
  }, [sessionId]);

  const handleUseThis = useCallback(() => {
    // Send an empty-ish acknowledgment that confirms the match
    // The backend handles the state machine from here
    if (!sessionId) return;
    setSending(true);
    api.sendChatMessage(sessionId, 'Use this goal type').then((result) => {
      if (result.data) {
        setMessages(result.data.messages);
        if (result.data.draft_goal) setDraftGoal(result.data.draft_goal);
      }
      setSending(false);
    });
  }, [sessionId]);

  const handleTryAnother = useCallback(() => {
    if (!sessionId) return;
    setSending(true);
    api.sendChatMessage(sessionId, 'Try another approach').then((result) => {
      if (result.data) setMessages(result.data.messages);
      setSending(false);
    });
  }, [sessionId]);

  const handleRephrase = useCallback(() => {
    // Just focus the input — user rephrases naturally
  }, []);

  // Scroll to bottom when messages change
  useEffect(() => {
    if (flatListRef.current && messages.length > 0) {
      setTimeout(() => {
        flatListRef.current?.scrollToEnd({ animated: true });
      }, 100);
    }
  }, [messages]);

  if (loading) {
    return (
      <View className="flex-1 bg-codex-bg">
        <CodexHeader />
        <View className="flex-1 items-center justify-center">
          <ActivityIndicator size="large" color="#8A2A1C" />
          <Text className="mt-4 font-sans text-sm text-codex-muted">Starting chat...</Text>
        </View>
      </View>
    );
  }

  if (error && messages.length === 0) {
    return (
      <View className="flex-1 bg-codex-bg">
        <CodexHeader />
        <View className="flex-1 items-center justify-center px-6">
          <Text className="mb-2 font-sans text-lg text-codex-accent">Connection Error</Text>
          <Text className="mb-6 font-sans text-sm text-codex-muted">{error}</Text>
          <Pressable
            className="rounded-sm bg-codex-accent px-6 py-3"
            onPress={() => {
              setError(null);
              setLoading(true);
              api.createChatSession().then((result) => {
                if (result.data) {
                  setSessionId(result.data.session_id);
                  setMessages(result.data.messages);
                } else {
                  setError(result.error || 'Retry failed');
                }
                setLoading(false);
              });
            }}
          >
            <Text className="font-sans-medium text-base text-codex-surface">Retry</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  return (
    <View className="flex-1 bg-codex-bg">
      <CodexHeader />
      <KeyboardAvoidingView
        className="flex-1"
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={Platform.OS === 'ios' ? 0 : 0}
      >
        <FlatList
          ref={flatListRef}
          className="flex-1 px-4 pt-3"
          data={messages}
          keyExtractor={(_, i) => String(i)}
          renderItem={({ item: msg }) => (
            <>
              <ChatBubble msg={msg} />
              <ActionCard
                msg={msg}
                onUseThis={handleUseThis}
                onTryAnother={handleTryAnother}
                onBuildIt={handleBuildNewType}
                onRephrase={handleRephrase}
                onEdit={handleRephrase}
                onCreateGoal={handleCreateGoal}
                onProvideCriterion={() => {}}
              />
            </>
          )}
          ListFooterComponent={sending ? <TypingIndicator /> : null}
          contentContainerStyle={{ paddingBottom: 8 }}
          showsVerticalScrollIndicator={false}
        />

        {error && messages.length > 0 && (
          <View className="mx-4 mb-1 rounded-sm border border-codex-accent bg-codex-surface px-3 py-2">
            <Text className="font-sans text-xs text-codex-accent">{error}</Text>
          </View>
        )}

        <View className="flex-row items-end gap-2 border-t border-codex-border bg-codex-bg px-4 pb-8 pt-3">
          <TextInput
            className="flex-1 rounded-sm border border-codex-border bg-codex-surface px-4 py-3 font-sans text-base text-codex-text"
            value={inputText}
            onChangeText={setInputText}
            placeholder="Tell me what you want to do..."
            placeholderTextColor="#85796A"
            multiline
            editable={!sending}
            onSubmitEditing={sendMessage}
            returnKeyType="send"
            blurOnSubmit
          />
          <Pressable
            className={`rounded-sm px-4 py-3 ${!inputText.trim() || sending ? 'bg-codex-border' : 'bg-codex-accent active:bg-codex-accent-light'}`}
            onPress={sendMessage}
            disabled={!inputText.trim() || sending}
          >
            <Text className={`font-sans-medium text-sm ${!inputText.trim() || sending ? 'text-codex-muted' : 'text-codex-surface'}`}>
              Send
            </Text>
          </Pressable>
        </View>
      </KeyboardAvoidingView>
    </View>
  );
}