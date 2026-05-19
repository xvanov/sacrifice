let store: Record<string, string> = {};

export function getItemAsync(key: string): Promise<string | null> {
  return Promise.resolve(store[key] ?? null);
}

export function setItemAsync(key: string, value: string): Promise<void> {
  store[key] = value;
  return Promise.resolve();
}

export function deleteItemAsync(key: string): Promise<void> {
  delete store[key];
  return Promise.resolve();
}

export function isAvailableAsync(): Promise<boolean> {
  return Promise.resolve(true);
}

export function resetStore() {
  store = {};
}
