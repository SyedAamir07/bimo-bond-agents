"""Entrypoint: `python -m src.camera_agent.main`"""
import time

from .agent import CameraAgent
from .config import SETTINGS


def main() -> None:
    agent = CameraAgent(SETTINGS)
    agent.run()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        agent.shutdown()


if __name__ == "__main__":
    main()