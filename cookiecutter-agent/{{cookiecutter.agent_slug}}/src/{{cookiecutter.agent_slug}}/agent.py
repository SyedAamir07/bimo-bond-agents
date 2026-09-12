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
        outputs=[],
        tools=[],
        permission_scopes=_csv("{{cookiecutter.permission_scopes}}"),
        subscribed_topics=_csv("{{cookiecutter.subscribed_topics}}"),
        published_topics=[],
        failure_cases=[],
        owner="{{cookiecutter.author}}",
        acceptance_criteria=[],
        automatic_actions=[],
        human_review_actions=[],
    )

    def handle_event(self, event: EventEnvelope) -> None:
        # TODO: implement this agent's business logic.
        self.logger.info(
            "Received event_type=%s event_id=%s payload=%s",
            event.event_type,
            event.event_id,
            event.payload,
        )
