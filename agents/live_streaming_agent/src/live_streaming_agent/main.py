"""Entrypoint: `python -m src.live_streaming_agent.main`"""
import time

from .agent import LiveStreamingAgent
from .config import SETTINGS


def main() -> None:
    agent = LiveStreamingAgent(SETTINGS)
    agent.run()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        agent.shutdown()


if __name__ == "__main__":
    main()