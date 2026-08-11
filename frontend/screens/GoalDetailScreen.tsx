import { useCallback, useEffect, useRef, useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { CodexHeader } from '../components/CodexHeader';
import { CodexCard } from '../components/CodexCard';
import { CodexButton } from '../components/CodexButton';
import { CodexInput } from '../components/CodexInput';
import { DatePickerField } from '../components/DatePickerField';
import { TimePickerField } from '../components/TimePickerField';
import { StatusBadge, typeLabel } from '../components/StatusBadge';
import { formatDateTime, formatMoney, formatTimezone } from '../utils/format';
import { api } from '../services/api';
import { useNavigation } from '../hooks/useNavigation';
import type { Charity, Goal } from '../types';

interface Props {
  goalId: string;
}

function recurrenceLabel(r: string | null): string {
  if (!r || r === 'none') return 'Does not repeat';
  return r.charAt(0).toUpperCase() + r.slice(1);
}

// Criteria keys/values rendered as plain language instead of schema names.
const CRITERIA_LABELS: Record<string, string> = {
  radius_m: 'Allowed radius',
  target_latitude: 'Target latitude',
  target_longitude: 'Target longitude',
  min_duration_seconds: 'Minimum video length',
  expected_status: 'Expected HTTP status',
  expected_body_schema: 'Expected response shape',
  video_description: 'Expected video content',
  repo_url: 'Repository',
  test_command: 'Test command',
  url: 'URL',
  method: 'HTTP method',
};

function criteriaLabel(key: string): string {
  return CRITERIA_LABELS[key] ?? key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function criteriaValue(key: string, value: unknown): string {
  if (key === 'radius_m' && typeof value === 'number') {
    return `${value} m (~${Math.round(value * 3.28084)} ft)`;
  }
  if (key === 'min_duration_seconds' && typeof value === 'number') {
    return value % 60 === 0 ? `${value / 60} min` : `${value} sec`;
  }
  return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <View className="mb-3">
      <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">
        {label}
      </Text>
      <Text className="mt-1 font-sans text-base text-codex-text">{value}</Text>
    </View>
  );
}

function Divider() {
  return <View className="my-3 h-px bg-codex-border" />;
}

function LoadingSkeleton() {
  return (
    <View className="flex-1 bg-codex-bg px-6 pt-3" testID="goal-detail-loading">
      <View className="mb-6 h-7 w-3/4 rounded-sm bg-codex-border" />
      <View className="mb-4 h-20 rounded-sm bg-codex-surface" />
      <View className="mb-3 h-4 w-1/3 rounded-sm bg-codex-border" />
      <View className="mb-3 h-4 w-1/2 rounded-sm bg-codex-surface" />
      <View className="mb-3 h-10 w-full rounded-sm bg-codex-surface" />
    </View>
  );
}

function DetailChrome({
  title,
  children,
  onBack,
}: {
  title: string;
  children: React.ReactNode;
  onBack: () => void;
}) {
  return (
    <View className="flex-1 bg-codex-bg">
      <CodexHeader />
      <View className="flex-row items-center px-6 pb-2 pt-3">
        <Pressable onPress={onBack} className="mr-3 p-1">
          <Text className="font-serif text-2xl text-codex-muted">{'←'}</Text>
        </Pressable>
        <Text className="flex-1 font-serif-italic text-lg text-codex-text" numberOfLines={1}>
          {title}
        </Text>
      </View>
      {children}
    </View>
  );
}

export default function GoalDetailScreen({ goalId }: Props) {
  const { navigate } = useNavigation();
  const [goal, setGoal] = useState<Goal | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [charity, setCharity] = useState<Charity | null>(null);
  const [charityResolved, setCharityResolved] = useState(false);
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [pickingCharity, setPickingCharity] = useState(false);
  const [charityQuery, setCharityQuery] = useState('');
  const [charityResults, setCharityResults] = useState<Charity[]>([]);
  const [charitySearching, setCharitySearching] = useState(false);
  const [charitySaving, setCharitySaving] = useState(false);
  const [charityError, setCharityError] = useState<string | null>(null);

  const isValidGoalId = typeof goalId === 'string' && goalId.length > 0;

  const fetchGoal = useCallback(async () => {
    if (!isValidGoalId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    const result = await api.getGoal(goalId);
    if (result.data) {
      setGoal(result.data);
    } else {
      setError(result.error || 'Failed to load goal');
    }
    setLoading(false);
  }, [goalId, isValidGoalId]);

  useEffect(() => {
    fetchGoal();
  }, [fetchGoal]);

  // Resolve the raw recipient id (acct_… or everyorg:…) to a human name.
  useEffect(() => {
    let cancelled = false;
    if (!goal?.charity_id) {
      setCharity(null);
      setCharityResolved(true);
      return;
    }
    setCharityResolved(false);
    api.lookupCharity(goal.charity_id).then((res) => {
      if (cancelled) return;
      setCharity(res.data ?? null);
      setCharityResolved(true);
    });
    return () => {
      cancelled = true;
    };
  }, [goal?.charity_id]);

  const searchSeq = useRef(0);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Debounced + sequenced: the search fans out to external charity APIs
  // (~1-2s), so firing per keystroke made the picker feel stuck and let
  // stale responses overwrite newer ones.
  const searchRecipients = useCallback((query: string) => {
    if (searchTimer.current) clearTimeout(searchTimer.current);
    setCharitySearching(true);
    setCharityError(null);
    searchTimer.current = setTimeout(async () => {
      const seq = ++searchSeq.current;
      const res = await api.searchCharities(query);
      if (seq !== searchSeq.current) return; // a newer search superseded this one
      setCharityResults(res.data ?? []);
      if (res.error) setCharityError(res.error);
      setCharitySearching(false);
    }, 350);
  }, []);

  const chooseRecipient = useCallback(
    async (charityId: string | null) => {
      if (!goal) return;
      setCharitySaving(true);
      setCharityError(null);
      const res = await api.updateGoal(goal.id, { charity_id: charityId });
      setCharitySaving(false);
      if (res.data) {
        setGoal(res.data);
        setPickingCharity(false);
        setCharityQuery('');
        setCharityResults([]);
      } else {
        setCharityError(res.error?.replace(/^HTTP \d+:\s*/, '') || 'Could not update recipient');
      }
    },
    [goal],
  );

  const [renaming, setRenaming] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const [saving, setSaving] = useState(false);
  const [renameError, setRenameError] = useState<string | null>(null);

  const saveRename = useCallback(async () => {
    if (!goal || !newTitle.trim()) return;
    setSaving(true);
    setRenameError(null);
    const res = await api.updateGoal(goal.id, { title: newTitle.trim() });
    setSaving(false);
    if (res.data) {
      setGoal(res.data);
      setRenaming(false);
    } else {
      setRenameError(res.error?.replace(/^HTTP \d+:\s*/, '') || 'Could not rename this goal');
    }
  }, [goal, newTitle]);

  // Full edit panel: description, deadline, pledge, recurrence.
  const [editing, setEditing] = useState(false);
  const [editDescription, setEditDescription] = useState('');
  const [editDeadline, setEditDeadline] = useState<Date>(new Date());
  const [editPledge, setEditPledge] = useState('');
  const [editRecurrence, setEditRecurrence] = useState('none');
  const [editSaving, setEditSaving] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const startEdit = useCallback(() => {
    if (!goal) return;
    setEditDescription(goal.description || '');
    setEditDeadline(new Date(goal.deadline));
    setEditPledge((goal.pledge_amount / 100).toFixed(2));
    setEditRecurrence(goal.recurrence || 'none');
    setEditError(null);
    setEditing(true);
  }, [goal]);

  const saveEdit = useCallback(async () => {
    if (!goal) return;
    const pledgeCents = Math.round(parseFloat(editPledge || '0') * 100);
    if (!Number.isFinite(pledgeCents) || pledgeCents <= 0) {
      setEditError('Pledge must be a positive amount.');
      return;
    }
    setEditSaving(true);
    setEditError(null);
    // The deadline is left out entirely once it is locked, rather than echoed
    // back: the rest of the panel stays editable in the final hours, and there is
    // nothing to send for a field the owner cannot change.
    const res = await api.updateGoal(goal.id, {
      description: editDescription.trim() || null,
      ...(goal.deadline_locked ? {} : { deadline: editDeadline.toISOString() }),
      pledge_amount: pledgeCents,
      recurrence: editRecurrence,
    });
    setEditSaving(false);
    if (res.data) {
      setGoal(res.data);
      setEditing(false);
    } else {
      setEditError(res.error?.replace(/^HTTP \d+:\s*/, '') || 'Could not save changes');
    }
  }, [goal, editDescription, editDeadline, editPledge, editRecurrence]);

  const handleCancel = useCallback(async () => {
    if (!goal) return;
    setCancelling(true);
    setCancelError(null);
    const res = await api.updateGoal(goal.id, { status: 'cancelled' });
    setCancelling(false);
    if (res.data) {
      setGoal(res.data);
      setConfirmingCancel(false);
    } else {
      setCancelError(res.error || 'Could not cancel this goal.');
    }
  }, [goal]);

  // Hard delete, draft only. Cancelling keeps the record in a terminal state;
  // this removes it outright, which is why it is offered strictly before a goal
  // goes active. The server enforces the same rule — this is the UI half of it.
  const handleDelete = useCallback(async () => {
    if (!goal) return;
    setDeleting(true);
    setDeleteError(null);
    const res = await api.deleteGoal(goal.id);
    setDeleting(false);
    if (res.error) {
      setDeleteError(res.error.replace(/^HTTP \d+:\s*/, '') || 'Could not delete this goal.');
      return;
    }
    // The goal no longer exists, so there is no detail view left to show.
    navigate({ name: 'dashboard' });
  }, [goal, navigate]);

  if (!isValidGoalId) {
    return (
      <DetailChrome title="Not found" onBack={() => navigate({ name: 'home' })}>
        <View className="flex-1 items-center justify-center px-6" testID="goal-detail-invalid-id">
          <CodexCard className="w-full p-4">
            <Text className="mb-3 font-sans text-base text-codex-text">Goal not found.</Text>
            <CodexButton onPress={() => navigate({ name: 'dashboard' })}>
              Back to Dashboard
            </CodexButton>
          </CodexCard>
        </View>
      </DetailChrome>
    );
  }

  if (loading) {
    return (
      <DetailChrome title="Loading…" onBack={() => navigate({ name: 'home' })}>
        <LoadingSkeleton />
      </DetailChrome>
    );
  }

  if (error || !goal) {
    return (
      <DetailChrome title="Error" onBack={() => navigate({ name: 'home' })}>
        <View className="flex-1 items-center justify-center px-6">
          <Text className="mb-4 text-center font-sans text-base text-codex-accent">
            {error || 'Goal not found'}
          </Text>
          <CodexButton onPress={fetchGoal}>Try again</CodexButton>
        </View>
      </DetailChrome>
    );
  }

  const isAwaiting = goal.status === 'awaiting_goal_type';
  const canCancel = goal.status === 'draft' || goal.status === 'awaiting_goal_type';
  // Deliberately narrower than canCancel: deletion destroys the record, so it
  // stops at draft and never extends to an active goal.
  const canDelete = goal.status === 'draft';
  const canEdit = goal.status === 'draft' || goal.status === 'active';

  return (
    <DetailChrome title={goal.title} onBack={() => navigate({ name: 'home' })}>
      <ScrollView className="flex-1 px-6" showsVerticalScrollIndicator={false}>
        {isAwaiting && (
          <CodexCard className="mb-4 border-codex-warn bg-codex-warn-light p-4" testID="awaiting-goal-type-notice">
            <Text className="font-sans-bold text-sm text-codex-text">We're building your verifier</Text>
            <Text className="mt-1.5 font-sans text-sm leading-relaxed text-codex-text-secondary">
              This goal needs a custom way to check your proof, and we're building it now. That can
              take a while. If you'd rather not wait, cancel this goal and create a new one with an
              existing goal type instead.
            </Text>
          </CodexCard>
        )}

        <CodexCard className="mb-6 p-4">
          {/* Title + rename. The API allows title edits on draft/active goals. */}
          <View className="mb-3">
            <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">Title</Text>
            {!renaming ? (
              <View className="mt-1 flex-row items-center justify-between gap-2">
                <Text className="flex-1 font-sans text-base text-codex-text" testID="goal-title">
                  {goal.title}
                </Text>
                {(goal.status === 'draft' || goal.status === 'active') && (
                  <Pressable
                    testID="rename-goal"
                    className="rounded-sm border border-codex-border bg-codex-surface px-2.5 py-1.5"
                    onPress={() => {
                      setNewTitle(goal.title);
                      setRenameError(null);
                      setRenaming(true);
                    }}
                  >
                    <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">Rename</Text>
                  </Pressable>
                )}
              </View>
            ) : (
              <View className="mt-2">
                <CodexInput
                  testID="rename-input"
                  label=""
                  value={newTitle}
                  onChangeText={setNewTitle}
                  placeholder="Goal title"
                />
                {renameError && (
                  <Text className="mb-2 font-sans text-sm text-codex-accent">{renameError}</Text>
                )}
                <View className="flex-row gap-2">
                  <CodexButton
                    testID="rename-save"
                    disabled={saving || !newTitle.trim()}
                    onPress={saveRename}
                  >
                    {saving ? 'Saving…' : 'Save'}
                  </CodexButton>
                  <CodexButton variant="secondary" onPress={() => setRenaming(false)} disabled={saving}>
                    Cancel
                  </CodexButton>
                </View>
              </View>
            )}
          </View>
          <Divider />
          <InfoRow label="Description" value={goal.description || 'No description'} />
          {canEdit && !editing && (
            <Pressable
              testID="edit-goal"
              className="mb-3 self-start rounded-sm border border-codex-border bg-codex-surface px-2.5 py-1.5"
              onPress={startEdit}
            >
              <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">
                Edit details
              </Text>
            </Pressable>
          )}

          {editing && (
            <View testID="edit-panel" className="mb-4 rounded-sm border border-codex-border bg-codex-surface p-3">
              <CodexInput
                testID="edit-description"
                label="Description"
                value={editDescription}
                onChangeText={setEditDescription}
                placeholder="What is this goal about?"
              />
              <Text className="mb-1 mt-2 font-sans text-xs uppercase tracking-wider text-codex-muted">
                Deadline
              </Text>
              {goal.deadline_locked ? (
                <View className="mb-2" testID="deadline-locked-notice">
                  <Text className="font-sans-medium text-sm text-codex-text">
                    {formatDateTime(goal.deadline)}
                  </Text>
                  <Text className="mt-1 font-sans text-xs leading-relaxed text-codex-text-secondary">
                    Locked. Within three hours of a deadline the date is fixed — from here
                    the goal is met with proof, not with a new date.
                  </Text>
                </View>
              ) : (
                <View className="mb-2 flex-row gap-2">
                  <View className="flex-1">
                    <DatePickerField value={editDeadline} onChange={setEditDeadline} />
                  </View>
                  <View className="flex-1">
                    <TimePickerField value={editDeadline} onChange={setEditDeadline} />
                  </View>
                </View>
              )}
              <CodexInput
                testID="edit-pledge"
                label="Pledge (USD)"
                value={editPledge}
                onChangeText={setEditPledge}
                placeholder="5.00"
                keyboardType="numeric"
              />
              <Text className="mb-1 mt-2 font-sans text-xs uppercase tracking-wider text-codex-muted">
                Repeats
              </Text>
              <View className="mb-3 flex-row gap-2">
                {(['none', 'daily', 'weekly', 'monthly'] as const).map((r) => (
                  <Pressable
                    key={r}
                    testID={`recurrence-${r}`}
                    className={`rounded-full px-3 py-1.5 ${
                      editRecurrence === r ? 'bg-codex-accent' : 'border border-codex-border bg-codex-bg'
                    }`}
                    onPress={() => setEditRecurrence(r)}
                  >
                    <Text
                      className={`font-sans-medium text-xs ${
                        editRecurrence === r ? 'text-codex-surface' : 'text-codex-muted'
                      }`}
                    >
                      {r === 'none' ? 'Never' : r.charAt(0).toUpperCase() + r.slice(1)}
                    </Text>
                  </Pressable>
                ))}
              </View>
              {editError && (
                <Text className="mb-2 font-sans text-sm text-codex-accent">{editError}</Text>
              )}
              <View className="flex-row gap-2">
                <CodexButton testID="edit-save" onPress={saveEdit} disabled={editSaving}>
                  {editSaving ? 'Saving…' : 'Save changes'}
                </CodexButton>
                <CodexButton variant="secondary" onPress={() => setEditing(false)} disabled={editSaving}>
                  Cancel
                </CodexButton>
              </View>
            </View>
          )}
          <Divider />

          <View className="mb-3 flex-row">
            <View className="flex-1">
              <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">Status</Text>
              <View className="mt-1.5">
                <StatusBadge status={goal.status} />
              </View>
            </View>
            <View className="flex-1">
              <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">Type</Text>
              <Text className="mt-1 font-sans text-base text-codex-text">{typeLabel(goal.goal_type)}</Text>
            </View>
          </View>

          <Divider />
          <InfoRow label="Pledge Amount" value={formatMoney(goal.pledge_amount, goal.currency)} />
          <Divider />

          {/* Where the money goes if the goal fails. */}
          <View className="mb-3">
            <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">
              Pledge goes to
            </Text>
            {!goal.charity_id ? (
              <Text className="mt-1 font-sans text-base text-codex-accent" testID="charity-none">
                No recipient — the pledge is still charged if you fail
              </Text>
            ) : !charityResolved ? (
              <Text className="mt-1 font-sans text-base text-codex-muted">Looking up recipient…</Text>
            ) : charity ? (
              <Text className="mt-1 font-sans text-base text-codex-text" testID="charity-name">
                {charity.name || 'Unnamed recipient'}
                {charity.source === 'everyorg' ? ' · via Every.org' : ''}
              </Text>
            ) : (
              <Text className="mt-1 font-sans text-sm text-codex-muted" testID="charity-unresolved">
                Recipient not found — it may not have finished setup yet.
              </Text>
            )}

            {(goal.status === 'draft' || goal.status === 'active') && !pickingCharity && (
              <Pressable
                testID="change-recipient"
                className="mt-2 self-start rounded-sm border border-codex-border bg-codex-surface px-2.5 py-1.5"
                onPress={() => {
                  setPickingCharity(true);
                  searchRecipients('');
                }}
              >
                <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">
                  {goal.charity_id ? 'Change recipient' : 'Choose recipient'}
                </Text>
              </Pressable>
            )}

            {pickingCharity && (
              <View testID="recipient-picker" className="mt-3 rounded-sm border border-codex-border bg-codex-surface p-3">
                <CodexInput
                  testID="recipient-search-input"
                  label="Search charities"
                  value={charityQuery}
                  onChangeText={(text: string) => {
                    setCharityQuery(text);
                    searchRecipients(text);
                  }}
                  placeholder="e.g. Doctors Without Borders"
                />
                {charitySearching ? (
                  <Text className="py-2 font-sans text-sm text-codex-muted">Searching…</Text>
                ) : charityResults.length === 0 ? (
                  <Text className="py-2 font-sans text-sm text-codex-muted">
                    {charityQuery ? 'No charities found.' : 'Type to search every registered nonprofit.'}
                  </Text>
                ) : (
                  charityResults.slice(0, 6).map((c) => (
                    <Pressable
                      key={c.id}
                      testID={`recipient-option-${c.id}`}
                      className="border-b border-codex-border py-2 active:bg-codex-bg"
                      onPress={() => void chooseRecipient(c.id)}
                      disabled={charitySaving}
                    >
                      <Text className="font-sans text-sm text-codex-text">{c.name}</Text>
                      <Text className="font-sans text-xs text-codex-muted">
                        {c.source === 'pledge'
                          ? `Public nonprofit · donates automatically${c.location ? ` · ${c.location}` : ''}`
                          : c.source === 'everyorg'
                            ? `Public nonprofit · donation link${c.location ? ` · ${c.location}` : ''}`
                            : 'Platform recipient (Stripe)'}
                      </Text>
                    </Pressable>
                  ))
                )}
                {charityError && (
                  <Text className="pt-2 font-sans text-sm text-codex-accent">{charityError}</Text>
                )}
                <View className="mt-2 flex-row gap-2">
                  {goal.charity_id && (
                    <Pressable
                      testID="remove-recipient"
                      className="rounded-sm border border-codex-border px-2.5 py-1.5"
                      onPress={() => void chooseRecipient(null)}
                      disabled={charitySaving}
                    >
                      <Text className="font-sans text-xs uppercase tracking-wider text-codex-dark">Remove recipient</Text>
                    </Pressable>
                  )}
                  <Pressable
                    testID="close-recipient-picker"
                    className="rounded-sm border border-codex-border px-2.5 py-1.5"
                    onPress={() => setPickingCharity(false)}
                  >
                    <Text className="font-sans text-xs uppercase tracking-wider text-codex-muted">Close</Text>
                  </Pressable>
                </View>
              </View>
            )}
          </View>

          <Divider />
          <InfoRow label="Deadline" value={formatDateTime(goal.deadline)} />
          <Divider />
          <InfoRow label="Timezone" value={formatTimezone(goal.timezone)} />
          <Divider />
          <InfoRow label="Recurrence" value={recurrenceLabel(goal.recurrence)} />

          {goal.criteria && (
            <>
              <Divider />
              <Text className="mb-2 font-sans text-xs uppercase tracking-wider text-codex-muted">
                Verification details
              </Text>
              {Object.entries(goal.criteria.criteria_data).map(([key, value]) => (
                <View key={key} className="mb-1.5">
                  <Text className="font-sans text-xs text-codex-muted">
                    {criteriaLabel(key)}
                  </Text>
                  <Text className="font-sans text-sm text-codex-text">
                    {criteriaValue(key, value)}
                  </Text>
                </View>
              ))}
            </>
          )}

          <Divider />
          <InfoRow label="Created" value={formatDateTime(goal.created_at)} />
          <InfoRow label="Updated" value={formatDateTime(goal.updated_at)} />
        </CodexCard>

        {goal.status === 'active' && (
          <CodexButton
            testID="submit-proof-button"
            onPress={() => {
              if (goal.goal_type === 'api_endpoint') {
                navigate({ name: 'api-endpoint-proof-submission', goalId: goal.id });
              } else if (goal.goal_type === 'geolocation') {
                navigate({ name: 'geolocation-proof-submission', goalId: goal.id });
              } else if (goal.goal_type === 'dev_sandbox' || goal.goal_type === 'github_repo') {
                navigate({ name: 'dev-sandbox-proof-submission', goalId: goal.id });
              } else {
                navigate({ name: 'proof-submission', goalId: goal.id });
              }
            }}
            variant="primary"
            className="mb-6"
          >
            Submit Proof
          </CodexButton>
        )}

        {canCancel && (
          <View className="mb-8">
            {!confirmingCancel ? (
              <CodexButton
                testID="cancel-goal-button"
                variant="secondary"
                onPress={() => {
                  setCancelError(null);
                  setConfirmingCancel(true);
                }}
              >
                Cancel this goal
              </CodexButton>
            ) : (
              <CodexCard className="border-codex-accent p-4">
                <Text className="font-sans-bold text-sm text-codex-text">Cancel this goal?</Text>
                <Text className="mt-1 font-sans text-sm leading-relaxed text-codex-text-secondary">
                  This stops the goal for good. You won't be charged, and it can't be resumed.
                </Text>
                {cancelError && (
                  <Text testID="cancel-goal-error" className="mt-2 font-sans text-sm text-codex-accent">
                    {cancelError}
                  </Text>
                )}
                <View className="mt-3 flex-row gap-2">
                  <View className="flex-1">
                    <CodexButton
                      testID="cancel-goal-confirm"
                      variant="primary"
                      loading={cancelling}
                      onPress={handleCancel}
                    >
                      Yes, cancel it
                    </CodexButton>
                  </View>
                  <View className="flex-1">
                    <CodexButton
                      variant="secondary"
                      disabled={cancelling}
                      onPress={() => setConfirmingCancel(false)}
                    >
                      Keep it
                    </CodexButton>
                  </View>
                </View>
              </CodexCard>
            )}
          </View>
        )}

        {canDelete && (
          <View className="mb-8">
            {!confirmingDelete ? (
              <CodexButton
                testID="delete-goal-button"
                variant="secondary"
                onPress={() => {
                  setDeleteError(null);
                  setConfirmingDelete(true);
                }}
              >
                Delete this draft
              </CodexButton>
            ) : (
              <CodexCard className="border-codex-accent p-4">
                <Text className="font-sans-bold text-sm text-codex-text">Delete this draft?</Text>
                <Text className="mt-1 font-sans text-sm leading-relaxed text-codex-text-secondary">
                  The draft is removed for good. Nothing is charged, and it won&apos;t appear in
                  your history.
                </Text>
                {deleteError && (
                  <Text testID="delete-goal-error" className="mt-2 font-sans text-sm text-codex-accent">
                    {deleteError}
                  </Text>
                )}
                <View className="mt-3 flex-row gap-2">
                  <View className="flex-1">
                    <CodexButton
                      testID="delete-goal-confirm"
                      variant="primary"
                      loading={deleting}
                      onPress={handleDelete}
                    >
                      Yes, delete it
                    </CodexButton>
                  </View>
                  <View className="flex-1">
                    <CodexButton
                      variant="secondary"
                      disabled={deleting}
                      onPress={() => setConfirmingDelete(false)}
                    >
                      Keep it
                    </CodexButton>
                  </View>
                </View>
              </CodexCard>
            )}
          </View>
        )}
      </ScrollView>
    </DetailChrome>
  );
}
