"""Thin actor entrypoint that registers LeLab's environment before LeRobot."""

from __future__ import annotations

from .env import register_superarm_isaac_env


def main() -> None:
    register_superarm_isaac_env()
    from lerobot.rl.actor import actor_cli

    actor_cli()


if __name__ == "__main__":
    main()
