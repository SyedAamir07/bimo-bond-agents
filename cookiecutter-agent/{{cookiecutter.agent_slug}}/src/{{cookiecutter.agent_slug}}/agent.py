"""
{{cookiecutter.agent_name}}

{{cookiecutter.agent_objective}}
"""
from foundation import AgentContract, BaseAgent, EventEnvelope


class {{cookiecutter.agent_name.title().replace(' ', '')}}(BaseAgent):
    objective = "{{cookiecutter.agent_objective}}"
    contract = AgentContract(
        objective=objective,
        inputs=[t for t in "{{cookiecutter.subscribed_topics}}".split(",") if t],
        outputs=[],
        tools=[],
        permission_scopes=[s for s in "{{cookiecutter.permission_scopes}}".split(",") if s],
        subscribed_topics=[t for t in "{{cookiecutter.subscribed_topics}}".split(",") if t],
        published_topics=[],
        failure_cases=[],
        owner="{{cookiecutter.author}}",
        acceptance_criteria=[],
        automatic_actions=[],
        human_review_actions=[],
    )

    def handle_event(self, event: EventEnvelope) -> None:
        # TODO: implement this agent's business logic.
        self.logger.info("Received event_type=%s payload=%s", event.event_type, event.payload)
