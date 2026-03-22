// src/jobs/scheduler.js — Cron-based queue processor
const cron = require("node-cron");
const db = require("../db");
const { postToPlatform } = require("../services/poster");

let isRunning = false;

const processQueue = async () => {
  if (isRunning) {
    console.log("[Scheduler] Previous run still active, skipping.");
    return;
  }

  isRunning = true;

  try {
    const duePosts = db.getPendingDue();

    if (duePosts.length === 0) {
      isRunning = false;
      return;
    }

    // Group platform_posts by their parent post ID
    const grouped = {};
    for (const row of duePosts) {
      if (!grouped[row.id]) grouped[row.id] = [];
      grouped[row.id].push(row);
    }

    console.log(
      `[Scheduler] Processing ${Object.keys(grouped).length} post(s) with ${duePosts.length} platform task(s)`
    );

    for (const [postId, platformRows] of Object.entries(grouped)) {
      // Run all platforms for this post concurrently
      await Promise.allSettled(
        platformRows.map(async (row) => {
          const hashtags = JSON.parse(row.hashtags || "[]");
          console.log(`  → [${row.platform.toUpperCase()}] Posting: "${row.topic?.slice(0, 40)}..."`);

          const result = await postToPlatform(
            row.platform,
            row.content,
            hashtags
          );

          db.markPlatformResult(row.pp_id, result.success, result);

          if (result.success) {
            console.log(`  ✓ [${row.platform.toUpperCase()}] Posted. ID: ${result.platform_post_id}`);
          } else {
            console.error(`  ✗ [${row.platform.toUpperCase()}] Failed: ${result.error}`);
          }
        })
      );

      // Update parent post status based on platform results
      db.finalizePostStatus(postId);
    }
  } catch (err) {
    console.error("[Scheduler] Unexpected error:", err.message);
  } finally {
    isRunning = false;
  }
};

const startScheduler = () => {
  // Run every minute — checks for any posts due at current time
  cron.schedule("* * * * *", processQueue, {
    scheduled: true,
    timezone: "Asia/Kolkata", // Change to your timezone if needed
  });

  console.log("[Scheduler] Started — checking queue every minute (IST timezone)");

  // Also run immediately on startup to catch any missed posts
  processQueue();
};

module.exports = { startScheduler, processQueue };
