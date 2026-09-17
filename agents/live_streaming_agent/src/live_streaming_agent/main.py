"""Entrypoint: `python -m src.live_streaming_agent.main`"""
import threading
import time

from .agent import LiveStreamingAgent
from .config import SETTINGS, STALE_SWEEP_INTERVAL_SECONDS


def _run_stale_sweeper(agent: LiveStreamingAgent, stop_event: threading.Event) -> None:
    """Background loop calling check_stale_sessions() on a fixed cadence.
    Runs as a daemon thread so shutdown() isn't delayed behind a sleep."""
    while not stop_event.wait(STALE_SWEEP_INTERVAL_SECONDS):
        try:
            agent.check_stale_sessions()
        except Exception:  # noqa: BLE001 - a sweep failure must not kill the loop
            agent.logger.exception("Stale-session sweep failed")


def main() -> None:
    agent = LiveStreamingAgent(SETTINGS)
    agent.run()

    stop_event = threading.Event()
    sweeper = threading.Thread(
        target=_run_stale_sweeper,
        args=(agent, stop_event),
        name="live-streaming-stale-sweeper",
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
        sweeper.join(timeout=STALE_SWEEP_INTERVAL_SECONDS + 1)
        agent.shutdown()


if __name__ == "__main__":
    main()
