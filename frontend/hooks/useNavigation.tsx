import React, { createContext, useCallback, useContext, useState } from 'react';

export type Screen =
  | { name: 'home' }
  | { name: 'dashboard' }
  | { name: 'goal-create' }
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

const historyStack: Screen[] = [];

export function NavigationProvider({ children }: { children: React.ReactNode }) {
  const [currentScreen, setCurrentScreen] = useState<Screen>({ name: 'home' });

  const navigate = useCallback((screen: Screen) => {
    historyStack.push(currentScreen);
    setCurrentScreen(screen);
  }, [currentScreen]);

  const goBack = useCallback(() => {
    const prev = historyStack.pop();
    if (prev) setCurrentScreen(prev);
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
