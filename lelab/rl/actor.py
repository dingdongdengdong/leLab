"""Thin actor entrypoint that registers LeLab's environment before LeRobot."""

from __future__ import annotations

from .env import register_superarm_isaac_env


def main() -> None:
    register_superarm_isaac_env()
    import gymnasium as gym

    from lerobot.rl import actor

    original_make_robot_env = actor.make_robot_env

    def make_robot_env(cfg):
        if cfg.name == "gym_hil" and cfg.task == "SuperArmIsaacPickLift-v0":
            return (
                gym.make(
                    "gym_hil/SuperArmIsaacPickLift-v0",
                    image_obs=True,
                    render_mode="human",
                    use_gripper=True,
                    gripper_penalty=-0.02,
                ),
                None,
            )
        return original_make_robot_env(cfg)

    # LeRobot's actor remains byte-for-byte upstream; this process-local hook
    # replaces only its MuJoCo-specific environment factory for our task.
    actor.make_robot_env = make_robot_env

    actor.actor_cli()


if __name__ == "__main__":
    main()
