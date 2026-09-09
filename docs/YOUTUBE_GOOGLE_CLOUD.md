# Google Cloud & YouTube OAuth Setup

ATTA uses Google OAuth 2.0 to connect YouTube channels and publish for the authorized channel.

A Gemini API key does **not** replace this setup.

Google's current OAuth UI is the **Google Auth Platform**, with areas including Branding, Audience, Clients, Data Access and Verification Center.

Official references:

https://support.google.com/cloud/answer/15544987

https://developers.google.com/identity/protocols/oauth2/web-server

https://developers.google.com/youtube/v3/getting-started

## 1. Decide the public ATTA URL

You need the real public HTTPS URL of the deployed app.

Example:

```text
https://attavideoai.site
```

The callback becomes:

```text
https://attavideoai.site/youtube/oauth/callback
```

If the production dashboard is on another hostname/subdomain, use that real hostname instead.

## 2. Create/select a Google Cloud project

1. Open Google Cloud Console:

   https://console.cloud.google.com/

2. Create a project or choose the project dedicated to ATTA.
3. Example project name: `ATTA Video AI`.

## 3. Enable YouTube Data API v3

1. Open **APIs & Services → Library**.
2. Search for **YouTube Data API v3**.
3. Open it.
4. Click **Enable**.

## 4. Configure Google Auth Platform

### Branding

Configure:

- App name
- User support email
- Developer contact email
- Homepage
- Privacy Policy
- Terms where applicable
- Authorized domain(s)

Official branding guide:

https://support.google.com/cloud/answer/15549049

### Audience

Choose the correct audience.

**External** is appropriate when ordinary Google accounts outside one Workspace organization must authorize.

During testing, add the Google accounts that need access if Google requires test users for the project's publishing state.

**Internal** is only for eligible Google Workspace organization users.

### Data Access

Review the OAuth scopes requested by ATTA and request only what is needed.

Google may require verification for sensitive/restricted access before broad production use.

### Verification Center

Use this if Google requires branding or data-access verification before production.

## 5. Create the OAuth client

1. Open **Google Auth Platform → Clients**.
2. Click **Create Client**.
3. Choose **Web application**.
4. Name it, e.g. `ATTA Video AI Web`.
5. Add the production callback under **Authorized redirect URIs**.

Example:

```text
https://attavideoai.site/youtube/oauth/callback
```

6. Create the client.
7. Copy the **Client ID** and **Client Secret**.

Google requires the redirect URI sent by ATTA to exactly match an Authorized Redirect URI. Scheme, hostname, path and trailing slash matter.

## 6. Enter values in ATTA

Open **ATTA → API & Integrations**.

### Public App URL

Set your real production root URL.

### OAuth Callback URL

ATTA should display/build the callback from that public URL.

### Client ID / Secret

Paste the Web OAuth Client ID and Client Secret.

Click **Save API Settings**.

## 7. Connect a channel

1. Open **Publishing** in ATTA.
2. Start the YouTube connection flow.
3. Sign in to the Google account that owns/manages the channel.
4. Approve requested permissions.
5. Google redirects back to ATTA.
6. Confirm the channel appears as connected.

Each channel has its own authorization/token state.

## 8. `redirect_uri_mismatch`

If Google shows this error, compare the exact URI sent by ATTA against the Authorized Redirect URI in Google Cloud.

For example, these are different:

```text
https://attavideoai.site/youtube/oauth/callback
https://attavideoai.site/youtube/oauth/callback/
```

Likewise `http` and `https` do not match.

## 9. Testing-state issue

If the app is still in testing, only permitted/test users may be allowed to authorize. Add the required account as a test user when applicable, or complete Google's production/verification process.

## 10. Protect OAuth secrets

Never commit the Client Secret, access tokens, refresh tokens, `.env` or production database.
