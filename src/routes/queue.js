// src/routes/queue.js — REST API for the frontend
const express = require("express");
const router = express.Router();
const db = require("../db");
const { postToPlatform } = require("../services/poster");

// ── Auth middleware ────────────────────────────────────────────
const requireSecret = (req, res, next) => {
  const secret = req.headers["x-api-secret"];
  if (!process.env.API_SECRET || secret !== process.env.API_SECRET) {
    return res.status(401).json({ error: "Unauthorized" });
  }
  next();
};

// ── GET /api/queue ─────────────────────────────────────────────
// Returns scheduled queue (optionally filtered by status)
router.get("/", requireSecret, (req, res) => {
  try {
    const { status, limit } = req.query;
    const queue = db.getQueue(status || null, parseInt(limit) || 50);
    res.json({ success: true, data: queue });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// ── POST /api/queue ────────────────────────────────────────────
// Add a new scheduled post batch to the queue
// Body: { topic, tone, scheduledFor, posts: { platform: content }, hashtags, imagePrompt }
router.post("/", requireSecret, (req, res) => {
  try {
    const { topic, tone, scheduledFor, posts, hashtags, imagePrompt } = req.body;

    if (!topic || !scheduledFor || !posts || Object.keys(posts).length === 0) {
      return res.status(400).json({
        success: false,
        error: "Required: topic, scheduledFor, posts (object with platform keys)",
      });
    }

    const scheduled = new Date(scheduledFor);
    if (isNaN(scheduled)) {
      return res.status(400).json({ success: false, error: "Invalid scheduledFor date" });
    }

    const postId = db.addToQueue({
      topic,
      tone: tone || "Professional",
      scheduledFor: scheduled.toISOString(),
      posts,
      hashtags: hashtags || {},
      imagePrompt: imagePrompt || null,
    });

    res.status(201).json({ success: true, postId });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// ── DELETE /api/queue/:id ──────────────────────────────────────
// Remove a post from the queue
router.delete("/:id", requireSecret, (req, res) => {
  try {
    db.deletePost(req.params.id);
    res.json({ success: true });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// ── POST /api/queue/:id/post-now ──────────────────────────────
// Force immediate posting (bypass scheduler)
router.post("/:id/post-now", requireSecret, async (req, res) => {
  try {
    const queue = db.getQueue(null, 200);
    const job = queue.find((q) => q.id === req.params.id);
    if (!job) return res.status(404).json({ success: false, error: "Post not found" });

    const results = {};

    await Promise.allSettled(
      job.platforms.map(async (pp) => {
        const result = await postToPlatform(pp.platform, pp.content, pp.hashtags);
        db.markPlatformResult(pp.id || `${job.id}-${pp.platform}`, result.success, result);
        results[pp.platform] = result;
      })
    );

    db.finalizePostStatus(job.id);
    res.json({ success: true, results });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// ── GET /api/queue/stats ───────────────────────────────────────
// Queue statistics
router.get("/stats", requireSecret, (req, res) => {
  try {
    const stats = db.getStats();
    res.json({ success: true, data: stats });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// ── GET /api/queue/health ──────────────────────────────────────
// Public health check (no auth required)
router.get("/health", (req, res) => {
  res.json({ status: "ok", timestamp: new Date().toISOString() });
});

// ── POST /api/queue/test-post ──────────────────────────────────
// Test a single platform post without saving to queue
router.post("/test-post", requireSecret, async (req, res) => {
  try {
    const { platform, content, hashtags } = req.body;
    if (!platform || !content) {
      return res.status(400).json({ success: false, error: "platform and content required" });
    }

    const result = await postToPlatform(platform, content, hashtags || []);
    res.json({ success: result.success, result });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

module.exports = router;
