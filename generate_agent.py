#!/usr/bin/env python3
"""
generate_agent.py

Expands cookiecutter-agent/ into a real agent folder under agents/,
substituting the {{cookiecutter.*}} placeholders.

No third-party deps required (plain string substitution). Equivalent to:

    cookiecutter cookiecutter-agent/

Usage:
    python generate_agent.py \
        --agent-name "Camera Agent" \
        --agent-objective "Manage camera functions and image quality." \
        --subscribed-topics "camera.settings.changed" \
        --permission-scopes "device.camera.read,device.camera.write"
"""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).parent
TEMPLATE_ROOT = REPO_ROOT / "cookiecutter-agent" / "{{cookiecutter.agent_slug}}"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "agents"
PYTEST_INI = REPO_ROOT / "pytest.ini"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
COMPOSE_PORT_START = 8081


def slugify(name: str) -> str:
    return name.lower().replace(" ", "_")


def class_name(name: str) -> str:
    return name.title().replace(" ", "")


def build_replacements(
    agent_name: str,
    agent_objective: str,
    subscribed_topics: str,
    permission_scopes: str,
    author: str,
) -> list[tuple[str, str]]:
    slug = slugify(agent_name)
    # Longer / more specific tokens first so partial replaces stay correct.
    return [
        (
            "{{cookiecutter.agent_name.title().replace(' ', '')}}",
            class_name(agent_name),
        ),
        ("{{cookiecutter.agent_name}}", agent_name),
        ("{{cookiecutter.agent_slug}}", slug),
        ("{{cookiecutter.agent_objective}}", agent_objective),
        ("{{cookiecutter.subscribed_topics}}", subscribed_topics),
        ("{{cookiecutter.permission_scopes}}", permission_scopes),
        ("{{cookiecutter.author}}", author),
    ]


def render_string(text: str, replacements: list[tuple[str, str]]) -> str:
    for old, new in replacements:
        text = text.replace(old, new)
    leftover = re.findall(r"\{\{\s*cookiecutter\.[^}]+\}\}", text)
    if leftover:
        raise ValueError(f"Unresolved cookiecutter placeholders: {leftover}")
    return text


def _prune_missing_agent_paths(text: str) -> tuple[str, bool]:
    """Drop pytest.ini lines that point at agents not present on disk."""
    changed = False
    out_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        m = re.match(r"agents/([a-z0-9_]+)/(?:src|tests)$", stripped)
        if m:
            slug = m.group(1)
            agent_dir = DEFAULT_OUTPUT_DIR / slug
            if not agent_dir.is_dir():
                changed = True
                continue
        out_lines.append(line)
    return "".join(out_lines), changed


def _ensure_pytest_ini(slug: str) -> None:
    """Wire the new agent into the root pytest.ini pythonpath + testpaths."""
    if not PYTEST_INI.exists():
        print(f"Warning: {PYTEST_INI} missing — skip pytest wiring")
        return

    text = PYTEST_INI.read_text(encoding="utf-8")
    text, pruned = _prune_missing_agent_paths(text)
    src_line = f"    agents/{slug}/src"
    tests_line = f"    agents/{slug}/tests"
    changed = pruned

    if src_line not in text:
        # Insert under pythonpath= block (before testpaths=).
        if re.search(r"(?m)^testpaths\s*=", text):
            text = re.sub(
                r"(?m)^(testpaths\s*=)",
                src_line + "\n\\1",
                text,
                count=1,
            )
            changed = True
        else:
            text += f"\npythonpath =\n{src_line}\n"
            changed = True

    if tests_line not in text:
        if not text.endswith("\n"):
            text += "\n"
        text += tests_line + "\n"
        changed = True

    if changed:
        PYTEST_INI.write_text(text, encoding="utf-8")
        print(f"Updated {PYTEST_INI.name} for agents/{slug}")


def _next_compose_host_port(compose_text: str) -> int:
    ports = [int(p) for p in re.findall(r'"(\d+):8080"', compose_text)]
    if not ports:
        return COMPOSE_PORT_START
    return max(ports) + 1


