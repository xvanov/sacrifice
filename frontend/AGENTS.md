# Expo HAS CHANGED

Read the exact versioned docs at https://docs.expo.dev/versions/v54.0.0/ before writing any code.

## Repo memory

- In Playwright auth callback tests that mock `/api/auth/exchange`, also mock `/api/goals` (or use a real dev token). Otherwise HomeScreen quickly 401s with fake tokens, emits `sacrifice-session-expired`, and the UI returns to login before authenticated assertions run.

