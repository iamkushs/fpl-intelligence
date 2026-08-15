"""Application-owned Codex execution service."""

import logging

from fpl_intelligence.codex.client import (
    CodexAppServerClient,
    CodexExecutionConfig,
    CodexExecutionResult,
)
from fpl_intelligence.config import Settings, get_settings

logger = logging.getLogger(__name__)


class CodexService:
    """The only application boundary for generative AI execution."""

    def __init__(self, client: CodexAppServerClient | None = None, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.client = client or CodexAppServerClient(self.settings)

    def execute(
        self,
        *,
        prompt: str,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> CodexExecutionResult:
        config = CodexExecutionConfig(
            model=model if model is not None else self.settings.codex_default_model,
            reasoning_effort=(
                reasoning_effort
                if reasoning_effort is not None
                else self.settings.codex_default_reasoning_effort
            ),
        )
        logger.info(
            "codex_execution_start model=%s reasoning_effort=%s",
            config.model,
            config.reasoning_effort,
        )
        result = self.client.execute(prompt, config)
        logger.info(
            "codex_execution_complete thread_id=%s turn_id=%s model=%s reasoning_effort=%s",
            result.thread_id,
            result.turn_id,
            result.model,
            result.reasoning_effort,
        )
        return result
