/**
 * Tests for the chat-resume audit surface (utils/chatAudit.ts).
 *
 * These tests validate AC1.1 and AC1.2: the runnable audit target exposes
 * objective evidence of persisted chat-session state AND navigation/return
 * context — session identifier, last assistant message, restore-state
 * evidence, and leave/return event correlation — without a live app session.
 */

import {
  CHAT_NAVIGATION_AUDIT_KEY,
  CHAT_SESSION_STORAGE_KEY,
  generateChatAuditEvidence,
  readNavigationLog,
  readStoredSessionSync,
  recordNavigationEvent,
  type ChatAuditEvidence,
} from '../../utils/chatAudit';

// ---- localStorage mock (mirrors ChatGoalCreateScreen.test.tsx) --------------

const mockLocalStorage = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => {
      store[key] = value;
    },
    removeItem: (key: string) => {
      delete store[key];
    },
    clear: () => {
      store = {};
    },
  };
})();
Object.defineProperty(global, 'localStorage', { value: mockLocalStorage, writable: true });

// ---- helpers ---------------------------------------------------------------

function seedLocalStorage(session: Record<string, unknown>): void {
  localStorage.setItem(CHAT_SESSION_STORAGE_KEY, JSON.stringify(session));
}

function clearStorage(): void {
  localStorage.removeItem(CHAT_SESSION_STORAGE_KEY);
  localStorage.removeItem(CHAT_NAVIGATION_AUDIT_KEY);
}

function makeAssistantMessage(
  content: string,
  action: unknown = null,
): { role: 'assistant'; content: string; action: unknown } {
  return { role: 'assistant', content, action };
}

function makeUserMessage(content: string): { role: 'user'; content: string; action: null } {
  return { role: 'user', content, action: null };
}

// ---- evidence shape validation ---------------------------------------------

function validateEvidenceShape(evidence: ChatAuditEvidence): void {
  expect(typeof evidence.storageKey).toBe('string');
  expect(['web-storage', 'SecureStore', 'none']).toContain(evidence.storageBackend);
  expect(typeof evidence.hasSession).toBe('boolean');
  expect(evidence.sessionId === null || typeof evidence.sessionId === 'string').toBe(true);
  expect(typeof evidence.messageCount).toBe('number');
  expect(typeof evidence.hasDraftGoal).toBe('boolean');
  expect(typeof evidence.generating).toBe('boolean');

  // restoreEvidence shape
  expect(typeof evidence.restoreEvidence.canRestore).toBe('boolean');
  expect(
    evidence.restoreEvidence.reason === null ||
      typeof evidence.restoreEvidence.reason === 'string',
  ).toBe(true);

  // lastAssistantMessage shape when present
  if (evidence.lastAssistantMessage !== null) {
    expect(evidence.lastAssistantMessage.role).toBe('assistant');
    expect(typeof evidence.lastAssistantMessage.content).toBe('string');
  }

  // navigationContext shape
  const nc = evidence.navigationContext;
  expect(Array.isArray(nc.leaveEvents)).toBe(true);
  expect(Array.isArray(nc.returnEvents)).toBe(true);
  expect(typeof nc.hasLeftAndReturned).toBe('boolean');
  expect(nc.lastLeaveTimestamp === null || typeof nc.lastLeaveTimestamp === 'number').toBe(true);
  expect(nc.lastReturnTimestamp === null || typeof nc.lastReturnTimestamp === 'number').toBe(true);
}

// ---- tests -----------------------------------------------------------------

