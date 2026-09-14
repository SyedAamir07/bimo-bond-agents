"""Entrypoint: `python -m src.gift_effects_agent.main`"""
import time

from .agent import GiftEffectsAgent
from .config import SETTINGS


def main() -> None:
    agent = GiftEffectsAgent(SETTINGS)
    agent.run()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        agent.shutdown()


if __name__ == "__main__":
    main()
