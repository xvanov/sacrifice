/**
 * Tests for the chat-resume audit surface (utils/chatAudit.ts).
 *
 * These tests validate AC1.1: the runnable audit target exposes objective
 * evidence of persisted chat-session state — session identifier, last
 * assistant message, and restore-state evidence — without a live app session.
 */

import {
  CHAT_SESSION_STORAGE_KEY,
  generateChatAuditEvidence,
  readStoredSessionSync,
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
  expect(typeof evidence.hasDraftInput).toBe('boolean');
  expect(evidence.draftInput === null || typeof evidence.draftInput === 'string').toBe(true);
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
    // action can be anything serializable or null
  }
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

    const first = generateChatAuditEvidence();
    const second = generateChatAuditEvidence();

    expect(first).toEqual(second);
    // Spot-check key values are not random/timestamp-based
    expect(first.sessionId).toBe('sess-det');
    expect(first.messageCount).toBe(2);
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

  // -- AC1.2: draft_input evidence -----------------------------------------

  it('exposes draft_input when present in stored session', () => {
    seedLocalStorage({
      session_id: 'sess-draft-input',
      messages: [makeAssistantMessage('What would you like to track?')],
      draft_goal: null,
      draft_input: 'I want to exercise daily',
    });

    const evidence = generateChatAuditEvidence();
    validateEvidenceShape(evidence);
    expect(evidence.hasDraftInput).toBe(true);
    expect(evidence.draftInput).toBe('I want to exercise daily');
  });

  it('reports hasDraftInput false and draftInput null when draft_input is absent', () => {
    seedLocalStorage({
      session_id: 'sess-no-input',
      messages: [makeAssistantMessage('hello')],
      draft_goal: null,
    });

    const evidence = generateChatAuditEvidence();
    expect(evidence.hasDraftInput).toBe(false);
    expect(evidence.draftInput).toBeNull();
  });

  it('reports hasDraftInput false when draft_input is an empty string', () => {
    seedLocalStorage({
      session_id: 'sess-empty-input',
      messages: [makeAssistantMessage('hello')],
      draft_goal: null,
      draft_input: '',
    });

    const evidence = generateChatAuditEvidence();
    expect(evidence.hasDraftInput).toBe(false);
    expect(evidence.draftInput).toBeNull();
  });
});

