from __future__ import annotations


def test_superarm_server_html_exposes_source_arm_isaacsim_contract() -> None:
    from lelab import superarm_server

    html = superarm_server.index()

    assert "LeLab SuperArm Isaac Sim Control" in html
    assert "source_arm_isaacsim_arm_only.yaml" in html
    assert "rpo_arm_isaacsim.yaml" not in html
    for joint_name in [
        "joint_rev_1",
        "joint_rev_2",
        "joint_rev_3",
        "joint_rev_4",
        "joint_rev_5",
    ]:
        assert joint_name in html
    # Sliders are generated from this five-entry JavaScript array in the LeLab UI.
    assert html.count("{name:") == 5
    assert "function values()" in html
    assert "send-joint-action" in html


def test_superarm_server_routes_forward_to_teleoperate_handlers(monkeypatch) -> None:
    from lelab import superarm_server
    from lelab.teleoperate import JointActionRequest, TeleoperateRequest

    calls = []

    def fake_start(request):
        calls.append(("start", request.robot_backend, request.follower_config))
        return {"success": True, "robot_backend": request.robot_backend}

    def fake_send(request):
        calls.append(("send", list(request.action)))
        return {"success": True, "sent_action": list(request.action)}

    monkeypatch.setattr(superarm_server, "handle_start_teleoperation", fake_start)
    monkeypatch.setattr(superarm_server, "handle_send_joint_action", fake_send)

    move_result = superarm_server.move_arm(
        TeleoperateRequest(
            robot_backend="isaacsim_rpo_arm",
            leader_port="unused",
            follower_port="unused",
            leader_config="unused",
            follower_config="/workspaces/superarm_ws/isaacsim_test/lerobot/source_arm_isaacsim_arm_only.yaml",
            superarm_ws_path="/workspaces/superarm_ws",
        )
    )
    action_result = superarm_server.send_joint_action(
        JointActionRequest(action=[0.35, 0.0, 0.0, 0.0, 0.0])
    )

    assert move_result == {"success": True, "robot_backend": "isaacsim_rpo_arm"}
    assert action_result == {"success": True, "sent_action": [0.35, 0.0, 0.0, 0.0, 0.0]}
    assert calls == [
        ("start", "isaacsim_rpo_arm", "/workspaces/superarm_ws/isaacsim_test/lerobot/source_arm_isaacsim_arm_only.yaml"),
        ("send", [0.35, 0.0, 0.0, 0.0, 0.0]),
    ]