def _compose_service_block(slug: str, host_port: int) -> str:
    # HEALTH_PORT is force-set to 8080 (container-internal) even though
    # each agent's .env.example sets a different HEALTH_PORT for local
    # (non-Docker) runs, where multiple agents share one host and need
    # distinct ports. Inside Compose each agent has its own container,
    # so 8080 internally + a distinct host port (host_port:8080 below)
    # is correct -- without this override, the env_file's local-dev
    # HEALTH_PORT would win and the port mapping below would point at
    # nothing (see docker-compose.yml's other services for the same pattern).
    return (
        f"\n"
        f"  {slug}:\n"
        f"    build:\n"
        f"      context: .\n"
        f"      dockerfile: agents/{slug}/Dockerfile\n"
        f"    env_file: agents/{slug}/.env.example\n"
        f"    environment:\n"
        f"      EVENT_BUS_URL: redis://redis:6379/0\n"
        f"      AGENT_EVENTS_STREAM: agent:events\n"
        f'      HEALTH_PORT: "8080"\n'
        f"    ports:\n"
        f'      - "{host_port}:8080"\n'
        f"    depends_on:\n"
        f"      redis:\n"
        f"        condition: service_healthy\n"
    )


def _ensure_compose(slug: str) -> None:
    """Append a Compose service for the new agent if missing."""
    if not COMPOSE_FILE.exists():
        print(f"Warning: {COMPOSE_FILE} missing — skip Compose wiring")
        return

    text = COMPOSE_FILE.read_text(encoding="utf-8")
    # Match service key at indent 2: "  slug:"
    if re.search(rf"(?m)^  {re.escape(slug)}:\s*$", text):
        print(f"Compose already has service '{slug}' — skip")
        return

    host_port = _next_compose_host_port(text)
    block = _compose_service_block(slug, host_port)
    if not text.endswith("\n"):
        text += "\n"
    text += block
    COMPOSE_FILE.write_text(text, encoding="utf-8")
    print(f"Wired Compose service '{slug}' on host port {host_port}")


def generate(
    agent_name: str,
    agent_objective: str,
    subscribed_topics: str,
    permission_scopes: str,
    output_dir: Path,
    author: str = "Bimo Bond Engineering",
    *,
    wire_pytest: bool = True,
    wire_compose: bool = True,
) -> Path:
    replacements = build_replacements(
        agent_name, agent_objective, subscribed_topics, permission_scopes, author
    )
    slug = slugify(agent_name)
    dest_root = output_dir / slug

    if not TEMPLATE_ROOT.is_dir():
        raise FileNotFoundError(f"Template missing: {TEMPLATE_ROOT}")

    if dest_root.exists():
        shutil.rmtree(dest_root)

    for src_path in TEMPLATE_ROOT.rglob("*"):
        rel = src_path.relative_to(TEMPLATE_ROOT)
        rel_str = str(rel).replace("{{cookiecutter.agent_slug}}", slug)
        dest_path = dest_root / rel_str

        if src_path.is_dir():
            dest_path.mkdir(parents=True, exist_ok=True)
            continue

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        raw = src_path.read_text(encoding="utf-8")
        rendered = render_string(raw, replacements)
        dest_path.write_text(rendered, encoding="utf-8")

    init_py = dest_root / "src" / slug / "__init__.py"
    init_py.parent.mkdir(parents=True, exist_ok=True)
    if not init_py.exists():
        init_py.write_text("", encoding="utf-8")

    in_default_agents = output_dir.resolve() == DEFAULT_OUTPUT_DIR.resolve()
    if wire_pytest and in_default_agents:
        _ensure_pytest_ini(slug)
    if wire_compose and in_default_agents:
        _ensure_compose(slug)

    print(f"Generated agent '{agent_name}' -> {dest_root}")
    print(
        "Next: implement handle_event() and fill contract TODOs. "
        "Compose + pytest are wired automatically when targeting ./agents."
    )
    return dest_root


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate a new agent from the cookiecutter-agent template."
    )
    parser.add_argument("--agent-name", required=True)
    parser.add_argument("--agent-objective", required=True)
    parser.add_argument("--subscribed-topics", default="")
    parser.add_argument("--permission-scopes", default="")
    parser.add_argument("--author", default="Bimo Bond Engineering")
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        type=Path,
        help="Where to place the new agent folder (default: ./agents)",
    )
    parser.add_argument(
        "--no-pytest-wire",
        action="store_true",
        help="Do not update root pytest.ini",
    )
    parser.add_argument(
        "--no-compose-wire",
        action="store_true",
        help="Do not update docker-compose.yml",
    )
    args = parser.parse_args()

    output_dir = args.output_dir
    if not output_dir.is_absolute():
        output_dir = (Path.cwd() / output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    generate(
        agent_name=args.agent_name,
        agent_objective=args.agent_objective,
        subscribed_topics=args.subscribed_topics,
        permission_scopes=args.permission_scopes,
        output_dir=output_dir,
        author=args.author,
        wire_pytest=not args.no_pytest_wire,
        wire_compose=not args.no_compose_wire,
    )
