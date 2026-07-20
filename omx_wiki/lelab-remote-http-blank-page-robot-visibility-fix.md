---
title: "LeLab Remote HTTP Blank Page Robot Visibility Fix"
tags: ["lelab", "frontend", "remote-access", "superarm", "amazinghand", "debugging"]
created: 2026-07-20T00:49:32.242Z
updated: 2026-07-20T00:49:32.242Z
sources: []
links: []
category: debugging
confidence: medium
schemaVersion: 1
---

# LeLab Remote HTTP Blank Page Robot Visibility Fix

## Symptom
Opening LeLab from a non-localhost HTTP address, including the direct SuperArm teleoperation route, rendered a blank page, so the robot could not be seen. Localhost continued to work.

## Root cause
The non-localhost HTTP origin is not a secure browser context. `SingleTabGuard` called `crypto.randomUUID()` unconditionally; Firefox exposes that method on localhost but not on the remote HTTP origin. The thrown effect error unmounted the React tree. Separately, `ApiContext` defaulted API and WebSocket traffic to `http://localhost:8000`, which means the viewer computer rather than the LeLab host during remote access.

## Fix
- Generate tab IDs with `randomUUID` when available, `getRandomValues` otherwise, and a final non-cryptographic uniqueness fallback.
- Use the page origin for API/WebSocket calls when the UI is served remotely or directly by port 8000. Preserve `http://localhost:8000` for the separate localhost Vite development server.
- Ignore a stored loopback API URL when the current page is remote.

## Fresh verification
- Frontend: 17 tests passed, TypeScript passed, changed-file ESLint had no errors.
- Browser at the remote HTTP route: React root length 3881; URDF viewer present; robot loaded with 28 links and 27 joints; exact MuJoCo hand overlay visible with 33 body nodes; 23 exact STL assets and the manifest loaded; zero localhost API requests.
- Visual evidence: `omx_wiki/assets/lelab-remote-http-robot-visible.png`.

## Runtime note
The robot is visible. The current status badge still reports `0/13 physical joints live` and `Runtime/URDF joint mismatch`; that is a separate live-joint mapping/state issue, not the blank-page renderer failure.
