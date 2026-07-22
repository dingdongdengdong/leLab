---
title: "LeLab Isaac Sim Control Scope"
tags: ["lelab", "isaac-sim", "superarm", "scope"]
created: 2026-07-22T10:02:05.261Z
updated: 2026-07-22T11:39:20.000Z
sources: []
links: ["lelab-controlled-superarm-in-isaac-sim-6-0.md", "superarm-urdf-validation-in-isaac-sim-6-0.md"]
category: decision
confidence: high
schemaVersion: 1
---

# LeLab Isaac Sim Control Scope

2026-07-22 decision: this branch does not integrate the external
AmazingHandControl GitHub package. The active target is the LeLab SuperArm Isaac
Sim backend: six logical controls (five arm joints plus one grasp motion) mapped
to thirteen physical Isaac joints. Website changes expose Isaac Sim sessions
and measured 13-joint telemetry while keeping MuJoCo MJCF visuals MuJoCo-only;
real hardware and external hand protocols are deferred unless explicitly
re-enabled.

The website displays the source URDF with its hand visuals preserved and applies
measured 5+8 joint telemetry. MuJoCo keeps its stripped-hand URDF plus exact
MJCF overlay and continuous video. Live Isaac viewport capture is disabled
because every tested headless render path either produced no usable frame or
failed to terminate within its deadline while the control stage was active.
The runtime advertises `supports_capture=false`, and the website explains that
static Isaac images are a separate evidence category.

The earlier on-demand-capture design remains covered by generic API safety tests
but is not advertised by the Isaac Sim 6.0 runtime. A future renderer may
re-enable it only after a live frame is reviewed and the operation reliably
returns within a bounded control-safe deadline.

See [[lelab-controlled-superarm-in-isaac-sim-6-0]] and
[[superarm-urdf-validation-in-isaac-sim-6-0]] for exact results and proof
boundaries.
