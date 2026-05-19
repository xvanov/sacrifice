import React from 'react';
import { render, fireEvent, act } from '@testing-library/react-native';
import LoginScreen from '../../screens/LoginScreen';

const mockLoginWithGoogle = jest.fn();
const mockLoginWithGithub = jest.fn();

jest.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({
    user: null,
    isLoading: false,
    isAuthenticated: false,
    loginWithGoogle: mockLoginWithGoogle,
    loginWithGithub: mockLoginWithGithub,
    logout: jest.fn(),
  }),
}));

beforeEach(() => {
  mockLoginWithGoogle.mockReset();
  mockLoginWithGithub.mockReset();
});

describe('LoginScreen', () => {
  it('renders the app title and tagline', () => {
    const { getByText } = render(<LoginScreen />);
    expect(getByText('Sacrifice')).toBeTruthy();
    expect(getByText('Commit to your goals. Put money on the line.')).toBeTruthy();
  });

  it('renders both OAuth buttons when user is not authenticated', () => {
    const { getByText } = render(<LoginScreen />);
    expect(getByText('Sign in with Google')).toBeTruthy();
    expect(getByText('Sign in with GitHub')).toBeTruthy();
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

  it('shows loading indicator on Google button when pressed', () => {
    const { getByText, queryByTestId } = render(<LoginScreen />);
    const googleBtn = getByText('Sign in with Google');
    fireEvent.press(googleBtn);
    expect(mockLoginWithGoogle).toHaveBeenCalledTimes(1);
  });

  it('shows loading indicator on GitHub button when pressed', () => {
    const { getByText } = render(<LoginScreen />);
    const githubBtn = getByText('Sign in with GitHub');
    fireEvent.press(githubBtn);
    expect(mockLoginWithGithub).toHaveBeenCalledTimes(1);
  });
});
