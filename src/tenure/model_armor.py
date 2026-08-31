"""Model Armor prompt boundary using the official regional REST surface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ArmorVerdict:
    allowed: bool
    filter_match_state: str
    invocation_result: str
    filter_results: dict[str, Any]


class ModelArmorGateway:
    """Screen only the newest user prompt before it can reach an LLM."""

    def __init__(
        self,
        project_id: str,
        location: str,
        template_id: str,
        session: Any | None = None,
    ) -> None:
        if not template_id:
            raise ValueError("a Model Armor template ID is required")
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            session = AuthorizedSession(credentials)
        self.session = session
        self.url = (
            f"https://modelarmor.{location}.rep.googleapis.com/v1/projects/"
            f"{project_id}/locations/{location}/templates/{template_id}:sanitizeUserPrompt"
        )

    def sanitize_user_prompt(self, latest_user_input: str) -> ArmorVerdict:
        """Send only the latest user input; never system prompts or conversation history."""
        response = self.session.post(
            self.url,
            json={"userPromptData": {"text": latest_user_input}},
            timeout=30,
        )
        response.raise_for_status()
        result = response.json().get("sanitizationResult", {})
        match_state = str(result.get("filterMatchState", "UNKNOWN"))
        invocation = str(result.get("invocationResult", "FAILURE"))
        allowed = match_state == "NO_MATCH_FOUND" and invocation == "SUCCESS"
        return ArmorVerdict(
            allowed=allowed,
            filter_match_state=match_state,
            invocation_result=invocation,
            filter_results=dict(result.get("filterResults", {})),
        )
