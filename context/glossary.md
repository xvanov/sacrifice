# Glossary

## Goal
A commitment the user creates in Sacrifice, with a deadline, criteria, and a pledge amount attached (`PRD.md`, `backend/app/routes/goals.py`).

## Pledge
The money the user puts at risk for failing a goal. A valid authenticated session can reach payment setup and payment history tied to that pledge (`PRD.md`, `backend/app/routes/payment.py`).

## Proof submission
The evidence a user sends before a deadline so the system can verify the goal outcome (`PRD.md`, `backend/app/routes/goals.py`).

## Goal type
The verification shape attached to a goal, discovered by the backend at startup and exposed through the API (`backend/app/main.py`, `backend/app/routes/goals.py`).

## Pending auth code
The one-time server-side login handoff stored after OAuth callback processing and consumed by `/api/auth/exchange` so the frontend does not receive a raw access token in the redirect URL (`backend/app/routes/auth.py`, `backend/tests/test_auth.py`).

## Auth session id
The server-side session marker on the user record that binds otherwise bearer-style access tokens to the currently active login session (`backend/app/services/auth.py`, `backend/app/models/user.py`, `backend/app/core/dependencies.py`).

## Provider conflict
The explicit `account_exists` response or redirect that tells the client which provider already owns an email address, preventing silent account takeover or duplicate-account drift (`backend/tests/test_auth.py`, `backend/tests/test_email_auth.py`, `frontend/screens/LoginScreen.tsx`).

## Pledge abuse
The practical consequence of account impersonation in this app: an attacker who gets valid bearer material can act as the victim across goal, payment, notification, and other authenticated surfaces (`backend/app/core/dependencies.py`, `backend/app/routes/goals.py`, `backend/app/routes/payment.py`).
