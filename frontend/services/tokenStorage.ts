import { Platform } from 'react-native';

const TOKEN_KEY = 'sacrifice_auth_token';

function getWebStorage(): Storage | null {
  if (Platform.OS !== 'web') return null;
  if (typeof localStorage === 'undefined') return null;
  try {
    return localStorage;
  } catch {
    return null;
  }
}

export function getTokenSync(): string | null {
  const storage = getWebStorage();
  if (!storage) return null;
  try {
    return storage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export async function persistToken(token: string): Promise<void> {
  const storage = getWebStorage();
  if (storage) {
    try {
      storage.setItem(TOKEN_KEY, token);
    } catch {
      // Ignore web storage write failures.
    }
    return;
  }

  try {
    const SecureStore = require('expo-secure-store');
    await SecureStore.setItemAsync(TOKEN_KEY, token);
  } catch {
    // Ignore SecureStore availability/write failures.
  }
}

export async function removeToken(): Promise<void> {
  const storage = getWebStorage();
  if (storage) {
    try {
      storage.removeItem(TOKEN_KEY);
    } catch {
      // Ignore web storage remove failures.
    }
    return;
  }

  try {
    const SecureStore = require('expo-secure-store');
    await SecureStore.deleteItemAsync(TOKEN_KEY);
  } catch {
    // Ignore SecureStore availability/remove failures.
  }
}

export async function restoreToken(): Promise<string | null> {
  const storage = getWebStorage();
  if (storage) {
    return getTokenSync();
  }

  try {
    const SecureStore = require('expo-secure-store');
    const token = await SecureStore.getItemAsync(TOKEN_KEY);
    return token || null;
  } catch {
    return null;
  }
}
