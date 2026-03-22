// src/services/poster.js — Platform-specific posting logic
require("dotenv").config();
const { TwitterApi } = require("twitter-api-v2");
const axios = require("axios");
const FormData = require("form-data");

// ── Helper ────────────────────────────────────────────────────────────────────

const buildPostText = (content, hashtags = [], platform) => {
  const tags = hashtags.map((t) => (t.startsWith("#") ? t : `#${t}`)).join(" ");
  if (!tags || platform === "whatsapp") return content;
  return `${content}\n\n${tags}`;
};

// ── Twitter / X ───────────────────────────────────────────────────────────────

const postToTwitter = async (content, hashtags) => {
  const client = new TwitterApi({
    appKey: process.env.TWITTER_API_KEY,
    appSecret: process.env.TWITTER_API_SECRET,
    accessToken: process.env.TWITTER_ACCESS_TOKEN,
    accessSecret: process.env.TWITTER_ACCESS_TOKEN_SECRET,
  });

  const text = buildPostText(content, hashtags, "twitter");

  // Twitter hard limit: 280 chars
  const truncated = text.length > 280 ? text.slice(0, 277) + "..." : text;

  const tweet = await client.v2.tweet({ text: truncated });
  return {
    success: true,
    platform_post_id: tweet.data.id,
    url: `https://twitter.com/i/web/status/${tweet.data.id}`,
  };
};

// ── LinkedIn ──────────────────────────────────────────────────────────────────

const postToLinkedIn = async (content, hashtags) => {
  const text = buildPostText(content, hashtags, "linkedin");
  const authorUrn = process.env.LINKEDIN_PERSON_URN; // urn:li:person:XXXXX

  const body = {
    author: authorUrn,
    lifecycleState: "PUBLISHED",
    specificContent: {
      "com.linkedin.ugc.ShareContent": {
        shareCommentary: { text },
        shareMediaCategory: "NONE",
      },
    },
    visibility: {
      "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC",
    },
  };

  const res = await axios.post("https://api.linkedin.com/v2/ugcPosts", body, {
    headers: {
      Authorization: `Bearer ${process.env.LINKEDIN_ACCESS_TOKEN}`,
      "Content-Type": "application/json",
      "X-Restli-Protocol-Version": "2.0.0",
    },
  });

  const postId = res.headers["x-restli-id"] || res.data.id || "";
  return {
    success: true,
    platform_post_id: postId,
    url: `https://www.linkedin.com/feed/update/${postId}/`,
  };
};

// ── Facebook (Page) ───────────────────────────────────────────────────────────

const postToFacebook = async (content, hashtags) => {
  const text = buildPostText(content, hashtags, "facebook");
  const pageId = process.env.FACEBOOK_PAGE_ID;
  const token = process.env.FACEBOOK_PAGE_ACCESS_TOKEN;

  const res = await axios.post(
    `https://graph.facebook.com/v20.0/${pageId}/feed`,
    { message: text, access_token: token }
  );

  return {
    success: true,
    platform_post_id: res.data.id,
    url: `https://www.facebook.com/${res.data.id}`,
  };
};

// ── Instagram (Business via Graph API) ────────────────────────────────────────
// Instagram text-only posts are NOT supported via API.
// You MUST provide an image URL. This uses the image_prompt as a placeholder concept.
// For production: generate image first (via DALL-E API), upload to CDN, pass URL here.
// This implementation posts a text caption with a placeholder image URL if none given.

const postToInstagram = async (content, hashtags, imageUrl = null) => {
  const caption = buildPostText(content, hashtags, "instagram");
  const accountId = process.env.INSTAGRAM_ACCOUNT_ID;
  const token = process.env.INSTAGRAM_ACCESS_TOKEN;

  if (!imageUrl) {
    // Instagram requires an image. Return a clear error instead of a silent fail.
    return {
      success: false,
      error:
        "Instagram requires an image URL. Generate an image from the image prompt and pass the URL.",
    };
  }

  // Step 1: Create media container
  const containerRes = await axios.post(
    `https://graph.facebook.com/v20.0/${accountId}/media`,
    { image_url: imageUrl, caption, access_token: token }
  );

  const creationId = containerRes.data.id;

  // Step 2: Publish the container
  const publishRes = await axios.post(
    `https://graph.facebook.com/v20.0/${accountId}/media_publish`,
    { creation_id: creationId, access_token: token }
  );

  return {
    success: true,
    platform_post_id: publishRes.data.id,
    url: `https://www.instagram.com/p/${publishRes.data.id}/`,
  };
};

// ── WhatsApp Business ─────────────────────────────────────────────────────────
// WhatsApp Business API (Cloud API) — sends a text message to a recipient.
// For broadcast: use a message template approved by Meta.
// This sends a free-form message (only works within 24h of user-initiated conversation).
// For scheduled marketing: you MUST use approved templates.

const postToWhatsApp = async (content) => {
  const phoneId = process.env.WHATSAPP_PHONE_NUMBER_ID;
  const token = process.env.WHATSAPP_ACCESS_TOKEN;
  const recipient = process.env.WHATSAPP_RECIPIENT_PHONE;

  const body = {
    messaging_product: "whatsapp",
    to: recipient,
    type: "text",
    text: { body: content },
  };

  const res = await axios.post(
    `https://graph.facebook.com/v20.0/${phoneId}/messages`,
    body,
    {
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
    }
  );

  return {
    success: true,
    platform_post_id: res.data.messages?.[0]?.id,
    recipient,
  };
};

// ── Main Dispatcher ───────────────────────────────────────────────────────────

const POSTERS = {
  twitter: postToTwitter,
  linkedin: postToLinkedIn,
  facebook: postToFacebook,
  instagram: postToInstagram,
  whatsapp: postToWhatsApp,
};

const postToPlatform = async (platform, content, hashtags, imageUrl = null) => {
  const poster = POSTERS[platform];
  if (!poster) {
    return { success: false, error: `Unknown platform: ${platform}` };
  }

  try {
    if (platform === "instagram") {
      return await poster(content, hashtags, imageUrl);
    } else if (platform === "whatsapp") {
      return await poster(content);
    } else {
      return await poster(content, hashtags);
    }
  } catch (err) {
    const errorDetail =
      err.response?.data?.error?.message ||
      err.response?.data?.message ||
      err.message ||
      "Unknown API error";

    console.error(`[${platform.toUpperCase()}] Post failed:`, errorDetail);

    return {
      success: false,
      error: errorDetail,
      status_code: err.response?.status,
    };
  }
};

module.exports = { postToPlatform };
