import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  Text,
  TextInput,
  View,
} from 'react-native';
import { CodexHeader } from '../components/CodexHeader';
import { CodexFooter } from '../components/CodexFooter';
import { api, type ChatAction as ApiChatAction, type ChatMessage as ApiChatMessage, type GoalTypeInfo } from '../services/api';
import { useNavigation } from '../hooks/useNavigation';
import { MapPicker } from '../components/MapPicker';
import type { Charity } from '../types';

export const CHAT_GOAL_CREATE_SESSION_STORAGE_KEY = 'sacrifice_chat_goal_create_session';

function humanizeGoalTypeName(name: string): string {
  return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

interface ChatMessage extends ApiChatMessage {
  id: string;
  timestamp: number;
}

interface StoredChatSession {
  session_id: string;
  messages: ApiChatMessage[];
  draft_goal: Record<string, unknown> | null;
  // True once a "build a new goal type" request has been accepted, so that
  // returning to this screen resumes polling the generation status.
  generating?: boolean;
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
          generating: parsed.generating === true,
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
  // `building` drives the spinner on the "Yes, build it" button while the
  // request is in flight; `generation` holds the latest generation status so
  // we can show a live progress card and resume polling on return.
  const [building, setBuilding] = useState(false);
  const [generation, setGeneration] = useState<{ status: string; directionId: string } | null>(null);
  // Charity picker: only populated once the assistant asks for a recipient, so
  // we never fetch charities on mount (keeps the send/fetch sequence clean).
  const [charities, setCharities] = useState<Charity[]>([]);
  const [charitiesLoading, setCharitiesLoading] = useState(false);
  const [charitiesLoaded, setCharitiesLoaded] = useState(false);
  // Goal-type registry metadata fetched from /api/goal-types so the
  // match_proposed card renders labels and descriptions from the live
  // registry instead of a hardcoded client-side map.
  const [goalTypesMap, setGoalTypesMap] = useState<Record<string, GoalTypeInfo> | null>(null);
  const [goalTypesLoading, setGoalTypesLoading] = useState(true);
  const [goalTypesError, setGoalTypesError] = useState<string | null>(null);
  const flatListRef = useRef<FlatList>(null);
  const isMounted = useRef(true);

  useEffect(() => {
    return () => {
      isMounted.current = false;
    };
  }, []);

  // Fetch goal-type registry metadata so the match_proposed card renders
  // labels and descriptions from the live registry instead of a hardcoded
  // client-side map.
  useEffect(() => {
    let cancelled = false;
    async function fetchGoalTypes() {
      setGoalTypesLoading(true);
      setGoalTypesError(null);
      const result = await api.listGoalTypes();
      if (cancelled || !isMounted.current) return;
      if (result.data?.goal_types) {
        const map: Record<string, GoalTypeInfo> = {};
        for (const gt of result.data.goal_types) {
          map[gt.name] = gt;
        }
        setGoalTypesMap(map);
      } else {
        setGoalTypesError(result.error || 'Failed to load goal types');
      }
      setGoalTypesLoading(false);
    }
    void fetchGoalTypes();
    return () => { cancelled = true; };
  }, []);

  const initializeSession = useCallback(async () => {
    setInitializing(true);
    setError(null);
    setRetryMessageId(null);

    // "+ New goal" always starts a FRESH conversation. Resuming the stored
    // session here (the old behavior) trapped users in their previous
    // attempt — including sessions whose server row no longer exists, which
    // looped "Session not found" errors. In-flight goal-type builds are not
    // lost by this: the goal already exists in "Building verifier" state and
    // is visible from the home list / goal detail.
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

  // Keep the newest message in view. The FlatList's own size/layout events
  // fire before web finishes laying out affordance cards, so also nudge the
  // scroll shortly after each message batch lands.
  useEffect(() => {
    if (messages.length === 0) return;
    const timer = setTimeout(() => {
      flatListRef.current?.scrollToEnd({ animated: true });
    }, 120);
    return () => clearTimeout(timer);
  }, [messages]);

  // Poll generation status while a build is in flight. Each successful poll
  // sets a fresh `generation` object, which re-runs this effect and schedules
  // the next poll; terminal states (pr_merged/rejected) and 404 (no in-flight
  // generation) stop it.
  useEffect(() => {
    if (!sessionId || !generation) {
      return;
    }
    if (generation.status === 'pr_merged' || generation.status === 'rejected') {
      return;
    }
    let cancelled = false;
    const timer = setTimeout(async () => {
      const res = await api.getGenerationStatus(sessionId);
      if (cancelled || !isMounted.current) {
        return;
      }
      if (res.status === 404) {
        setGeneration(null);
      } else if (res.data) {
        setGeneration({ status: res.data.status, directionId: res.data.direction_id });
      } else {
        // Transient error — keep polling by nudging the object identity.
        setGeneration((current) => (current ? { ...current } : current));
      }
    }, 5000);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [sessionId, generation]);

  // The assistant drives which recipient prompt is active via the last
  // message's action; only then do we surface the tap-to-pick charity bar.
  const lastAction = (messages[messages.length - 1]?.action as ApiChatAction | null) ?? null;
  const awaitingCharity =
    lastAction?.type === 'awaiting_input' && lastAction.field === 'charity_id';
  const awaitingCoordinates =
    lastAction?.type === 'awaiting_input' &&
    (lastAction.field === 'target_latitude' || lastAction.field === 'target_longitude');
  const [locating, setLocating] = useState(false);
  const [locationError, setLocationError] = useState<string | null>(null);
  const [showMapPicker, setShowMapPicker] = useState(false);

  useEffect(() => {
    if (!awaitingCharity || charitiesLoaded || charitiesLoading) {
      return;
    }
    setCharitiesLoading(true);
    api.searchCharities('').then((res) => {
      if (!isMounted.current) return;
      setCharities(res.data ?? []);
      setCharitiesLoaded(true);
      setCharitiesLoading(false);
    });
  }, [awaitingCharity, charitiesLoaded, charitiesLoading]);

  const sendMessage = useCallback(async (content: string) => {
    if (!sessionId || !content.trim() || sending) {
      return;
    }
    setLocationError(null);

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

  // "Use my current location" for coordinate questions: captures browser GPS
  // and sends it as a "lat, lng" reply (the backend parses pairs and fills
  // both axes at once).
  const sendCurrentLocation = useCallback(() => {
    if (Platform.OS !== 'web' || typeof navigator === 'undefined' || !navigator.geolocation) {
      setLocationError('Location is not available here — paste coordinates instead.');
      return;
    }
    setLocating(true);
    setLocationError(null);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocating(false);
        void sendMessage(`${pos.coords.latitude.toFixed(6)}, ${pos.coords.longitude.toFixed(6)}`);
      },
      () => {
        setLocating(false);
        setLocationError(
          'Could not read your location — allow location access or paste coordinates from Google Maps.',
        );
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
    );
  }, [sendMessage]);

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

  const handleCreateGoal = useCallback(async (goalPayload: Record<string, unknown>) => {
    if (!sessionId || sending) {
      return;
    }
    setSending(true);
    const result = await api.createGoalFromChat(sessionId, goalPayload);
    if (!isMounted.current) return;
    const assistantMessage: ChatMessage = {
      id: `msg-${Date.now()}`,
      role: 'assistant',
      content: result.data && !result.error
        ? 'Your goal is created and active. You can track it from the home screen.'
        : `I couldn't create the goal: ${result.error ?? 'unknown error'}`,
      action: null,
      timestamp: Date.now(),
    };
    setMessages((prev) => [...prev, assistantMessage]);
    setSending(false);
  }, [sessionId, sending]);

  const handleRequestBuild = useCallback(async () => {
    if (!sessionId || building) {
      return;
    }

    const chatHistory = messages.map((message) => ({
      role: message.role,
      content: message.content,
    }));
    const promptSummary = [...messages].reverse().find((message) => message.role === 'user')?.content
      || lastUserMessage
      || 'New goal type';

    setBuilding(true);
    let result;
    try {
      result = await api.requestNewGoalType(sessionId, {
        prompt_summary: promptSummary,
        goal_payload_draft: draftGoal || {},
        chat_history: chatHistory,
      });
    } finally {
      if (isMounted.current) {
        setBuilding(false);
      }
    }
    if (!isMounted.current) {
      return;
    }

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
      return;
    }

    // Accepted (202): generation is queued. Confirm to the user, kick off the
    // live status card + polling, and persist `generating` so navigating away
    // and back resumes the progress display.
    const directionId = (result.data?.direction_id as string | undefined) ?? '';
    const successMessage: ChatMessage = {
      id: `msg-${Date.now()}`,
      role: 'assistant',
      content:
        "On it — I'm building a new goal type for this. It can take a few minutes; " +
        "you can leave this screen and come back — the progress is saved here.",
      action: null,
      timestamp: Date.now(),
    };
    const nextMessages = [...messages, successMessage];
    setMessages(nextMessages);
    setGeneration({ status: 'queued', directionId });
    void persistStoredChatSession({
      session_id: sessionId,
      messages: serializeMessages(nextMessages),
      draft_goal: draftGoal,
      generating: true,
    });
  }, [building, draftGoal, lastUserMessage, messages, sessionId]);

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

        {action?.type === 'match_proposed' && (() => {
          const gtInfo = goalTypesMap?.[action.goal_type];
          const displayLabel = gtInfo ? humanizeGoalTypeName(gtInfo.name) : humanizeGoalTypeName(action.goal_type);
          const displayDescription = gtInfo?.description ?? null;
          const samplePrompt = gtInfo?.sample_prompts?.[0] ?? null;
          return (
            <View
              testID={`match-proposed-card-${action.goal_type}`}
              className="mt-2 rounded-sm border border-codex-accent bg-codex-surface p-3"
            >
              <Text className="font-sans-bold text-sm text-codex-accent">Use this goal type</Text>
              <Text className="mt-1 font-sans text-sm text-codex-text">
                Matched type: {displayLabel}
              </Text>
              {displayDescription && (
                <Text className="mt-1 font-sans text-xs text-codex-muted">
                  {displayDescription}
                </Text>
              )}
              {samplePrompt && (
                <Text className="mt-1 font-sans text-xs text-codex-muted">
                  Example: {samplePrompt}
                </Text>
              )}
              {goalTypesLoading && !gtInfo && (
                <Text className="mt-1 font-sans text-xs italic text-codex-muted">
                  Loading type details…
                </Text>
              )}
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
          );
        })()}

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
                disabled={building}
                className={`flex-row items-center gap-2 rounded-sm bg-codex-accent px-3 py-2 ${building ? 'opacity-60' : ''}`}
                onPress={() => {
                  void handleRequestBuild();
                }}
              >
                {building && <ActivityIndicator size="small" color="#fff" testID="build-spinner" />}
                <Text className="font-sans-medium text-sm text-codex-surface">
                  {building ? 'Building…' : 'Yes, build it'}
                </Text>
              </Pressable>
              <Pressable
                disabled={building}
                className={`rounded-sm border border-codex-border px-3 py-2 ${building ? 'opacity-60' : ''}`}
              >
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

        {action?.type === 'ready_to_create' && (
          <View
            testID="ready-to-create-card"
            className="mt-2 rounded-sm border border-codex-accent bg-codex-surface p-3"
          >
            <Text className="font-sans-bold text-sm text-codex-accent">Ready to create</Text>
            {['title', 'deadline', 'pledge_amount'].map((field) =>
              action.goal_payload?.[field] != null ? (
                <Text key={field} className="mt-1 font-sans text-xs text-codex-text">
                  {field}: {String(action.goal_payload[field])}
                </Text>
              ) : null,
            )}
            <View className="mt-2 flex-row gap-2">
              <Pressable
                testID="create-goal-confirm"
                className="rounded-sm bg-codex-accent px-3 py-2"
                onPress={() => {
                  void handleCreateGoal(action.goal_payload ?? {});
                }}
              >
                <Text className="font-sans-medium text-sm text-codex-surface">Create goal</Text>
              </Pressable>
              <Pressable
                testID="create-goal-edit"
                className="rounded-sm border border-codex-border px-3 py-2"
                onPress={() => {
                  void sendMessage('I want to change something');
                }}
              >
                <Text className="font-sans-medium text-sm text-codex-text">Make changes</Text>
              </Pressable>
            </View>
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
  }, [building, goalTypesLoading, goalTypesMap, handleRequestBuild, handleRetry, handleUseThisGoalType, retryMessageId]);

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
        // onContentSizeChange alone misses late layout on web (cards render
        // after the size event), leaving the newest message just off-screen.
        onLayout={() => {
          if (flatListRef.current && messages.length > 0) {
            flatListRef.current.scrollToEnd({ animated: false });
          }
        }}
      />

      {sending && (
        <View className="px-4 py-2">
          <Text className="font-sans text-xs text-codex-muted">Assistant is thinking...</Text>
        </View>
      )}

      {generation && (
        <View
          testID="generation-status-card"
          className="mx-4 mb-2 flex-row items-center gap-2 rounded-sm border border-codex-accent bg-codex-surface px-3 py-2"
        >
          {generation.status !== 'pr_merged' && generation.status !== 'rejected' && (
            <ActivityIndicator size="small" color="#8A2A1C" testID="generation-spinner" />
          )}
          <Text className="flex-1 font-sans text-xs text-codex-text">
            {generation.status === 'pr_merged'
              ? '✓ Your new goal type is built and ready to use.'
              : generation.status === 'rejected'
                ? "Couldn't build that goal type — try rephrasing what you want to track."
                : 'Building your new goal type… you can leave and come back; progress is saved.'}
          </Text>
        </View>
      )}

      {goalTypesError && !goalTypesLoading && (
        <View
          testID="goal-types-error-banner"
          className="mx-4 mb-2 rounded-sm border border-codex-accent bg-codex-surface px-3 py-2"
        >
          <Text className="font-sans text-xs text-codex-accent">
            Couldn't load goal-type details. Some labels may be missing.
          </Text>
        </View>
      )}

      {awaitingCoordinates && !showMapPicker && (
        <View testID="location-helper" className="border-t border-codex-border bg-codex-bg px-4 py-3">
          <View className="flex-row gap-2">
            <Pressable
              testID="use-current-location"
              className="rounded-full border border-codex-accent bg-codex-surface px-3.5 py-2"
              disabled={sending || locating}
              onPress={sendCurrentLocation}
            >
              <Text className="font-sans-medium text-sm text-codex-accent">
                {locating ? 'Locating…' : '📍 Use my current location'}
              </Text>
            </Pressable>
            <Pressable
              testID="open-map-picker"
              className="rounded-full border border-codex-accent bg-codex-surface px-3.5 py-2"
              disabled={sending}
              onPress={() => setShowMapPicker(true)}
            >
              <Text className="font-sans-medium text-sm text-codex-accent">🗺 Pick on map</Text>
            </Pressable>
          </View>
          <Text className="mt-1.5 font-sans text-xs text-codex-muted">
            Or paste coordinates from Google Maps — decimal or 35°53'53"N style both work.
          </Text>
          {locationError && (
            <Text className="mt-1 font-sans text-xs text-codex-accent">{locationError}</Text>
          )}
        </View>
      )}

      {awaitingCoordinates && showMapPicker && (
        <MapPicker
          onConfirm={(lat, lng, radiusM) => {
            setShowMapPicker(false);
            void sendMessage(`${lat.toFixed(6)}, ${lng.toFixed(6)} (radius ${radiusM}m)`);
          }}
          onCancel={() => setShowMapPicker(false)}
        />
      )}

      {awaitingCharity && (
        <View testID="charity-picker" className="border-t border-codex-border bg-codex-bg px-4 py-3">
          <Text className="mb-2 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">
            Choose a recipient
          </Text>
          {charitiesLoading ? (
            <ActivityIndicator size="small" color="#8A2A1C" />
          ) : charities.length === 0 ? (
            <Text className="font-sans text-sm text-codex-muted">
              No recipients yet — add one from the Payments screen, or skip for now.
            </Text>
          ) : (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }}>
              {charities.map((c) => (
                <Pressable
                  key={c.id}
                  testID={`charity-chip-${c.id}`}
                  className="rounded-full border border-codex-accent bg-codex-surface px-3.5 py-2"
                  disabled={sending}
                  onPress={() => void sendMessage(c.name || c.id)}
                >
                  <Text className="font-sans-medium text-sm text-codex-accent">
                    {c.name || c.id}
                  </Text>
                </Pressable>
              ))}
            </ScrollView>
          )}
          <Pressable
            testID="charity-skip"
            className="mt-2 self-start"
            disabled={sending}
            onPress={() => void sendMessage('Skip the recipient for now')}
          >
            <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">
              Skip for now
            </Text>
          </Pressable>
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