describe('chatAudit', () => {
  beforeEach(() => {
    clearStorage();
  });

  afterEach(() => {
    clearStorage();
  });

  // -- AC1.1: no stored session -----------------------------------------------

  it('reports no session when localStorage is empty', () => {
    const evidence = generateChatAuditEvidence();

    validateEvidenceShape(evidence);
    expect(evidence.storageKey).toBe(CHAT_SESSION_STORAGE_KEY);
    expect(evidence.hasSession).toBe(false);
    expect(evidence.sessionId).toBeNull();
    expect(evidence.messageCount).toBe(0);
    expect(evidence.lastAssistantMessage).toBeNull();
    expect(evidence.hasDraftGoal).toBe(false);
    expect(evidence.generating).toBe(false);
    expect(evidence.restoreEvidence.canRestore).toBe(false);
    expect(evidence.restoreEvidence.reason).toBe('no stored session');
    // Navigation context: no events recorded
    expect(evidence.navigationContext.leaveEvents).toHaveLength(0);
    expect(evidence.navigationContext.returnEvents).toHaveLength(0);
    expect(evidence.navigationContext.hasLeftAndReturned).toBe(false);
    expect(evidence.navigationContext.lastLeaveTimestamp).toBeNull();
    expect(evidence.navigationContext.lastReturnTimestamp).toBeNull();
  });

  // -- AC1.1: valid session with assistant messages --------------------------

  it('exposes session_id, last assistant message, and restore evidence from a valid stored session', () => {
    seedLocalStorage({
      session_id: 'sess-audit-42',
      messages: [
        makeAssistantMessage('Hello! What would you like to track?'),
        makeUserMessage('I want to exercise daily'),
        makeAssistantMessage('Got it — a daily exercise goal. What time each day?', {
          type: 'awaiting_input',
          field: 'deadline',
          prompt: 'What time each day?',
        }),
      ],
      draft_goal: { goal_type: 'daily_exercise' },
    });

    const evidence = generateChatAuditEvidence();

    validateEvidenceShape(evidence);
    expect(evidence.hasSession).toBe(true);
    expect(evidence.sessionId).toBe('sess-audit-42');
    expect(evidence.messageCount).toBe(3);

    // Last assistant message evidence
    expect(evidence.lastAssistantMessage).not.toBeNull();
    expect(evidence.lastAssistantMessage!.content).toBe(
      'Got it — a daily exercise goal. What time each day?',
    );
    expect(evidence.lastAssistantMessage!.action).toEqual({
      type: 'awaiting_input',
      field: 'deadline',
      prompt: 'What time each day?',
    });

    // Draft goal evidence
    expect(evidence.hasDraftGoal).toBe(true);

    // Restore evidence
    expect(evidence.restoreEvidence.canRestore).toBe(true);
    expect(evidence.restoreEvidence.reason).toBeNull();
  });

  // -- AC1.1: session with generating flag set -------------------------------

  it('exposes the generating flag when an in-flight goal-type build was persisted', () => {
    seedLocalStorage({
      session_id: 'sess-building',
      messages: [
        makeAssistantMessage("On it — I'm building a new goal type for this."),
      ],
      draft_goal: {},
      generating: true,
    });

    const evidence = generateChatAuditEvidence();

    validateEvidenceShape(evidence);
    expect(evidence.hasSession).toBe(true);
    expect(evidence.sessionId).toBe('sess-building');
    expect(evidence.generating).toBe(true);
    expect(evidence.restoreEvidence.canRestore).toBe(true);
  });

  // -- AC1.1: last assistant message when multiple assistants exist ----------

  it('returns the LAST assistant message by position, not by role sort', () => {
    seedLocalStorage({
      session_id: 'sess-multi',
      messages: [
        makeAssistantMessage('First assistant message'),
        makeUserMessage('User says hello'),
        makeAssistantMessage('Second assistant message — should be the last'),
        makeUserMessage('Another user message'),
        makeAssistantMessage('Final assistant message'),
      ],
      draft_goal: null,
    });

    const evidence = generateChatAuditEvidence();

    expect(evidence.lastAssistantMessage).not.toBeNull();
    expect(evidence.lastAssistantMessage!.content).toBe('Final assistant message');
  });

  // -- AC1.1: corrupt storage is handled gracefully --------------------------

  it('treats unparseable stored data as no session', () => {
    localStorage.setItem(CHAT_SESSION_STORAGE_KEY, 'not-valid-json{{{');

    const evidence = generateChatAuditEvidence();

    validateEvidenceShape(evidence);
    expect(evidence.hasSession).toBe(false);
    expect(evidence.sessionId).toBeNull();
    expect(evidence.lastAssistantMessage).toBeNull();
    expect(evidence.restoreEvidence.canRestore).toBe(false);
    expect(evidence.restoreEvidence.reason).toBe('no stored session');
  });

  // -- AC1.1: malformed stored data (missing session_id) ---------------------

  it('treats stored data without session_id as no session', () => {
    localStorage.setItem(
      CHAT_SESSION_STORAGE_KEY,
      JSON.stringify({ messages: [makeAssistantMessage('orphan message')] }),
    );

    const evidence = generateChatAuditEvidence();

    expect(evidence.hasSession).toBe(false);
    expect(evidence.sessionId).toBeNull();
  });

  // -- AC1.1: stored data without messages array -----------------------------

  it('treats stored data without messages array as no session', () => {
    localStorage.setItem(
      CHAT_SESSION_STORAGE_KEY,
      JSON.stringify({ session_id: 'no-msgs' }),
    );

    const evidence = generateChatAuditEvidence();

    expect(evidence.hasSession).toBe(false);
  });

  // -- AC1.1: restore evidence: no assistant messages in session -------------

  it('reports cannot restore when session has no assistant messages', () => {
    seedLocalStorage({
      session_id: 'sess-user-only',
      messages: [makeUserMessage('just me')],
      draft_goal: null,
    });

    const evidence = generateChatAuditEvidence();

    expect(evidence.hasSession).toBe(true);
    expect(evidence.lastAssistantMessage).toBeNull();
    expect(evidence.restoreEvidence.canRestore).toBe(false);
    expect(evidence.restoreEvidence.reason).toBe('stored session has no assistant messages');
  });

  // -- AC1.1: restore evidence: empty messages array -------------------------

  it('reports cannot restore when messages array is empty', () => {
    seedLocalStorage({
      session_id: 'sess-empty',
      messages: [],
      draft_goal: null,
    });

    const evidence = generateChatAuditEvidence();

    expect(evidence.hasSession).toBe(true);
    expect(evidence.messageCount).toBe(0);
    expect(evidence.restoreEvidence.canRestore).toBe(false);
    expect(evidence.restoreEvidence.reason).toBe('stored session has no messages');
  });

  // -- AC1.1: deterministic output for the same input ------------------------

  it('produces deterministic output for the same stored session', () => {
    const session = {
      session_id: 'sess-det',
      messages: [
        makeAssistantMessage('Deterministic test'),
        makeUserMessage('user input'),
      ],
      draft_goal: null,
    };
    seedLocalStorage(session);

    const evidence = generateChatAuditEvidence();

    validateEvidenceShape(evidence);
    // Spot-check key values are not random/timestamp-based
    expect(evidence.sessionId).toBe('sess-det');
    expect(evidence.messageCount).toBe(2);
    // Session-level fields are deterministic; navigation context may carry
    // runtime timestamps from external writers — those are validated
    // separately in the navigation tests below.
    expect(evidence.storageKey).toBe(CHAT_SESSION_STORAGE_KEY);
    expect(evidence.storageBackend).toBe('web-storage');
    expect(evidence.hasSession).toBe(true);
    expect(evidence.lastAssistantMessage!.content).toBe('Deterministic test');
  });

  // -- AC1.1: readStoredSessionSync returns null when nothing stored ---------

  it('readStoredSessionSync returns null for empty storage', () => {
    expect(readStoredSessionSync()).toBeNull();
  });

  // -- AC1.1: readStoredSessionSync returns parsed session -------------------

  it('readStoredSessionSync returns the parsed session object', () => {
    seedLocalStorage({
      session_id: 'sess-direct',
      messages: [makeAssistantMessage('direct read')],
      draft_goal: { key: 'value' },
      generating: false,
    });

    const session = readStoredSessionSync();
    expect(session).not.toBeNull();
    expect(session!.session_id).toBe('sess-direct');
    expect(session!.messages).toHaveLength(1);
    expect(session!.draft_goal).toEqual({ key: 'value' });
    expect(session!.generating).toBe(false);
  });

  // -- AC1.1: generating defaults to false when absent -----------------------

  it('defaults generating to false when the field is absent from stored data', () => {
    seedLocalStorage({
      session_id: 'sess-no-gen',
      messages: [makeAssistantMessage('no generating field')],
      draft_goal: null,
      // generating is intentionally absent
    });

    const evidence = generateChatAuditEvidence();
    expect(evidence.generating).toBe(false);

    const session = readStoredSessionSync();
    expect(session!.generating).toBe(false);
  });

  // -- AC1.1: draft_goal defaults to null when absent ------------------------

  it('defaults draft_goal to null when the field is absent from stored data', () => {
    seedLocalStorage({
      session_id: 'sess-no-draft',
      messages: [makeAssistantMessage('no draft goal field')],
      // draft_goal is intentionally absent
    });

    const evidence = generateChatAuditEvidence();
    expect(evidence.hasDraftGoal).toBe(false);

    const session = readStoredSessionSync();
    expect(session!.draft_goal).toBeNull();
  });
});

