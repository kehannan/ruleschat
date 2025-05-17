# MySite2

This project is a small FastAPI web app. A new invitation system allows administrators to send invite links that let new users create accounts.

## Inviting Users

1. Log in as the admin user (defined by the `ADMIN_USERNAME` environment variable).
2. Navigate to `/admin/invite` to create an invite for an email address. The server prints the invite link to the console – send this link to the user via email.
3. The user visits the link and completes the sign‑up form to create their account.
4. Admins can revoke invites from the same page.
