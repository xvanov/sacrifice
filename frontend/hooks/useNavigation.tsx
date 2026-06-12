import React, { createContext, useCallback, useContext, useState } from 'react';

export type Screen =
  | { name: 'home' }
  | { name: 'dashboard' }
  | { name: 'chat-goal-create' }
  | { name: 'goal-detail'; goalId: string }
  | { name: 'proof-submission'; goalId: string }
  | { name: 'api-endpoint-proof-submission'; goalId: string }
  | { name: 'dev-sandbox-proof-submission'; goalId: string }
  | { name: 'notifications' }
  | { name: 'login' };

interface NavigationState {
  currentScreen: Screen;
  navigate: (screen: Screen) => void;
  goBack: () => void;
}

const NavigationContext = createContext<NavigationState | null>(null);

export function NavigationProvider({ children }: { children: React.ReactNode }) {
  const [currentScreen, setCurrentScreen] = useState<Screen>({ name: 'home' });
  const [, setHistoryStack] = useState<Screen[]>([]);

  const navigate = useCallback((screen: Screen) => {
    setHistoryStack((prev) => [...prev, currentScreen]);
    setCurrentScreen(screen);
  }, [currentScreen]);

  const goBack = useCallback(() => {
    setHistoryStack((prev) => {
      if (prev.length === 0) return prev;
      setCurrentScreen(prev[prev.length - 1]);
      return prev.slice(0, -1);
    });
  }, []);

  return (
    <NavigationContext.Provider value={{ currentScreen, navigate, goBack }}>
      {children}
    </NavigationContext.Provider>
  );
}

export function useNavigation(): NavigationState {
  const ctx = useContext(NavigationContext);
  if (!ctx) {
    throw new Error('useNavigation must be used within a NavigationProvider');
  }
  return ctx;
}
