import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { Platform } from 'react-native';
import { auth, type EmailAuthResult, type EmailAuthProvider } from '../services/auth';
import type { User } from '../types';

export interface OAuthRedirectError {
  error: string;
  provider?: EmailAuthProvider;
}

interface AuthState {
  user: User | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  loginWithGoogle: () => void;
  loginWithGithub: () => void;
  loginWithEmail: (email: string, password: string) => Promise<EmailAuthResult>;
  registerWithEmail: (
    email: string,
    password: string,
    displayName?: string,
  ) => Promise<EmailAuthResult>;
  redirectError: OAuthRedirectError | null;
  clearRedirectError: () => void;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [redirectError, setRedirectError] = useState<OAuthRedirectError | null>(null);

  const processCallback = useCallback(async () => {
    const result = auth.handleRedirectCallback();
    if (!result) return;

    if (result.error) {
      setRedirectError({ error: result.error, provider: result.provider });
      setIsLoading(false);
      return;
    }

    setIsLoading(true);
    try {
      let accessToken: string;
      if (result.accessToken) {
        accessToken = result.accessToken;
      } else if (result.token) {
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
    await auth.restoreToken();
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
    if (Platform.OS === 'web') {
      window.location.href = `${auth.getApiBase()}/api/auth/google/login`;
    } else {
      auth.nativeOAuthLogin('google').then((res) => {
        if (res) {
          auth.setToken(res.access_token);
          setUser(res.user);
        }
      }).catch((err) => {
        console.error('Google login error:', err);
      });
    }
  }, []);

  const loginWithGithub = useCallback(() => {
    if (Platform.OS === 'web') {
      window.location.href = `${auth.getApiBase()}/api/auth/github/login`;
    } else {
      auth.nativeOAuthLogin('github').then((res) => {
        if (res) {
          auth.setToken(res.access_token);
          setUser(res.user);
        }
      }).catch((err) => {
        console.error('GitHub login error:', err);
      });
    }
  }, []);

  const finalizeEmailAuth = useCallback(async (result: EmailAuthResult): Promise<EmailAuthResult> => {
    if (result.ok) {
      auth.setToken(result.access_token);
      setUser(result.user);
    }
    return result;
  }, []);

  const loginWithEmail = useCallback(
    async (email: string, password: string) => {
      const res = await auth.emailLogin(email, password);
      return finalizeEmailAuth(res);
    },
    [finalizeEmailAuth],
  );

  const registerWithEmail = useCallback(
    async (email: string, password: string, displayName?: string) => {
      const res = await auth.emailRegister(email, password, displayName);
      return finalizeEmailAuth(res);
    },
    [finalizeEmailAuth],
  );

  const clearRedirectError = useCallback(() => setRedirectError(null), []);

  const logout = useCallback(() => {
    auth.removeToken();
    setUser(null);
  }, []);

  // The API layer fires this when a request 401s (token expired): drop the
  // in-memory user so the app returns to the login screen instead of hanging
  // on screens whose every request fails.
  useEffect(() => {
    if (Platform.OS !== 'web' || typeof window === 'undefined') return;
    const onExpired = () => setUser(null);
    window.addEventListener('sacrifice-session-expired', onExpired);
    return () => window.removeEventListener('sacrifice-session-expired', onExpired);
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        isLoading,
        isAuthenticated: !!user,
        loginWithGoogle,
        loginWithGithub,
        loginWithEmail,
        registerWithEmail,
        redirectError,
        clearRedirectError,
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