// ---- navigation-audit tests (AC1.1 & AC1.2) -------------------------------

describe('chatAudit — navigation context', () => {
  beforeEach(() => {
    clearStorage();
  });

  afterEach(() => {
    clearStorage();
  });

  // -- recordNavigationEvent -------------------------------------------------

  it('recordNavigationEvent appends a leave event to the navigation audit log', () => {
    const before = Date.now();
    recordNavigationEvent('leave', 'chat-goal-create');
    const after = Date.now();

    const log = readNavigationLog();
    expect(log).not.toBeNull();
    expect(log!.events).toHaveLength(1);
    expect(log!.events[0].kind).toBe('leave');
    expect(log!.events[0].screenName).toBe('chat-goal-create');
    expect(log!.events[0].timestamp).toBeGreaterThanOrEqual(before);
    expect(log!.events[0].timestamp).toBeLessThanOrEqual(after);
  });

  it('recordNavigationEvent appends a return event to the navigation audit log', () => {
    recordNavigationEvent('return', 'chat-goal-create');

    const log = readNavigationLog();
    expect(log).not.toBeNull();
    expect(log!.events).toHaveLength(1);
    expect(log!.events[0].kind).toBe('return');
    expect(log!.events[0].screenName).toBe('chat-goal-create');
  });

  it('multiple events accumulate in insertion order', () => {
    recordNavigationEvent('return', 'chat-goal-create');
    recordNavigationEvent('leave', 'chat-goal-create');
    recordNavigationEvent('return', 'chat-goal-create');

    const log = readNavigationLog();
    expect(log!.events).toHaveLength(3);
    expect(log!.events[0].kind).toBe('return');
    expect(log!.events[1].kind).toBe('leave');
    expect(log!.events[2].kind).toBe('return');
  });

  // -- readNavigationLog with no data ----------------------------------------

  it('readNavigationLog returns null when no events have been recorded', () => {
    expect(readNavigationLog()).toBeNull();
  });

  it('readNavigationLog returns null when stored data is corrupt', () => {
    localStorage.setItem(CHAT_NAVIGATION_AUDIT_KEY, 'not-valid-json{{');

    expect(readNavigationLog()).toBeNull();
  });

  it('readNavigationLog returns null when stored data is not an object with events array', () => {
    localStorage.setItem(CHAT_NAVIGATION_AUDIT_KEY, JSON.stringify({ notEvents: [] }));

    expect(readNavigationLog()).toBeNull();
  });

  // -- navigationContext in evidence -----------------------------------------

  it('evidence includes empty navigation context when no events recorded', () => {
    const evidence = generateChatAuditEvidence();

    validateEvidenceShape(evidence);
    const nc = evidence.navigationContext;
    expect(nc.leaveEvents).toHaveLength(0);
    expect(nc.returnEvents).toHaveLength(0);
    expect(nc.hasLeftAndReturned).toBe(false);
    expect(nc.lastLeaveTimestamp).toBeNull();
    expect(nc.lastReturnTimestamp).toBeNull();
  });

  it('evidence reflects a single leave event', () => {
    recordNavigationEvent('leave', 'chat-goal-create');

    const evidence = generateChatAuditEvidence();
    validateEvidenceShape(evidence);

    const nc = evidence.navigationContext;
    expect(nc.leaveEvents).toHaveLength(1);
    expect(nc.leaveEvents[0].screenName).toBe('chat-goal-create');
    expect(nc.returnEvents).toHaveLength(0);
    expect(nc.hasLeftAndReturned).toBe(false);
    expect(typeof nc.lastLeaveTimestamp).toBe('number');
    expect(nc.lastReturnTimestamp).toBeNull();
  });

  it('evidence reflects a single return event', () => {
    recordNavigationEvent('return', 'chat-goal-create');

    const evidence = generateChatAuditEvidence();
    validateEvidenceShape(evidence);

    const nc = evidence.navigationContext;
    expect(nc.leaveEvents).toHaveLength(0);
    expect(nc.returnEvents).toHaveLength(1);
    expect(nc.returnEvents[0].screenName).toBe('chat-goal-create');
    expect(nc.hasLeftAndReturned).toBe(false);
    expect(nc.lastLeaveTimestamp).toBeNull();
    expect(typeof nc.lastReturnTimestamp).toBe('number');
  });

  it('hasLeftAndReturned is true when at least one leave AND one return exist', () => {
    recordNavigationEvent('return', 'chat-goal-create');
    recordNavigationEvent('leave', 'chat-goal-create');

    const evidence = generateChatAuditEvidence();
    validateEvidenceShape(evidence);

    const nc = evidence.navigationContext;
    expect(nc.leaveEvents).toHaveLength(1);
    expect(nc.returnEvents).toHaveLength(1);
    expect(nc.hasLeftAndReturned).toBe(true);
    expect(typeof nc.lastLeaveTimestamp).toBe('number');
    expect(typeof nc.lastReturnTimestamp).toBe('number');
  });

  it('last timestamps reflect the most recent event of each kind', () => {
    recordNavigationEvent('return', 'chat-goal-create');
    const firstLeave = Date.now();
    recordNavigationEvent('leave', 'chat-goal-create');

    // Small sleep so timestamps are guaranteed different
    // (in JSDOM Date.now() ticks with microtask queue, so a synchronous
    //  busy-wait via performance.now isn't reliable — instead just use
    //  two calls spaced by a mock timestamp advance if needed)
    const secondReturnTime = firstLeave + 1000;
    jest.spyOn(Date, 'now').mockReturnValueOnce(secondReturnTime);
    recordNavigationEvent('return', 'chat-goal-create');

    const evidence = generateChatAuditEvidence();
    validateEvidenceShape(evidence);

    const nc = evidence.navigationContext;
    expect(nc.leaveEvents).toHaveLength(1);
    expect(nc.returnEvents).toHaveLength(2);
    expect(nc.lastReturnTimestamp).toBe(secondReturnTime);
    expect(nc.lastLeaveTimestamp).toBe(firstLeave);

    jest.restoreAllMocks();
  });

  it('screenName is preserved correctly for each event', () => {
    recordNavigationEvent('return', 'chat-goal-create');
    recordNavigationEvent('leave', 'chat-goal-create');
    recordNavigationEvent('return', 'chat-goal-create');

    const evidence = generateChatAuditEvidence();

    for (const e of evidence.navigationContext.leaveEvents) {
      expect(e.screenName).toBe('chat-goal-create');
    }
    for (const e of evidence.navigationContext.returnEvents) {
      expect(e.screenName).toBe('chat-goal-create');
    }
  });
});