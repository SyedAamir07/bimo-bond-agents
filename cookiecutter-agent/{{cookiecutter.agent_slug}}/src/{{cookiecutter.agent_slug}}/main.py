"""Entrypoint: `python -m src.{{cookiecutter.agent_slug}}.main`"""
import time

from .agent import {{cookiecutter.agent_name.title().replace(' ', '')}}
from .config import SETTINGS


def main() -> None:
    agent = {{cookiecutter.agent_name.title().replace(' ', '')}}(SETTINGS)
    agent.run()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        agent.shutdown()


if __name__ == "__main__":
    main()
