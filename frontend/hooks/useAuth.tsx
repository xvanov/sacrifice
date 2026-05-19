import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { auth } from '../services/auth';
import type { User } from '../types';

interface AuthState {
  user: User | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  loginWithGoogle: () => void;
  loginWithGithub: () => void;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const processCallback = useCallback(async () => {
    const result = auth.handleRedirectCallback();
    if (!result) return;

    setIsLoading(true);
    try {
      let accessToken: string;
      if (result.token) {
        const res = await auth.googleLogin(result.token);
        accessToken = res.access_token;
      } else if (result.code) {
        const res = await auth.githubLogin(result.code);
        accessToken = res.access_token;
      } else {
        return;
      }
      auth.setToken(accessToken);
      const userData = await auth.fetchUser(accessToken);
      setUser(userData);
    } catch (err) {
      console.error('Auth callback error:', err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    processCallback();
  }, [processCallback]);

  const restoreSession = useCallback(async () => {
    const token = auth.getToken();
    if (!token) {
      setIsLoading(false);
      return;
    }
    try {
      const userData = await auth.fetchUser(token);
      setUser(userData);
    } catch {
      auth.removeToken();
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!auth.handleRedirectCallback()) {
      restoreSession();
    }
  }, [restoreSession]);

  const loginWithGoogle = useCallback(() => {
    const redirectUri = window.location.origin + window.location.pathname;
    window.location.href = auth.getGoogleOAuthUrl(redirectUri);
  }, []);

  const loginWithGithub = useCallback(() => {
    const redirectUri = window.location.origin + window.location.pathname;
    window.location.href = auth.getGithubOAuthUrl(redirectUri);
  }, []);

  const logout = useCallback(() => {
    auth.removeToken();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        isLoading,
        isAuthenticated: !!user,
        loginWithGoogle,
        loginWithGithub,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return ctx;
}
