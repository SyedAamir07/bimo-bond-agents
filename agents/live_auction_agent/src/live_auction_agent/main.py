"""Entrypoint: `python -m src.live_auction_agent.main`"""
import time

from .agent import LiveAuctionAgent
from .config import SETTINGS


def main() -> None:
    agent = LiveAuctionAgent(SETTINGS)
    agent.run()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        agent.shutdown()


if __name__ == "__main__":
    main()
