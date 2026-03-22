// src/server.js — SocialForge Backend Entry Point
require("dotenv").config();

const path = require("path");
const express = require("express");
const cors = require("cors");
const queueRoutes = require("./routes/queue");
const { startScheduler } = require("./jobs/scheduler");

const app = express();
const PORT = process.env.PORT || 3001;

// ── Middleware ────────────────────────────────────────────────
app.use(cors({
  origin: process.env.FRONTEND_URL || "*",
  methods: ["GET", "POST", "DELETE", "OPTIONS"],
  allowedHeaders: ["Content-Type", "x-api-secret"],
}));

app.use(express.json({ limit: "2mb" }));

// Request logger (lightweight, no external dep)
app.use((req, res, next) => {
  const start = Date.now();
  res.on("finish", () => {
    const ms = Date.now() - start;
    const color = res.statusCode >= 400 ? "\x1b[31m" : "\x1b[32m";
    console.log(`${color}${req.method} ${req.path} ${res.statusCode}\x1b[0m (${ms}ms)`);
  });
  next();
});

// ── Routes ────────────────────────────────────────────────────
app.use("/api/queue", queueRoutes);

// ── Serve Frontend Build ──────────────────────────────────────
const frontendDist = path.join(__dirname, "..", "frontend", "dist");
app.use(express.static(frontendDist));

// API info endpoint
app.get("/api/info", (req, res) => {
  res.json({
    name: "SocialForge Backend",
    version: "1.0.0",
    status: "running",
    endpoints: {
      health:    "GET  /api/queue/health",
      list:      "GET  /api/queue",
      add:       "POST /api/queue",
      delete:    "DELETE /api/queue/:id",
      postNow:   "POST /api/queue/:id/post-now",
      testPost:  "POST /api/queue/test-post",
      stats:     "GET  /api/queue/stats",
    },
  });
});

// SPA fallback — serve index.html for all non-API routes
app.get("*", (req, res, next) => {
  if (req.path.startsWith("/api")) return next();
  res.sendFile(path.join(frontendDist, "index.html"));
});

// Global error handler
app.use((err, req, res, next) => {
  console.error("[Error]", err.stack);
  res.status(500).json({ success: false, error: "Internal server error" });
});

// ── Boot ──────────────────────────────────────────────────────
app.listen(PORT, () => {
  console.log(`\n🚀 SocialForge Backend running on port ${PORT}`);
  console.log(`   Health: http://localhost:${PORT}/api/queue/health\n`);

  // Validate required env vars
  const required = [
    "API_SECRET",
  ];
  const optional = [
    "TWITTER_API_KEY", "LINKEDIN_ACCESS_TOKEN",
    "FACEBOOK_PAGE_ACCESS_TOKEN", "INSTAGRAM_ACCESS_TOKEN",
    "WHATSAPP_ACCESS_TOKEN",
  ];

  const missing = required.filter((k) => !process.env[k]);
  if (missing.length) {
    console.warn(`⚠  Missing required env vars: ${missing.join(", ")}`);
  }

  const configured = optional.filter((k) => !!process.env[k]);
  console.log(`✓  Platforms configured: ${configured.length ? configured.map(k => k.split("_")[0].toLowerCase()).join(", ") : "none — add credentials to .env"}`);

  // Start the cron scheduler
  startScheduler();
});

module.exports = app;
