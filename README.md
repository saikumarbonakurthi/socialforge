# SocialForge Backend

Auto-posting backend for SocialForge AI. Node.js + Express + SQLite + node-cron.
Checks queue every minute and auto-posts to configured platforms.

---

## Architecture

```
Frontend (React) ──POST /api/queue──► Express Server
                                          │
                              node-cron (every 1 min)
                                          │
                         ┌────────────────┼────────────────┐
                      Twitter/X      LinkedIn         Facebook
                      (v2 API)    (UGC Posts API)  (Graph API)
                                          │
                                   Instagram  WhatsApp
                                  (Graph API)  (Cloud API)
```

---

## Quick Start (Local)

```bash
# 1. Install dependencies
npm install

# 2. Copy environment file and fill credentials
cp .env.example .env
# Edit .env with your API keys

# 3. Start
npm start

# Development (auto-restart)
npm run dev
```

---

## Deploy to Railway (Recommended — Free tier works)

1. Push this folder to a GitHub repo
2. Go to https://railway.app → New Project → Deploy from GitHub
3. Select your repo
4. In Railway dashboard → Variables → add all your .env values
5. Railway auto-detects Node.js and runs `npm start`
6. Your backend URL will be: `https://your-app.railway.app`

**Update FRONTEND_URL** in Railway variables to your frontend domain.

---

## Deploy to Render (Alternative)

1. Push to GitHub
2. https://render.com → New Web Service → Connect repo
3. Build Command: `npm install`
4. Start Command: `npm start`
5. Add environment variables in the Render dashboard

---

## Connecting the Frontend

In the SocialForge React app, when adding to queue, also call this backend:

```javascript
const BACKEND_URL = "https://your-backend.railway.app";
const API_SECRET  = "your_api_secret_from_env";

const addToBackendQueue = async (job) => {
  const res = await fetch(`${BACKEND_URL}/api/queue`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-secret": API_SECRET,
    },
    body: JSON.stringify({
      topic:        job.topic,
      tone:         job.tone,
      scheduledFor: `${job.date}T${job.time}:00`,
      posts:        job.posts,      // { linkedin: "...", twitter: "...", ... }
      hashtags:     job.hashtags,   // { linkedin: ["tag1"], twitter: [...], ... }
      imagePrompt:  job.imagePrompt,
    }),
  });
  return res.json();
};
```

---

## Getting API Credentials

### Twitter / X
1. Go to https://developer.twitter.com/en/portal/dashboard
2. Create a project → Create an App
3. **Important**: Set App permissions to "Read and Write"
4. Under "Keys and tokens":
   - Copy API Key → TWITTER_API_KEY
   - Copy API Secret → TWITTER_API_SECRET
5. Generate "Access Token and Secret" (under your own account):
   - Copy → TWITTER_ACCESS_TOKEN + TWITTER_ACCESS_TOKEN_SECRET
6. **Cost**: Free tier (500 tweets/month). $100/month for Basic (50k tweets).

### LinkedIn
1. Go to https://www.linkedin.com/developers/apps → Create App
2. Add products: "Share on LinkedIn" + "Sign In with LinkedIn"
3. Under Auth tab → copy Client ID + Client Secret
4. Generate OAuth 2.0 token via: https://www.linkedin.com/developers/tools/oauth/token-generator
   - Select scopes: `w_member_social`, `r_liteprofile`
   - Copy the generated token → LINKEDIN_ACCESS_TOKEN
5. Get your Person URN:
   ```
   GET https://api.linkedin.com/v2/me
   Authorization: Bearer {your_token}
   ```
   Copy `id` field → format as `urn:li:person:{id}` → LINKEDIN_PERSON_URN
6. **Cost**: Free for personal posting.

### Facebook (Page Posts)
1. Go to https://developers.facebook.com/apps → Create App → Business
2. Add "Facebook Login" product
3. Go to Graph API Explorer: https://developers.facebook.com/tools/explorer/
4. Select your app → Generate User Token with:
   - `pages_manage_posts`, `pages_read_engagement`
