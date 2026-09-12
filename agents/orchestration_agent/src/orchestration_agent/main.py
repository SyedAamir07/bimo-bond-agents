"""Entrypoint: `python -m src.orchestration_agent.main`"""
import time

from .agent import OrchestrationAgent
from .config import SETTINGS

TIMEOUT_SWEEP_SECONDS = 5


def main() -> None:
    agent = OrchestrationAgent(SETTINGS)
    agent.run()
    try:
        while True:
            time.sleep(TIMEOUT_SWEEP_SECONDS)
            agent.check_timeouts()
    except KeyboardInterrupt:
        agent.shutdown()


if __name__ == "__main__":
    main()
