#!/usr/bin/env python3
"""Run one real external-provider agent smoke test without printing the API key."""
from __future__ import annotations
import os, tempfile
from pathlib import Path
from app.agent import AgentExecutor
from app.ai import configure_provider_session, clear_provider_session, provider_status
from app.models import AgentRunCreateRequest, AIProviderSessionRequest
from app.persistence import Repository

def main() -> int:
    provider=os.getenv("AI_PROVIDER","openai-compatible")
    model=os.getenv("AI_MODEL")
    base=os.getenv("AI_BASE_URL")
    key=os.getenv("AI_API_KEY")
    if not model or not base:
        print("AI_MODEL and AI_BASE_URL are required")
        return 3
    configure_provider_session(AIProviderSessionRequest(provider=provider,model=model,base_url=base,api_key=key))
    try:
        with tempfile.TemporaryDirectory(prefix="syntheticforge-ai-smoke-") as td:
            repo=Repository(Path(td)/"state.db")
            executor=AgentExecutor(repo)
            req=AgentRunCreateRequest(goal="Create a small hospitality reservation system with guests, rooms, reservations and payments",database_type="postgresql",database_name="ai_smoke",schema_name="public",default_row_count=8,quality_threshold=95,max_repairs=2,ai_planning=True)
            run=executor.create(req); run=executor.execute(run.id,req)
            print({"provider":provider_status(),"state":run.state,"quality":run.quality_score,"trace_events":len(run.trace),"artifact":bool(run.artifact_path)})
            return 0 if run.state=="completed" and (run.quality_score or 0)>=95 else 2
    finally:
        clear_provider_session()

if __name__ == "__main__":
    raise SystemExit(main())
