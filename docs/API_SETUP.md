# ATTA VIDEO AI — API Setup

Configure credentials from **Dashboard → API & Integrations**.

If a credential is already saved, leave its field empty unless you want to replace it.

> Provider dashboards change over time. These steps reflect current provider documentation in September 2026.

## Google Gemini

Used for metadata, scene planning, media prompts, script fallback and live Google Search grounding.

### Get a key

1. Sign in to Google AI Studio.
2. Open **Dashboard → API Keys**.
3. Select/import the Google Cloud project you want to use.
4. Click **Create API key**.
5. Copy the new key.
6. Paste it into **Gemini API Key** in ATTA.
7. Click **Save API Settings**.

Current Google docs say new AI Studio keys are authorization keys and older unrestricted standard keys are being phased out. For a new ATTA setup, create a current key in AI Studio.

Official docs:

https://ai.google.dev/gemini-api/docs/api-key

https://ai.google.dev/gemini-api/docs/get-started

---

## Together AI

Used for long-form script generation.

Production routing:

1. DeepSeek V4 Flash
2. DeepSeek V4 Pro fallback
3. Gemini last fallback

### Get a key

1. Sign in to Together AI.
2. Open the project for ATTA.
3. Open **API Keys**.
4. Click **Create API Key**.
5. Give it a production name.
6. Copy it immediately; new keys are only shown once.
7. Paste it into **Together AI API Key** in ATTA.
8. Save.

Official docs:

https://docs.together.ai/docs/api-keys-authentication

https://docs.together.ai/docs/quickstart

---

## Pexels

Used for stock image/video search.

1. Create/sign in to Pexels.
2. Request an API key.
3. Copy it.
4. Paste it into **Pexels API Key**.
5. Save.

Official:

https://help.pexels.com/hc/en-us/articles/900004904026-How-do-I-get-an-API-key

https://www.pexels.com/api/

---

## Pixabay

Used as another stock-media provider.

1. Create/sign in to Pixabay.
2. Open the API documentation.
3. Obtain the key associated with your account.
4. Paste it into **Pixabay API Key**.
5. Save.

Official:

https://pixabay.com/api/docs/

Current Pixabay docs list a default limit of 100 requests per 60 seconds per API key.

---

## Coverr

Used as another stock-video provider.

1. Create a Coverr account.
2. Open the developer area.
3. Create/register an application.
4. Copy the generated API key.
5. Paste it into **Coverr API Key**.
6. Save.

Official:

https://api.coverr.co/docs/start/

https://api.coverr.co/docs/auth/

Check the current Coverr quota before scaling many parallel jobs.

---

## Openverse

ATTA supports authenticated Openverse access with:

- Client ID
- Client Secret

Create/register an Openverse application/client, then enter both credentials under **Openverse credentials** in ATTA.

Official auth/throttling docs:

https://docs.openverse.org/api/reference/authentication_and_throttling.html

API root:

https://api.openverse.org/v1/

---

## Google OAuth for YouTube

A Gemini API key is **not** enough for YouTube publishing.

You need:

- Google Cloud project
- YouTube Data API v3
- Google Auth Platform configuration
- Web OAuth Client ID
- Web OAuth Client Secret
- exact Authorized Redirect URI

See `YOUTUBE_GOOGLE_CLOUD.md`.

---

## Zernio for Facebook

ATTA currently uses Zernio for Facebook publishing.

### Get a key

1. Sign up/log in to Zernio.
2. Open **API Keys**.
3. Click **Create API key**.
4. Copy it immediately.
5. Paste it into **Zernio API Key** in ATTA.
6. Save.

Official:

https://docs.zernio.com/

Facebook:

https://docs.zernio.com/platforms/facebook

Zernio base API:

```text
https://zernio.com/api/v1
```

If Facebook publishing is not needed, Zernio can remain unconfigured.

---

## Recommended provider status

| Provider | Recommendation |
|---|---|
| Gemini | Required |
| Together AI | Required |
| Pexels | Recommended |
| Pixabay | Recommended |
| Coverr | Recommended |
| Openverse | Recommended |
| Google OAuth | Required for YouTube |
| Zernio | Required for Facebook |

## Security

Never put production keys in Git, screenshots, public tickets or README files. Rotate any credential that is exposed.
