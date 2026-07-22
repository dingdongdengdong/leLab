from isaacsim_validation.import_config import urdf_import_settings


def test_vla_asset_import_uses_simready_layer_transformer():
    settings = urdf_import_settings()

    assert settings["run_asset_transformer"] is True
    assert settings["run_multi_physics_conversion"] is True
    assert settings["fix_base"] is True
    assert settings["joint_target_type"] == "position"


def test_vla_asset_import_keeps_explicit_position_drive_gains():
    settings = urdf_import_settings()

    assert settings["joint_drive_type"] == "force"
    assert settings["override_joint_stiffness"] == 180.0
    assert settings["override_joint_damping"] == 18.0
