describe('auth storage adapter integration', () => {
  let tokenStorageMock: {
    getTokenSync: jest.Mock;
    persistToken: jest.Mock;
    removeToken: jest.Mock;
    restoreToken: jest.Mock;
  };

  beforeEach(() => {
    jest.resetModules();

    tokenStorageMock = {
      getTokenSync: jest.fn().mockReturnValue(null),
      persistToken: jest.fn().mockResolvedValue(undefined),
      removeToken: jest.fn().mockResolvedValue(undefined),
      restoreToken: jest.fn().mockResolvedValue('restored-native-token'),
    };

    jest.doMock('../../services/tokenStorage', () => tokenStorageMock);
    jest.doMock('react-native', () => {
      const RN = jest.requireActual('react-native');
      RN.Platform.OS = 'ios';
      return RN;
    });
  });

  it('routes token persistence through the shared token storage adapter', async () => {
    const { auth } = require('../../services/auth') as typeof import('../../services/auth');

    auth.setToken('jwt-1');
    expect(tokenStorageMock.persistToken).toHaveBeenCalledWith('jwt-1');

    auth.removeToken();
    expect(tokenStorageMock.removeToken).toHaveBeenCalledTimes(1);

    await auth.restoreToken();
    expect(tokenStorageMock.restoreToken).toHaveBeenCalledTimes(1);
    expect(auth.getToken()).toBe('restored-native-token');
  });

  it('notifies registered listeners when a session expires', () => {
    const { auth } = require('../../services/auth') as typeof import('../../services/auth');

    const listener = jest.fn();
    const unsubscribe = auth.onSessionExpired(listener);

    auth.notifySessionExpired();

    expect(listener).toHaveBeenCalledTimes(1);
    expect(tokenStorageMock.removeToken).toHaveBeenCalledTimes(1);

    unsubscribe();
    auth.notifySessionExpired();
    expect(listener).toHaveBeenCalledTimes(1);
  });
});