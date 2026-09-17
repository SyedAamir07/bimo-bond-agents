"""Entrypoint: `python -m src.orchestration_agent.main`"""
import threading
import time

from .agent import OrchestrationAgent
from .config import SETTINGS

TIMEOUT_SWEEP_SECONDS = 5


def _run_timeout_sweeper(agent: OrchestrationAgent, stop_event: threading.Event) -> None:
    """
    Background loop calling check_timeouts() on a fixed cadence.

    Runs as a daemon thread instead of blocking main() so shutdown()
    (SIGTERM/SIGINT) isn't delayed behind a long sleep, and so this is
    unit-testable by driving `stop_event` directly instead of relying
    on wall-clock sleeps.
    """
    while not stop_event.wait(TIMEOUT_SWEEP_SECONDS):
        try:
            agent.check_timeouts()
        except Exception:  # noqa: BLE001 - a sweep failure must not kill the loop
            agent.logger.exception("Timeout sweep failed")


def main() -> None:
    agent = OrchestrationAgent(SETTINGS)
    agent.run()

    stop_event = threading.Event()
    sweeper = threading.Thread(
        target=_run_timeout_sweeper,
        args=(agent, stop_event),
        name="orchestration-timeout-sweeper",
        daemon=True,
    )
    sweeper.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        sweeper.join(timeout=TIMEOUT_SWEEP_SECONDS + 1)
        agent.shutdown()


if __name__ == "__main__":
    main()
