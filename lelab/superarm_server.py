"""Lightweight LeLab control server for the SuperArm Isaac Sim backend.

This avoids importing the full LeLab app stack (record/train/calibrate) when the
runtime only needs direct Isaac Sim joint control via the patched teleoperate
backend.  It is intentionally Python 3.10 compatible so it can share ROS2
Humble's rclpy runtime.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .teleoperate import (
    JointActionRequest,
    TeleoperateRequest,
    handle_get_joint_positions,
    handle_send_joint_action,
    handle_start_teleoperation,
    handle_stop_teleoperation,
    handle_teleoperation_status,
)

app = FastAPI(title="LeLab SuperArm Isaac Sim Control")

CONTROL_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>LeLab SuperArm Isaac Sim Control</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; max-width: 960px; }
    .row { display: grid; grid-template-columns: 120px 1fr 90px; gap: 1rem; align-items: center; margin: .7rem 0; }
    input[type=range] { width: 100%; }
    button { padding: .6rem 1rem; margin: .3rem; }
    pre { background: #111; color: #eee; padding: 1rem; overflow: auto; }
  </style>
</head>
<body>
  <h1>LeLab SuperArm Isaac Sim Control</h1>
  <p>Backend: <code>isaacsim_rpo_arm</code>; focused source-arm follower: <code>joint_rev_1..5</code></p>
  <div>
    <button onclick="connectArm()">Connect Isaac Sim Arm</button>
    <button onclick="sendAction()">Send Slider Action</button>
    <button onclick="preset([0,0,0,0,0])">Home</button>
    <button onclick="preset([0.25,-0.2,0.3,-0.35,0.2])">Arm Pose A</button>
    <button onclick="preset([-0.25,0.2,-0.3,0.35,-0.2])">Arm Pose B</button>
    <button onclick="preset([0.4,0.1,0.15,-0.45,0.3])">Mixed Elbow</button>
    <button onclick="stopArm()">Stop</button>
  </div>
  <div id="sliders"></div>
  <h2>Status</h2>
  <pre id="out">Not connected</pre>
<script>
const joints = [
  {name: "joint_rev_1", min: -1.57, max: 1.57, step: 0.01, value: 0},
  {name: "joint_rev_2", min: -1.57, max: 1.57, step: 0.01, value: 0},
  {name: "joint_rev_3", min: -1.57, max: 1.57, step: 0.01, value: 0},
  {name: "joint_rev_4", min: -1.57, max: 1.57, step: 0.01, value: 0},
  {name: "joint_rev_5", min: -1.57, max: 1.57, step: 0.01, value: 0},
];
const names = joints.map(j => j.name);
const sliders = document.getElementById("sliders");
for (let i = 0; i < joints.length; i++) {
  const joint = joints[i];
  const row = document.createElement("div"); row.className = "row";
  row.innerHTML = `<label>${joint.name}</label><input id="j${i}" type="range" min="${joint.min}" max="${joint.max}" step="${joint.step}" value="${joint.value}" oninput="v${i}.textContent=this.value"><span id="v${i}">${joint.value}</span>`;
  sliders.appendChild(row);
}
function values(){ return names.map((_, i) => parseFloat(document.getElementById(`j${i}`).value)); }
function log(obj){ document.getElementById("out").textContent = JSON.stringify(obj, null, 2); }
async function post(path, body){ const r = await fetch(path, {method:"POST", headers:{"content-type":"application/json"}, body: JSON.stringify(body)}); const j = await r.json(); log(j); return j; }
async function connectArm(){
  return post('/move-arm', {
    robot_backend: 'isaacsim_rpo_arm',
    leader_port: 'unused', follower_port: 'unused', leader_config: 'unused',
    follower_config: '/workspaces/superarm_ws/isaacsim_test/lerobot/source_arm_isaacsim_arm_only.yaml',
    isaacsim_config: '/workspaces/superarm_ws/isaacsim_test/lerobot/source_arm_isaacsim_arm_only.yaml',
    superarm_ws_path: '/workspaces/superarm_ws'
  });
}
async function sendAction(){ return post('/send-joint-action', {action: values()}); }
async function preset(vals){ vals.forEach((v,i)=>{document.getElementById(`j${i}`).value=v; document.getElementById(`v${i}`).textContent=v;}); return post('/send-joint-action', {action: vals}); }
async function stopArm(){ return post('/stop-teleoperation', {}); }
async function poll(){ try { const r = await fetch('/joint-positions'); log(await r.json()); } catch(e){} }
setInterval(poll, 2000);
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return CONTROL_HTML


@app.post("/move-arm")
def move_arm(request: TeleoperateRequest):
    return handle_start_teleoperation(request)


@app.post("/send-joint-action")
def send_joint_action(request: JointActionRequest):
    return handle_send_joint_action(request)


@app.post("/stop-teleoperation")
def stop_teleoperation():
    return handle_stop_teleoperation()


@app.get("/teleoperation-status")
def teleoperation_status():
    return handle_teleoperation_status()


@app.get("/joint-positions")
def joint_positions():
    return handle_get_joint_positions()
