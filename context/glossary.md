# Glossary

## Goal
A user-defined commitment with a title, deadline, pledge amount, goal type, recurrence setting, and current status.

## Pledge
The amount of money, stored in cents in the codebase, that is charged if a goal fails.

## Charity
The Stripe Connect destination account that receives money when a failed goal is charged.

## Proof submission
The artifact a user sends to support completion of a goal, such as a YouTube URL, an API endpoint payload, or repository details for sandbox/repo checks.

## Verification status
The state attached to a proof submission while asynchronous workers evaluate whether the proof satisfies the goal.

## Goal criteria
The structured per-goal rules that define what proof should look like for a given goal type.

## Recurring goal
A goal whose next instance is created automatically after the current period ends.

## Donation receipt
A notification/payment outcome generated when a failed goal is successfully charged and transferred.

## Dev sandbox
The verification mode described in the product requirements where repository code is checked in an isolated environment.

## GitHub repo goal
A code-visible goal type that checks a repository-based deliverable and is present in the current backend routes, frontend types, and CLI.
