from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    searxng_base_url: str | None
    firecrawl_base_url: str | None
    firecrawl_api_key: str | None
    typesafe_api_key: str | None
    openai_api_key: str | None
    openai_model: str | None
    slack_webhook_url: str | None

    @classmethod
    def from_environment(cls) -> Settings:
        return cls(
            searxng_base_url=os.getenv("SEARXNG_BASE_URL"),
            firecrawl_base_url=os.getenv("FIRECRAWL_BASE_URL"),
            firecrawl_api_key=os.getenv("FIRECRAWL_API_KEY"),
            typesafe_api_key=os.getenv("TYPESAFE_API_KEY"),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_model=os.getenv("OPENAI_MODEL"),
            slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL"),
        )

    def require_assessment(self, *, synthetic: bool) -> None:
        required = {
            "TYPESAFE_API_KEY": self.typesafe_api_key,
            "OPENAI_API_KEY": self.openai_api_key,
            "OPENAI_MODEL": self.openai_model,
        }
        if not synthetic:
            required.update(
                {
                    "SEARXNG_BASE_URL": self.searxng_base_url,
                    "FIRECRAWL_BASE_URL": self.firecrawl_base_url,
                }
            )
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError("missing configuration: " + ", ".join(missing))

    def require_slack(self) -> None:
        if not self.slack_webhook_url:
            raise ValueError("missing configuration: SLACK_WEBHOOK_URL")
