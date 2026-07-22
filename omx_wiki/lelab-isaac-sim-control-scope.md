---
title: "LeLab Isaac Sim Control Scope"
tags: ["lelab", "isaac-sim", "superarm", "scope"]
created: 2026-07-22T10:02:05.261Z
updated: 2026-07-22T10:15:57.000Z
sources: []
links: []
category: decision
confidence: medium
schemaVersion: 1
---

# LeLab Isaac Sim Control Scope

2026-07-22 decision: current branch does not integrate the external AmazingHandControl GitHub package. The active target is the LeLab SuperArm Isaac Sim backend: six logical controls (five arm joints plus one grasp motion) mapped to thirteen physical Isaac joints. Website changes should expose Isaac Sim sessions/captures and keep MuJoCo MJCF visuals MuJoCo-only; real hardware/external hand protocol work is deferred unless explicitly re-enabled.

The website therefore uses explicit on-demand Isaac whole/hand captures rather
than labeling Isaac as live video. Isaac displays the source URDF with its hand
visuals preserved and applies measured 5+8 joint telemetry; MuJoCo keeps its
stripped-hand URDF plus exact MJCF overlay. Capture bytes are revalidated by
file identity and SHA-256 before response. See
[[superarm-urdf-validation-in-isaac-sim-6-0]] for the evidence boundary and
remaining live-runtime acceptance gate.

Capture metadata is current-session state: every session transition clears it,
increments the browser image key, and uses `Cache-Control: no-store`. A prior
Isaac session's frame must never be relabeled as the latest frame of a new one.
