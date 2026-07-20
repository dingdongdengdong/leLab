---
title: "SuperArm website real-hardware readiness page"
tags: ["superarm", "website", "hardware", "lerobot", "safety"]
created: 2026-07-20T03:35:49.492Z
updated: 2026-07-20T03:35:49.492Z
sources: []
links: []
category: decision
confidence: medium
schemaVersion: 1
---

# SuperArm website real-hardware readiness page

The website now has `/hardware-setup`, a read-only preparation page for the separate DM4340P CAN arm and AmazingHandControl SCS0009 serial hand. It requests `/api/superarm/hardware-readiness` to show package/serial-port detection and the ordered bench checklist. The page deliberately has no connect, calibrate, torque, or motion action. It links from the SuperArm dashboard and landing diagnostic card. The current `/superarm` page still supports only MuJoCo and MuJoCo-plus-serial-hand hybrid mode.

The page also surfaces the invalid-by-default configuration template and requires actual CAN IDs, direction/zero offsets, limits, and gains. A hardware action failure now disconnects both the CAN arm and serial hand rather than leaving a partial combined action active.
