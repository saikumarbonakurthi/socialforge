// src/db.js — SQLite queue persistence layer
const Database = require("better-sqlite3");
const path = require("path");
const { v4: uuidv4 } = require("uuid");

const DB_PATH = path.join(__dirname, "../data/queue.db");

// Ensure data directory exists
const fs = require("fs");
fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });

const db = new Database(DB_PATH);

// Enable WAL mode for better concurrent read performance
db.pragma("journal_mode = WAL");

// Schema
db.exec(`
  CREATE TABLE IF NOT EXISTS posts (
    id          TEXT PRIMARY KEY,
    topic       TEXT NOT NULL,
    tone        TEXT NOT NULL,
    scheduled_for DATETIME NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
  );

  CREATE TABLE IF NOT EXISTS platform_posts (
    id          TEXT PRIMARY KEY,
    post_id     TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    platform    TEXT NOT NULL,
    content     TEXT NOT NULL,
    hashtags    TEXT,         -- JSON array string
    image_prompt TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',
    result      TEXT,         -- JSON: { success, platform_post_id, error }
    posted_at   DATETIME,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
  );

  CREATE INDEX IF NOT EXISTS idx_posts_status_time
    ON posts(status, scheduled_for);

  CREATE INDEX IF NOT EXISTS idx_platform_posts_post_id
    ON platform_posts(post_id);
`);

// ── Queue Operations ──────────────────────────────────────────

const addToQueue = (job) => {
  const postId = uuidv4();
  const insertPost = db.prepare(`
    INSERT INTO posts (id, topic, tone, scheduled_for, status)
    VALUES (@id, @topic, @tone, @scheduled_for, 'pending')
  `);

  const insertPlatformPost = db.prepare(`
    INSERT INTO platform_posts (id, post_id, platform, content, hashtags, image_prompt, status)
    VALUES (@id, @post_id, @platform, @content, @hashtags, @image_prompt, 'pending')
  `);

  const transaction = db.transaction((job) => {
    insertPost.run({
      id: postId,
      topic: job.topic,
      tone: job.tone || "Professional",
      scheduled_for: job.scheduledFor,
    });

    for (const [platform, content] of Object.entries(job.posts)) {
      insertPlatformPost.run({
        id: uuidv4(),
        post_id: postId,
        platform,
        content,
        hashtags: JSON.stringify(job.hashtags?.[platform] || []),
        image_prompt: job.imagePrompt || null,
      });
    }
  });

  transaction(job);
  return postId;
};

const getPendingDue = () => {
  return db.prepare(`
    SELECT p.id, p.topic, p.tone, p.scheduled_for,
           pp.id as pp_id, pp.platform, pp.content, pp.hashtags, pp.image_prompt
    FROM posts p
    JOIN platform_posts pp ON pp.post_id = p.id
    WHERE p.status = 'pending'
      AND pp.status = 'pending'
      AND datetime(p.scheduled_for) <= datetime('now')
    ORDER BY p.scheduled_for ASC
  `).all();
};

const markPlatformResult = (ppId, success, result) => {
  db.prepare(`
    UPDATE platform_posts
    SET status = @status, result = @result, posted_at = CURRENT_TIMESTAMP,
        updated_at = CURRENT_TIMESTAMP
    WHERE id = @id
  `).run({
    id: ppId,
    status: success ? "posted" : "failed",
    result: JSON.stringify(result),
  });
};

const finalizePostStatus = (postId) => {
  const platforms = db.prepare(`
    SELECT status FROM platform_posts WHERE post_id = ?
  `).all(postId);

  const allDone = platforms.every((p) => p.status !== "pending");
  if (!allDone) return;

  const anyFailed = platforms.some((p) => p.status === "failed");
  const newStatus = anyFailed ? "partial" : "completed";

  db.prepare(`
    UPDATE posts SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?
  `).run(newStatus, postId);
};

const getQueue = (status = null, limit = 50) => {
  const where = status ? `WHERE p.status = '${status}'` : "";
  const posts = db.prepare(`
    SELECT p.id, p.topic, p.tone, p.scheduled_for, p.status, p.created_at
    FROM posts p
    ${where}
    ORDER BY p.scheduled_for DESC
    LIMIT ?
  `).all(limit);

  return posts.map((post) => {
    const platforms = db.prepare(`
      SELECT platform, content, hashtags, status, result, posted_at
      FROM platform_posts WHERE post_id = ?
    `).all(post.id);

    return {
      ...post,
      platforms: platforms.map((pp) => ({
        ...pp,
        hashtags: JSON.parse(pp.hashtags || "[]"),
        result: pp.result ? JSON.parse(pp.result) : null,
      })),
    };
  });
};

const deletePost = (postId) => {
  db.prepare("DELETE FROM posts WHERE id = ?").run(postId);
};

const getStats = () => {
  return db.prepare(`
    SELECT
      COUNT(CASE WHEN status = 'pending'   THEN 1 END) AS pending,
      COUNT(CASE WHEN status = 'completed' THEN 1 END) AS completed,
      COUNT(CASE WHEN status = 'partial'   THEN 1 END) AS partial,
      COUNT(CASE WHEN status = 'failed'    THEN 1 END) AS failed,
      COUNT(*) AS total
    FROM posts
  `).get();
};

module.exports = {
  addToQueue,
  getPendingDue,
  markPlatformResult,
  finalizePostStatus,
  getQueue,
  deletePost,
  getStats,
};
