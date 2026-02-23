# Adding Dashboard Users

This guide explains how to allow a new user (e.g. **ckloss@stedward.org**) to sign in and use the dashboard, including the **event search** feature.

Two things must be in place for a user to use the dashboard successfully:

1. **Azure AD (sign-in)** – The user must be allowed to sign in to the app.
2. **Microsoft 365 (calendar access)** – The user's account must have access to the shared calendar so that features like event search work.

Optionally, you can restrict the app to a fixed list of users with an environment variable.

---

## 1. Allow the user to sign in (Azure AD)

If the app registration is set to **Assign users** (recommended), you must add the new user in Azure.

1. In **Azure Portal** go to **Microsoft Entra ID** (Azure Active Directory) → **Enterprise applications**.
2. Open the application used by St. Edward Calendar Sync.
3. Go to **Users and groups**.
4. Click **Add user/group** and add the new user (e.g. **ckloss@stedward.org**).
5. Save.

After this, the user can sign in with Microsoft. If you do **not** add them here, they will get an error when they try to sign in (e.g. "AADSTS50105: user is not assigned to the application").

---

## 2. Grant calendar access (Microsoft 365)

Event search (and any dashboard feature that reads the calendar) uses the **signed-in user's** token to call Microsoft Graph. So the user must have access to the **shared mailbox calendar** (e.g. `calendar@stedward.org` and the "St. Edward Public Calendar" if it's a sub-calendar).

1. In **Microsoft 365 admin center** (or Exchange admin center), open the **shared mailbox** used by the app (e.g. `calendar@stedward.org`).
2. Add the new user (e.g. **ckloss@stedward.org**) with at least **Read** access to the mailbox/calendar (or **Full access** if you want them to manage the calendar too).
3. Wait a few minutes for permissions to apply.

If this step is missing, the user can sign in but will get errors (e.g. 403 Forbidden) when using event search or other calendar features.

---

## 3. Optional: restrict dashboard to specific users

If you want the app to **only** allow certain users (even if more can sign in via Azure), set the optional environment variable:

```bash
ALLOWED_DASHBOARD_USERS=rcarroll@stedward.org,eharnisch@stedward.org,ckloss@stedward.org
```

- Use comma-separated addresses, no spaces (or trim spaces when editing).
- Comparison is case-insensitive.
- If this variable is **not** set or is empty, any user who can sign in via Azure and has calendar access can use the dashboard.

When adding a new user (e.g. ckloss@stedward.org):

1. Add them in Azure AD (step 1) and M365 (step 2) as above.
2. If you use `ALLOWED_DASHBOARD_USERS`, add their email to that list and redeploy or restart the app.

---

## Summary: adding ckloss@stedward.org

| Step | Where | Action |
|------|--------|--------|
| 1 | Azure Portal → Enterprise application → Users and groups | Add **ckloss@stedward.org** |
| 2 | Microsoft 365 → Shared mailbox (e.g. calendar@stedward.org) | Grant **ckloss@stedward.org** access to the calendar |
| 3 | Hosting env (optional) | Add `ckloss@stedward.org` to `ALLOWED_DASHBOARD_USERS` if you use it |

After steps 1 and 2 (and 3 if applicable), ckloss@stedward.org can sign in and use event search successfully.
