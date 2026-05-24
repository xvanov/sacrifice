import React from 'react';
import { render, fireEvent, act, waitFor } from '@testing-library/react-native';
import LoginScreen from '../../screens/LoginScreen';

const mockLoginWithGoogle = jest.fn();
const mockLoginWithGithub = jest.fn();
const mockLoginWithEmail = jest.fn();
const mockRegisterWithEmail = jest.fn();
const mockClearRedirectError = jest.fn();

let mockRedirectError: { error: string; provider?: string } | null = null;

jest.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({
    user: null,
    isLoading: false,
    isAuthenticated: false,
    loginWithGoogle: mockLoginWithGoogle,
    loginWithGithub: mockLoginWithGithub,
    loginWithEmail: mockLoginWithEmail,
    registerWithEmail: mockRegisterWithEmail,
    redirectError: mockRedirectError,
    clearRedirectError: mockClearRedirectError,
    logout: jest.fn(),
  }),
}));

beforeEach(() => {
  mockLoginWithGoogle.mockReset();
  mockLoginWithGithub.mockReset();
  mockLoginWithEmail.mockReset();
  mockRegisterWithEmail.mockReset();
  mockClearRedirectError.mockReset();
  mockRedirectError = null;
});

describe('LoginScreen', () => {
  it('renders the app title and tagline', () => {
    const { getByText } = render(<LoginScreen />);
    expect(getByText('Sacrifice')).toBeTruthy();
    expect(getByText('Commit to your goals. Put money on the line.')).toBeTruthy();
  });

  it('renders email/password inputs and the OAuth buttons', () => {
    const { getByTestId, getByText } = render(<LoginScreen />);
    expect(getByTestId('email-input')).toBeTruthy();
    expect(getByTestId('password-input')).toBeTruthy();
    expect(getByText('Sign in with Google')).toBeTruthy();
    expect(getByText('Sign in with GitHub')).toBeTruthy();
  });

  it('starts in login mode and toggles to register mode', () => {
    const { getByText, getByTestId, queryByTestId } = render(<LoginScreen />);
    expect(getByText('Continue with email')).toBeTruthy();
    expect(queryByTestId('display-name-input')).toBeNull();

    fireEvent.press(getByTestId('mode-toggle'));

    expect(getByText('Create account')).toBeTruthy();
    expect(getByTestId('display-name-input')).toBeTruthy();
  });

  it('calls loginWithGoogle when Google button is pressed', () => {
    const { getByText } = render(<LoginScreen />);
    fireEvent.press(getByText('Sign in with Google'));
    expect(mockLoginWithGoogle).toHaveBeenCalledTimes(1);
  });

  it('calls loginWithGithub when GitHub button is pressed', () => {
    const { getByText } = render(<LoginScreen />);
    fireEvent.press(getByText('Sign in with GitHub'));
    expect(mockLoginWithGithub).toHaveBeenCalledTimes(1);
  });

  it('calls loginWithEmail with input values on submit', async () => {
    mockLoginWithEmail.mockResolvedValue({ ok: true, access_token: 't', user: { id: '1' } });
    const { getByTestId } = render(<LoginScreen />);
    fireEvent.changeText(getByTestId('email-input'), 'a@b.com');
    fireEvent.changeText(getByTestId('password-input'), 'longpassword');
    await act(async () => {
      fireEvent.press(getByTestId('email-submit'));
    });
    expect(mockLoginWithEmail).toHaveBeenCalledWith('a@b.com', 'longpassword');
  });

  it('calls registerWithEmail in register mode', async () => {
    mockRegisterWithEmail.mockResolvedValue({ ok: true, access_token: 't', user: { id: '1' } });
    const { getByTestId } = render(<LoginScreen />);
    fireEvent.press(getByTestId('mode-toggle')); // switch to register
    fireEvent.changeText(getByTestId('email-input'), 'new@b.com');
    fireEvent.changeText(getByTestId('password-input'), 'longpassword');
    fireEvent.changeText(getByTestId('display-name-input'), 'New User');
    await act(async () => {
      fireEvent.press(getByTestId('email-submit'));
    });
    expect(mockRegisterWithEmail).toHaveBeenCalledWith('new@b.com', 'longpassword', 'New User');
  });

  it('shows a conflict banner naming the provider on 409', async () => {
    mockLoginWithEmail.mockResolvedValue({
      ok: false,
      status: 409,
      error: 'account_exists',
      provider: 'google',
    });
    const { getByTestId, queryByText } = render(<LoginScreen />);
    fireEvent.changeText(getByTestId('email-input'), 'a@b.com');
    fireEvent.changeText(getByTestId('password-input'), 'longpassword');
    await act(async () => {
      fireEvent.press(getByTestId('email-submit'));
    });
    await waitFor(() => {
      expect(getByTestId('conflict-banner')).toBeTruthy();
    });
    expect(
      queryByText(
        'This email is registered with Google. Use the Google button below to sign in.',
      ),
    ).toBeTruthy();
  });

  it('shows an "Invalid email or password" error on 401', async () => {
    mockLoginWithEmail.mockResolvedValue({
      ok: false,
      status: 401,
      error: 'invalid_credentials',
    });
    const { getByTestId, queryByText } = render(<LoginScreen />);
    fireEvent.changeText(getByTestId('email-input'), 'a@b.com');
    fireEvent.changeText(getByTestId('password-input'), 'wrongpw');
    await act(async () => {
      fireEvent.press(getByTestId('email-submit'));
    });
    await waitFor(() => {
      expect(getByTestId('error-banner')).toBeTruthy();
    });
    expect(queryByText('Invalid email or password')).toBeTruthy();
  });

  it('validates password length client-side in register mode', async () => {
    const { getByTestId, queryByText } = render(<LoginScreen />);
    fireEvent.press(getByTestId('mode-toggle'));
    fireEvent.changeText(getByTestId('email-input'), 'a@b.com');
    fireEvent.changeText(getByTestId('password-input'), 'short');
    await act(async () => {
      fireEvent.press(getByTestId('email-submit'));
    });
    expect(queryByText('Password must be at least 8 characters.')).toBeTruthy();
    expect(mockRegisterWithEmail).not.toHaveBeenCalled();
  });

  it('surfaces a redirectError from the OAuth callback as a conflict banner', () => {
    mockRedirectError = { error: 'account_exists', provider: 'github' };
    const { getByTestId, queryByText } = render(<LoginScreen />);
    expect(getByTestId('conflict-banner')).toBeTruthy();
    expect(
      queryByText(
        'This email is registered with GitHub. Use the GitHub button below to sign in.',
      ),
    ).toBeTruthy();
  });
});
