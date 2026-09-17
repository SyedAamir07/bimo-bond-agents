"""
{{cookiecutter.agent_name}}

{{cookiecutter.agent_objective}}
"""
from foundation import AgentContract, BaseAgent, EventEnvelope


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


class {{cookiecutter.agent_name.title().replace(' ', '')}}(BaseAgent):
    objective = "{{cookiecutter.agent_objective}}"
    contract = AgentContract(
        objective=objective,
        inputs=_csv("{{cookiecutter.subscribed_topics}}"),
        outputs=[],  # TODO: event_types this agent publishes
        tools=[],  # TODO: e.g. "backend_http_client", "some_store (durable)"
        permission_scopes=_csv("{{cookiecutter.permission_scopes}}"),
        subscribed_topics=_csv("{{cookiecutter.subscribed_topics}}"),
        published_topics=[],  # TODO: keep in sync with `outputs` above
        failure_cases=[],  # TODO: e.g. "unapproved_x", "x_unavailable"
        owner="{{cookiecutter.author}}",
        acceptance_criteria=[],  # TODO: measurable, e.g. "x_latency_ms tracked"
        automatic_actions=[],  # TODO: what this agent does without human review
        human_review_actions=[],  # TODO: what requires a human before it happens
    )

    def handle_event(self, event: EventEnvelope) -> None:
        # TODO: implement this agent's business logic.
        #
        # A few patterns already proven out by the other agents in this
        # repo -- copy from the closest match rather than reinventing:
        #
        #   - Needs restart-safe state (a session, a ledger, a task)?
        #     See foundation.task_store.build_task_store() and how
        #     live_streaming_agent/session_store.py or
        #     live_auction_agent/ledger_store.py wrap it for a
        #     domain-specific shape.
        #   - Deciding "is this approved / allowed"? Don't hardcode the
        #     answer here -- fetch it from the backend (self.backend,
        #     a BackendClient) the way camera_agent/catalog_client.py
        #     does, and fail CLOSED (reject) if the fetch has never
        #     succeeded, not open (approve everything).
        #   - Every state-changing decision should call self.audit.record(...)
        #     with enough detail to answer "why did this happen" later --
        #     see any agent's _handle_* methods for the shape.
        #   - Track a measurable KPI with self.acceptance.observe(metric, value)
        #     when there's a target for it in foundation/acceptance.py.
        #
        # Deterministic workflow vs. LLM agent: per the project doc
        # (Anthropic, "Building effective agents"), most of the agents in
        # this project are workflows -- explicit rules, not a model
        # decision. Only reach for an LLM call here when the task is
        # genuinely classification/analysis/generation that a rule can't
        # do, and say so in this docstring when you do.
        self.logger.info(
            "Received event_type=%s event_id=%s payload=%s",
            event.event_type,
            event.event_id,
            event.payload,
        )
