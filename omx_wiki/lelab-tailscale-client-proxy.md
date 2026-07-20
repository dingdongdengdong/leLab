---
title: "LeLab Tailscale Client Proxy"
tags: ["lelab", "tailscale", "network", "superarm", "amazinghand", "operations"]
created: 2026-07-20T02:11:54.885Z
updated: 2026-07-20T02:11:54.885Z
sources: []
links: []
category: environment
confidence: medium
schemaVersion: 1
---

# LeLab Tailscale Client Proxy

## Client endpoint
Use `http://100.96.41.100:8000/` from a device connected to the same Tailscale tailnet. The explicit `:8000` is required because the LeLab application does not bind privileged port 80.

## Routing boundary
LeLab remains bound to `127.0.0.1:8000`. A dedicated user service binds only `100.96.41.100:8000` (the Tailscale interface) and forwards raw TCP to local LeLab. It does not listen on the public/LAN `0.0.0.0` interface.

## Persistent service
- Script: `/home/dong/.local/bin/lelab-tailnet-proxy.py`
- Unit: `/home/dong/.config/systemd/user/lelab-tailnet-proxy.service`
- Unit state: enabled and active; user linger is enabled so it survives logout/reboot.
- Inspect: `systemctl --user status lelab-tailnet-proxy.service`
- Logs: `journalctl --user -u lelab-tailnet-proxy.service`

## Verification
- `http://100.96.41.100:8000/health` returns LeLab healthy.
- Browser deep link returns HTTP 200: `/teleoperation?robot=SuperArm%20%2B%20AmazingHand`.
- `/ws/superarm` WebSocket handshakes through the proxy.
- SuperArm record endpoint returns the 13 physical joints; exact hand manifest remains available (33 bodies, 162 visuals, 23 meshes, 20 equalities).

## Runtime note
When normal teleoperation is stopped, the webpage shows `Waiting for Robot Data`; start teleoperation from the normal LeLab robot selector to stream live joints.
