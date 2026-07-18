import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../services/api';
import type { GoalTypeInfo } from '../services/api';

export interface GoalTypeLabels {
  full: Record<string, string>;
  short: Record<string, string>;
}

function buildLabels(goalTypes: GoalTypeInfo[] | undefined): GoalTypeLabels {
  const full: Record<string, string> = {};
  const short: Record<string, string> = {};
  for (const gt of goalTypes ?? []) {
    full[gt.name] = gt.description
      ? gt.description.split('.')[0]
      : humanize(gt.name);
    short[gt.name] = humanize(gt.name);
  }
  return { full, short };
}

function humanize(raw: string): string {
  return raw.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export function useGoalTypeLabels() {
  const [labels, setLabels] = useState<GoalTypeLabels | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const isMounted = useRef(true);

  useEffect(() => {
    return () => {
      isMounted.current = false;
    };
  }, []);

  const fetchLabels = useCallback(async () => {
    setLoading(true);
    setError(null);
    const result = await api.listGoalTypes();
    if (!isMounted.current) return;
    if (result.data) {
      setLabels(buildLabels(result.data.goal_types));
    } else {
      setError(result.error || 'Failed to load goal types');
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void fetchLabels();
  }, [fetchLabels]);

  /** Look up the full display label for a goal type. Falls back to humanization. */
  const typeLabel = useCallback(
    (t: string): string => labels?.full[t] ?? humanize(t),
    [labels],
  );

  /** Look up the short display label for a goal type. Falls back to humanization. */
  const typeLabelShort = useCallback(
    (t: string): string => labels?.short[t] ?? humanize(t),
    [labels],
  );

  return { labels, loading, error, typeLabel, typeLabelShort, refetch: fetchLabels };
}