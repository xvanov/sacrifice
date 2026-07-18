describe('tokenStorage', () => {
  afterEach(() => {
    jest.resetModules();
    jest.clearAllMocks();
    delete (global as any).localStorage;
  });

  it('uses localStorage on web and falls back cleanly if storage operations fail', async () => {
    const webStore = {
      getItem: jest.fn(() => {
        throw new Error('storage blocked');
      }),
      setItem: jest.fn(() => {
        throw new Error('storage blocked');
      }),
      removeItem: jest.fn(() => {
        throw new Error('storage blocked');
      }),
    };
    Object.defineProperty(global, 'localStorage', {
      value: webStore,
      configurable: true,
      writable: true,
    });

    jest.doMock('react-native', () => {
      const RN = jest.requireActual('react-native');
      RN.Platform.OS = 'web';
      return RN;
    });

    const storage = require('../../services/tokenStorage') as typeof import('../../services/tokenStorage');

    expect(storage.getTokenSync()).toBeNull();
    await expect(storage.persistToken('web-token')).resolves.toBeUndefined();
    await expect(storage.removeToken()).resolves.toBeUndefined();
  });

  it('uses SecureStore on native for persist/restore/remove', async () => {
    const secureStore = {
      setItemAsync: jest.fn().mockResolvedValue(undefined),
      getItemAsync: jest.fn().mockResolvedValue('native-token'),
      deleteItemAsync: jest.fn().mockResolvedValue(undefined),
    };

    jest.doMock('react-native', () => {
      const RN = jest.requireActual('react-native');
      RN.Platform.OS = 'ios';
      return RN;
    });
    jest.doMock('expo-secure-store', () => secureStore);

    const storage = require('../../services/tokenStorage') as typeof import('../../services/tokenStorage');

    await storage.persistToken('native-token');
    expect(secureStore.setItemAsync).toHaveBeenCalledWith('sacrifice_auth_token', 'native-token');

    await expect(storage.restoreToken()).resolves.toBe('native-token');
    expect(secureStore.getItemAsync).toHaveBeenCalledWith('sacrifice_auth_token');

    await storage.removeToken();
    expect(secureStore.deleteItemAsync).toHaveBeenCalledWith('sacrifice_auth_token');
  });
});