5. Exchange for Page Access Token:
   ```
   GET /me/accounts → find your page → copy access_token
   ```
   → FACEBOOK_PAGE_ACCESS_TOKEN
6. Copy Page ID → FACEBOOK_PAGE_ID
7. **Cost**: Free.

### Instagram (Business)
**Prerequisite**: Instagram account must be a Business/Creator account, linked to a Facebook Page.

1. Same Meta App as Facebook above
2. Add "Instagram Graph API" product
3. In Graph API Explorer:
   ```
   GET /me/accounts → get page ID
   GET /{page-id}?fields=instagram_business_account → get IG account ID
   ```
   → INSTAGRAM_ACCOUNT_ID
4. Use same long-lived token as Facebook → INSTAGRAM_ACCESS_TOKEN
5. **CRITICAL**: Instagram posts via API REQUIRE an image. Text-only not supported.
   For auto-posting with images:
   - Generate image with DALL-E API (separate key needed)
   - Upload to Cloudinary/S3
   - Pass public URL to the poster

### WhatsApp Business
1. Go to https://developers.facebook.com/apps → same Meta app
2. Add WhatsApp product
3. Under WhatsApp → API Setup:
   - Copy Phone Number ID → WHATSAPP_PHONE_NUMBER_ID
   - Copy Temporary Access Token (get permanent token for production) → WHATSAPP_ACCESS_TOKEN
4. **CRITICAL**: Free-form messages only work within 24h window of user interaction.
   For scheduled marketing messages, you MUST use Meta-approved Message Templates.
   Apply for template approval at: https://business.facebook.com/wa/manage/message-templates/

---

## API Reference

All endpoints (except `/health`) require header: `x-api-secret: YOUR_SECRET`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /api/queue/health | Health check (no auth) |
| GET | /api/queue | List queue (`?status=pending&limit=20`) |
| POST | /api/queue | Add post to queue |
| DELETE | /api/queue/:id | Remove post |
| POST | /api/queue/:id/post-now | Force immediate post |
| POST | /api/queue/test-post | Test single platform |
| GET | /api/queue/stats | Queue statistics |

### POST /api/queue — Request Body
```json
{
  "topic": "How Eskoolia Suite digitizes schools in 30 days",
  "tone": "Professional",
  "scheduledFor": "2026-03-24T09:00:00",
  "posts": {
    "linkedin": "Full LinkedIn post text...",
    "twitter": "Tweet text under 280 chars",
    "instagram": "Instagram caption...",
    "facebook": "Facebook post...",
    "whatsapp": "WhatsApp message..."
  },
  "hashtags": {
    "linkedin": ["EdTech", "SchoolERP", "DigitalIndia"],
    "twitter": ["EdTech", "Schools"],
    "instagram": ["EdTech", "SchoolManagement", "DigitalSchools"]
  },
  "imagePrompt": "Professional photo of modern school office..."
}
```

---

## Scheduler Notes

- Runs every **minute** via node-cron
- Timezone set to `Asia/Kolkata` (IST) — change in `src/jobs/scheduler.js` if needed
- Posts scheduled within the same minute fire together
- Failed platforms are retried on the **next** scheduler run (within same minute window)
- Platform results stored in SQLite — query via GET /api/queue for full audit trail

---

## Limitations (Be Aware)

| Platform | Limitation |
|----------|------------|
| Twitter/X | Free: 500 posts/month. Rate limits apply. |
| LinkedIn | Token expires every 60 days — must refresh manually |
| Instagram | **Requires image URL**. Text-only posts not supported via API |
| WhatsApp | Scheduled marketing requires pre-approved templates |
| Facebook | Page Access Tokens need renewal unless made permanent |

---

## File Structure

```
socialforge-backend/
├── src/
│   ├── server.js          # Express app entry point
│   ├── db.js              # SQLite queue operations
│   ├── routes/
│   │   └── queue.js       # REST API routes
│   ├── services/
│   │   └── poster.js      # Platform-specific posting logic
│   └── jobs/
│       └── scheduler.js   # Cron job runner
├── data/
│   └── queue.db           # SQLite file (auto-created)
├── .env.example           # Environment template
├── package.json
└── README.md
```
