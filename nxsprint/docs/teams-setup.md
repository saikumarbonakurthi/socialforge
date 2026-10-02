# Teams two way setup (Azure Bot)

Why a bot: the Power Automate webhook from Phase 4 can post messages but cannot receive replies. To let people reply `ack` or answer standup, NxSprint needs its own Teams bot. The bot also sends standup prompts and nudges to anyone who has talked to it.

I could not create or test any of this (it needs your Azure tenant). Every step below is written from documentation, not from a run. If a screen or field differs, send me what you see.

## What you need
- Azure and Microsoft 365 admin rights (or someone who has them), and a public https URL for core. `localhost:8000` will not work, Azure must reach `https://<your host>/webhooks/teams`. A reverse proxy or a tunnel is fine for testing.

## 1. App registration (Microsoft Entra ID)
1. Entra admin center, App registrations, New registration. Name `NxSprint`. Supported account types: **Single tenant**.
2. Copy the **Application (client) ID** and the **Directory (tenant) ID**.
3. Certificates and secrets, New client secret. Copy the secret **value** now, it is shown once.

## 2. Azure Bot resource
1. Azure portal, create a resource, **Azure Bot**. Handle `nxsprint`. Type of app: **Single tenant**. Use existing app registration, paste the client ID and tenant ID from step 1.
2. Configuration, Messaging endpoint: `https://<your host>/webhooks/teams`.
3. Channels, add **Microsoft Teams**. Accept the terms.

## 3. Put the three values in core's environment
In `.env` (never commit it):
```
NXSPRINT_BOT_APP_ID=<client id>
NXSPRINT_BOT_APP_PASSWORD=<client secret value>
NXSPRINT_BOT_TENANT_ID=<tenant id>
```
Restart core. All three are required together, core refuses to start with only some. Without them the bot endpoints answer 503 and everything keeps working over the webhook as before.

## 4. Teams app package
1. Copy `docs/teams-manifest.example.json`, replace both `REPLACE_WITH_APP_ID` values with the client ID, and add two icons next to it: `color.png` (192 by 192) and `outline.png` (32 by 32). Zip the three files.
2. Teams admin center, Manage apps, Upload new app (or Teams, Apps, Manage your apps, Upload a custom app for a test).
3. Each member opens the app and starts a chat with it, or you assign it by policy. The bot learns where to reach a person only after they message it or install it. Until then that person is served through the webhook and cannot reply.

## 5. Tell NxSprint who is who
`teams_user_id` in `config/projects.yaml` must be the person's **Microsoft Entra object ID** (Entra admin center, Users, the user, Object ID). The bot matches the sender by that id.

## How it behaves
- Reply `ack` to acknowledge every open nudge sent to you, or `ack 12` for one. `help` lists the commands.
- At the project's standup time each member gets their open sprint items and a request for three lines: `Done: ...`, `Doing: ...`, `Blocked: ...`. A reply in that format is stored, anything else is stored as written. Replying again the same day replaces the earlier note.
- From `standup_summary_time` one summary is posted to the team channel (through the team webhook): replies, what moved to Done, blockers on the board, and risks from the rules.
- Every inbound request is checked: Microsoft's signature, our app id as audience, the issuer, expiry, and the service URL in the token must equal the one in the message (stops a stolen token redirecting our replies). Anything else gets 401.
- In `dry_run` replies are written to the outbox as `teams_reply`, nothing is sent.
