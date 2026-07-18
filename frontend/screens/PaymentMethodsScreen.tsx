import { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Linking, Platform, Pressable, ScrollView, Text, View } from 'react-native';
import { CodexHeader } from '../components/CodexHeader';
import { CodexCard } from '../components/CodexCard';
import { CodexButton } from '../components/CodexButton';
import { CodexInput } from '../components/CodexInput';
import { api } from '../services/api';
import { useNavigation } from '../hooks/useNavigation';
import type { Charity } from '../types';

interface PaymentMethod {
  id: string;
  card: { last4: string; brand: string; exp_month: number; exp_year: number };
  billing_name: string;
}

// Stripe.js must come from js.stripe.com (PCI requirement — it cannot be
// bundled). Loaded once, on the web platform only.
let stripeJsPromise: Promise<any> | null = null;
function loadStripeJs(): Promise<any> {
  // Guard against native runtime — this module is imported in shared code paths.
  if (Platform.OS !== 'web' || typeof document === 'undefined') {
    return Promise.reject(new Error('Stripe.js is only available on web'));
  }
  if (stripeJsPromise) return stripeJsPromise;
  stripeJsPromise = new Promise((resolve, reject) => {
    const existing = (window as any).Stripe;
    if (existing) return resolve(existing);
    const script = document.createElement('script');
    script.src = 'https://js.stripe.com/v3/';
    script.onload = () => {
      const Stripe = (window as any).Stripe;
      if (Stripe) resolve(Stripe);
      else reject(new Error('Stripe.js failed to initialize'));
    };
    script.onerror = () => reject(new Error('Failed to load Stripe.js'));
    document.head.appendChild(script);
  });
  return stripeJsPromise;
}

// Shared styling so the three Stripe iframes read like Codex inputs.
const STRIPE_ELEMENT_STYLE = {
  base: {
    fontSize: '16px',
    color: '#0D0B08',
    fontFamily: 'Inter, system-ui, sans-serif',
    '::placeholder': { color: '#85796A' },
  },
  invalid: { color: '#8A2A1C' },
};

function SectionTitle({ children }: { children: string }) {
  return (
    <Text className="mb-1 mt-2 font-serif text-xl text-codex-text">{children}</Text>
  );
}

function openLink(url: string) {
  if (Platform.OS === 'web') {
    window.open(url, '_blank', 'noopener,noreferrer');
  } else {
    void Linking.openURL(url);
  }
}

