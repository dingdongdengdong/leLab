from isaacsim_validation.validation_policy import asset_validator_verdict


def test_validator_policy_blocks_errors_and_misplaced_physics_warnings():
    issues = [
        {"severity": "ERROR", "rule": "CheckRobotRelationships"},
        {"severity": "WARNING", "rule": "VerifyRobotPhysicsSchemaSourceLayer"},
        {"severity": "WARNING", "rule": "ThumbnailExists"},
    ]

    verdict = asset_validator_verdict(issues)

    assert verdict == {
        "passed": False,
        "blocking_issue_count": 2,
        "blocking_rules": ["CheckRobotRelationships", "VerifyRobotPhysicsSchemaSourceLayer"],
    }


def test_validator_policy_allows_thumbnail_warning():
    verdict = asset_validator_verdict([{"severity": "WARNING", "rule": "ThumbnailExists"}])

    assert verdict["passed"] is True
    assert verdict["blocking_issue_count"] == 0
