#!/usr/bin/env python3
"""
generate_agent.py

Expands cookiecutter-agent/ into a real agent folder, substituting the
{{cookiecutter.*}} placeholders. Functionally equivalent to running:

    cookiecutter cookiecutter-agent/

but implemented directly with Jinja2 so it works without installing the
`cookiecutter` CLI (useful in offline/restricted environments). Once you
have network access, prefer the real `cookiecutter` CLI for new agents --
this script exists to unblock generating the sample agents right now.

Usage:
    python generate_agent.py \
        --agent-name "Camera Agent" \
        --agent-objective "Manage camera functions and image quality." \
        --subscribed-topics "camera.settings.changed" \
        --permission-scopes "device.camera.read,device.camera.write" \
        --output-dir .
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from jinja2 import Template

TEMPLATE_ROOT = Path(__file__).parent / "cookiecutter-agent" / "{{cookiecutter.agent_slug}}"


def slugify(name: str) -> str:
    return name.lower().replace(" ", "_")


def class_name(name: str) -> str:
    return name.title().replace(" ", "")


def render_context(agent_name: str, agent_objective: str, subscribed_topics: str, permission_scopes: str, author: str) -> dict:
    return {
        "cookiecutter": {
            "agent_name": agent_name,
            "agent_slug": slugify(agent_name),
            "agent_objective": agent_objective,
            "subscribed_topics": subscribed_topics,
            "permission_scopes": permission_scopes,
            "author": author,
        }
    }


def render_string(text: str, context: dict) -> str:
    # Support the two expressions the templates actually use, in addition
    # to plain {{cookiecutter.field}} substitution.
    ctx = context["cookiecutter"]
    text = text.replace(
        "{{cookiecutter.agent_name.title().replace(' ', '')}}",
        class_name(ctx["agent_name"]),
    )
    return Template(text).render(context)


def generate(agent_name: str, agent_objective: str, subscribed_topics: str, permission_scopes: str, output_dir: Path, author: str = "Bimo Bond Engineering") -> Path:
    context = render_context(agent_name, agent_objective, subscribed_topics, permission_scopes, author)
    slug = slugify(agent_name)
    dest_root = output_dir / slug

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
        rendered = render_string(raw, context)
        dest_path.write_text(rendered, encoding="utf-8")

    print(f"Generated agent '{agent_name}' -> {dest_root}")
    return dest_root


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a new agent from the cookiecutter-agent template.")
    parser.add_argument("--agent-name", required=True)
    parser.add_argument("--agent-objective", required=True)
    parser.add_argument("--subscribed-topics", default="")
    parser.add_argument("--permission-scopes", default="")
    parser.add_argument("--author", default="Bimo Bond Engineering")
    parser.add_argument("--output-dir", default=".", type=Path)
    args = parser.parse_args()

    generate(
        agent_name=args.agent_name,
        agent_objective=args.agent_objective,
        subscribed_topics=args.subscribed_topics,
        permission_scopes=args.permission_scopes,
        output_dir=args.output_dir,
        author=args.author,
    )