export default function PaymentMethodsScreen() {
  const { goBack } = useNavigation();
  const [methods, setMethods] = useState<PaymentMethod[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const stripeRef = useRef<any>(null);
  const elementsRef = useRef<any>(null);
  const cardNumberRef = useRef<any>(null);

  // Charities / recipients.
  const [charities, setCharities] = useState<Charity[]>([]);
  const [charitiesLoading, setCharitiesLoading] = useState(true);
  const [charitiesError, setCharitiesError] = useState<string | null>(null);
  const [addingCharity, setAddingCharity] = useState(false);
  const [charityName, setCharityName] = useState('');
  const [charityEmail, setCharityEmail] = useState('');
  const [creatingCharity, setCreatingCharity] = useState(false);
  const [charityFormError, setCharityFormError] = useState<string | null>(null);
  const [connectDisabled, setConnectDisabled] = useState(false);
  const [newRecipient, setNewRecipient] = useState<{ name: string; onboarding_url: string } | null>(null);

  const fetchMethods = useCallback(async () => {
    setLoading(true);
    setError(null);
    const res = await api.getPaymentMethods();
    if (res.data) setMethods(res.data);
    else setError(res.error || 'Failed to load payment methods');
    setLoading(false);
  }, []);

  const fetchCharities = useCallback(async () => {
    setCharitiesLoading(true);
    setCharitiesError(null);
    const res = await api.searchCharities('');
    if (res.data) setCharities(res.data);
    else setCharitiesError(res.error || 'Failed to load recipients');
    setCharitiesLoading(false);
  }, []);

  useEffect(() => {
    fetchMethods();
    fetchCharities();
  }, [fetchMethods, fetchCharities]);

  const startAddCard = useCallback(async () => {
    setNotice(null);
    setError(null);
    try {
      const config = await api.getPaymentConfig();
      if (!config.data) throw new Error(config.error || 'Stripe is not configured');
      const Stripe = await loadStripeJs();
      stripeRef.current = Stripe(config.data.publishable_key);
      setAdding(true);
    } catch (e: any) {
      setError(e?.message || 'Could not start card entry');
    }
  }, []);

  // Mount the three split Stripe elements only after React has rendered their
  // container Views (nativeID → DOM id). Mounting from the click handler races
  // the render and Stripe throws "selector applies to no DOM elements". All
  // three share one elements() instance.
  useEffect(() => {
    if (!adding || !stripeRef.current || cardNumberRef.current) return;
    const elements = stripeRef.current.elements();
    elementsRef.current = elements;
    const cardNumber = elements.create('cardNumber', { style: STRIPE_ELEMENT_STYLE, showIcon: true });
    const cardExpiry = elements.create('cardExpiry', { style: STRIPE_ELEMENT_STYLE });
    const cardCvc = elements.create('cardCvc', { style: STRIPE_ELEMENT_STYLE });
    cardNumber.mount('#sacrifice-card-number');
    cardExpiry.mount('#sacrifice-card-expiry');
    cardCvc.mount('#sacrifice-card-cvc');
    cardNumberRef.current = cardNumber;
  }, [adding]);

  const teardownCard = useCallback(() => {
    if (elementsRef.current) {
      try {
        elementsRef.current.getElement('cardNumber')?.unmount();
        elementsRef.current.getElement('cardExpiry')?.unmount();
        elementsRef.current.getElement('cardCvc')?.unmount();
      } catch {
        // Elements may already be gone; ignore.
      }
    }
    elementsRef.current = null;
    cardNumberRef.current = null;
  }, []);

  const saveCard = useCallback(async () => {
    if (!stripeRef.current || !cardNumberRef.current) return;
    setSaving(true);
    setError(null);
    try {
      const intent = await api.createSetupIntent();
      if (!intent.data) throw new Error(intent.error || 'Failed to create setup intent');
      const result = await stripeRef.current.confirmCardSetup(intent.data.client_secret, {
        payment_method: { card: cardNumberRef.current },
      });
      if (result.error) throw new Error(result.error.message);
      teardownCard();
      setAdding(false);
      setNotice('Card saved. It will only be charged if a goal fails.');
      await fetchMethods();
    } catch (e: any) {
      setError(e?.message || 'Failed to save card');
    } finally {
      setSaving(false);
    }
  }, [fetchMethods, teardownCard]);

  const cancelAdd = useCallback(() => {
    teardownCard();
    setAdding(false);
  }, [teardownCard]);

  const removeMethod = useCallback(
    async (id: string) => {
      setError(null);
      const res = await api.deletePaymentMethod(id);
      if (res.error) setError(res.error);
      await fetchMethods();
    },
    [fetchMethods],
  );

  const createCharity = useCallback(async () => {
    setCharityFormError(null);
    setConnectDisabled(false);
    if (!charityName.trim() || !charityEmail.trim()) {
      setCharityFormError('A name and email are both required.');
      return;
    }
    setCreatingCharity(true);
    const res = await api.createCharity({ name: charityName.trim(), email: charityEmail.trim() });
    setCreatingCharity(false);
    if (res.data) {
      setNewRecipient({ name: res.data.name, onboarding_url: res.data.onboarding_url });
      setCharityName('');
      setCharityEmail('');
      setAddingCharity(false);
      await fetchCharities();
      return;
    }
    // 502 → Connect not enabled on the platform account.
    if (res.status === 502) {
      setConnectDisabled(true);
      setCharityFormError(
        res.error?.replace(/^HTTP 502:\s*/, '') || 'Stripe Connect is not enabled on this platform.',
      );
      return;
    }
    setCharityFormError(res.error?.replace(/^HTTP \d+:\s*/, '') || 'Could not create recipient.');
  }, [charityName, charityEmail, fetchCharities]);

  if (Platform.OS !== 'web') {
    return (
      <View className="flex-1 bg-codex-bg">
        <CodexHeader />
        <View className="flex-1 items-center justify-center px-6">
          <Text className="text-center font-sans text-sm text-codex-muted">
            Card entry is available in the web app.
          </Text>
        </View>
      </View>
    );
  }

  return (
    <View className="flex-1 bg-codex-bg">
      <CodexHeader />
      <View className="flex-row items-center px-4 pb-2 pt-1">
        <Pressable
          testID="payment-methods-back"
          onPress={goBack}
          className="mr-3 rounded-full border border-codex-border bg-codex-surface px-3 py-1.5"
        >
          <Text className="font-sans-medium text-xs tracking-wide text-codex-muted">Back</Text>
        </Pressable>
        <Text className="font-serif-italic text-lg text-codex-text">Payments</Text>
      </View>

      <ScrollView className="flex-1 px-4" contentContainerStyle={{ paddingBottom: 40 }}>
        {notice && (
          <CodexCard className="mb-3 border-codex-success bg-codex-success-light p-3">
            <Text className="font-sans text-sm text-codex-success">{notice}</Text>
          </CodexCard>
        )}
        {error && (
          <CodexCard className="mb-3 border-codex-accent bg-codex-danger-light p-3">
            <Text testID="payment-methods-error" className="font-sans text-sm text-codex-accent">
              {error}
            </Text>
          </CodexCard>
        )}

        {/* ---- Payment method ---- */}
        <SectionTitle>Your card</SectionTitle>
        <Text className="mb-3 font-sans text-sm leading-relaxed text-codex-muted">
          We only charge this card when a goal fails. The pledge is then donated to that goal's
          recipient.
        </Text>

        {loading ? (
          <View className="mb-4">
            <View className="mb-2 h-14 rounded-sm bg-codex-surface" />
          </View>
        ) : methods.length === 0 && !adding ? (
          <CodexCard className="mb-3 items-center p-5" testID="payment-methods-empty">
            <Text className="font-serif text-lg text-codex-text">No card on file</Text>
            <Text className="mt-1 text-center font-sans text-sm text-codex-muted">
              Add one so your pledges can be honored.
            </Text>
          </CodexCard>
        ) : (
          methods.map((m) => (
            <CodexCard key={m.id} className="mb-2 flex-row items-center justify-between p-4">
              <View>
                <Text className="font-sans-medium text-sm text-codex-text">
                  {m.card.brand.toUpperCase()} •••• {m.card.last4}
                </Text>
                <Text className="mt-0.5 font-sans text-xs text-codex-muted">
                  Expires {String(m.card.exp_month).padStart(2, '0')}/{m.card.exp_year}
                </Text>
              </View>
              <Pressable
                testID={`remove-method-${m.id}`}
                onPress={() => removeMethod(m.id)}
                className="rounded-full border border-codex-border px-3 py-1.5"
              >
                <Text className="font-sans-medium text-xs tracking-wide text-codex-accent">Remove</Text>
              </Pressable>
            </CodexCard>
          ))
        )}

        {adding ? (
          <CodexCard className="mb-4 p-4">
            <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">
              Card number
            </Text>
            {/* nativeID becomes the DOM id on web; Stripe Elements mounts here. */}
            <View
              nativeID="sacrifice-card-number"
              className="mb-3 justify-center rounded-sm border border-codex-border bg-codex-surface px-4 py-3"
              style={{ minHeight: 46 }}
            />
            <View className="flex-row gap-3">
              <View className="flex-1">
                <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">
                  Expiry
                </Text>
                <View
                  nativeID="sacrifice-card-expiry"
                  className="justify-center rounded-sm border border-codex-border bg-codex-surface px-4 py-3"
                  style={{ minHeight: 46 }}
                />
              </View>
              <View className="flex-1">
                <Text className="mb-1.5 font-sans-medium text-xs uppercase tracking-wider text-codex-muted">
                  CVC
                </Text>
                <View
                  nativeID="sacrifice-card-cvc"
                  className="justify-center rounded-sm border border-codex-border bg-codex-surface px-4 py-3"
                  style={{ minHeight: 46 }}
                />
              </View>
            </View>
            <View className="mt-4 flex-row gap-2">
              <View className="flex-1">
                <CodexButton onPress={saveCard} loading={saving}>
                  Save card
                </CodexButton>
              </View>
              <View className="flex-1">
                <CodexButton variant="secondary" onPress={cancelAdd} disabled={saving}>
                  Cancel
                </CodexButton>
              </View>
            </View>
          </CodexCard>
        ) : (
          <View className="mb-6">
            <CodexButton
              testID="add-card-button"
              variant={methods.length === 0 ? 'primary' : 'secondary'}
              onPress={startAddCard}
            >
              {methods.length === 0 ? 'Add card' : 'Add another card'}
            </CodexButton>
          </View>
        )}

        <View className="my-2 h-px bg-codex-border" />

        {/* ---- Recipients / charities ---- */}
        <SectionTitle>Where pledges go</SectionTitle>
        <Text className="mb-3 font-sans text-sm leading-relaxed text-codex-muted">
          Recipients are optional. A failed goal always charges your card — if the goal has a
          recipient, the money is forwarded to them; otherwise it stays with the platform.
          Pick any public nonprofit (via Every.org — no setup needed) when choosing a
          recipient on a goal, or add a personal recipient below (they must finish Stripe
          onboarding before money can reach them).
        </Text>

        {newRecipient && (
          <CodexCard className="mb-3 border-codex-success bg-codex-success-light p-4" testID="recipient-created">
            <Text className="font-sans-bold text-sm text-codex-text">
              {newRecipient.name} added
            </Text>
            <Text className="mt-1 font-sans text-sm leading-relaxed text-codex-text-secondary">
              This recipient can't receive money until they complete Stripe onboarding. Send them
              this link:
            </Text>
            <Pressable
              className="mt-2 rounded-sm bg-codex-accent px-4 py-2.5"
              onPress={() => openLink(newRecipient.onboarding_url)}
            >
              <Text className="text-center font-sans-medium text-sm text-codex-surface">
                Open onboarding link
              </Text>
            </Pressable>
            <Text selectable className="mt-2 font-mono text-[11px] text-codex-muted" numberOfLines={2}>
              {newRecipient.onboarding_url}
            </Text>
          </CodexCard>
        )}

        {connectDisabled && (
          <CodexCard className="mb-3 border-codex-warn bg-codex-warn-light p-4" testID="connect-disabled-notice">
            <Text className="font-sans-bold text-sm text-codex-text">Stripe Connect isn't enabled</Text>
            <Text className="mt-1 font-sans text-sm leading-relaxed text-codex-text-secondary">
              Recipients use Stripe Connect, which isn't turned on for this platform account yet.
              Enable it in the Stripe dashboard, then try again.
            </Text>
            <Pressable
              className="mt-2"
              onPress={() => openLink('https://dashboard.stripe.com/connect')}
            >
              <Text className="font-sans-medium text-sm text-codex-accent">
                Open Stripe Connect settings →
              </Text>
            </Pressable>
          </CodexCard>
        )}

        {charitiesLoading ? (
          <View className="mb-2 h-14 rounded-sm bg-codex-surface" />
        ) : charitiesError && !connectDisabled ? (
          <CodexCard className="mb-3 p-4">
            <Text className="font-sans text-sm text-codex-muted">
              {charitiesError.replace(/^HTTP \d+:\s*/, '')}
            </Text>
          </CodexCard>
        ) : charities.length === 0 ? (
          <CodexCard className="mb-3 items-center p-5" testID="charities-empty">
            <Text className="font-serif text-lg text-codex-text">No recipients yet</Text>
            <Text className="mt-1 text-center font-sans text-sm text-codex-muted">
              Add one below to give your pledges a destination.
            </Text>
          </CodexCard>
        ) : (
          charities.map((c) => (
            <CodexCard key={c.id} className="mb-2 p-4" testID={`charity-${c.id}`}>
              <Text className="font-sans-medium text-sm text-codex-text">
                {c.name || 'Unnamed recipient'}
              </Text>
              <Text className="mt-0.5 font-mono text-[11px] text-codex-muted">{c.id}</Text>
            </CodexCard>
          ))
        )}

        {addingCharity ? (
          <CodexCard className="mb-4 p-4">
            <CodexInput
              testID="charity-name-input"
              label="Recipient name"
              value={charityName}
              onChangeText={setCharityName}
              placeholder="e.g. Doctors Without Borders"
              autoCapitalize="words"
            />
            <CodexInput
              testID="charity-email-input"
              label="Recipient email"
              value={charityEmail}
              onChangeText={setCharityEmail}
              placeholder="finance@example.org"
            />
            {charityFormError && (
              <Text testID="charity-form-error" className="mb-2 font-sans text-sm text-codex-accent">
                {charityFormError}
              </Text>
            )}
            <View className="flex-row gap-2">
              <View className="flex-1">
                <CodexButton testID="charity-create-submit" onPress={createCharity} loading={creatingCharity}>
                  Create recipient
                </CodexButton>
              </View>
              <View className="flex-1">
                <CodexButton
                  variant="secondary"
                  disabled={creatingCharity}
                  onPress={() => {
                    setAddingCharity(false);
                    setCharityFormError(null);
                  }}
                >
                  Cancel
                </CodexButton>
              </View>
            </View>
          </CodexCard>
        ) : (
          <View className="mb-4">
            <CodexButton
              testID="add-charity-button"
              variant="secondary"
              onPress={() => {
                setNewRecipient(null);
                setAddingCharity(true);
              }}
            >
              Add a recipient
            </CodexButton>
          </View>
        )}
      </ScrollView>
    </View>
  );
}
