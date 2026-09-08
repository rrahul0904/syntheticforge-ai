# Agentic AI Architecture

SyntheticForge 0.5 uses a constrained, persisted agent loop rather than an unrestricted autonomous agent.

The executor takes a user goal, normalizes any AI-proposed plan against an allow-listed tool registry, and forces mandatory safety steps. A normal run models or inspects a system, profiles bounded source samples when a live source exists, classifies sensitive fields, infers business rules, generates synthetic data, validates it, repairs failures up to the configured limit, then persists and packages the validated dataset.

Target database writes are never part of autonomous execution. A completed run enters a pending approval state and the user must explicitly approve a separate writable target configuration before loading.

AI is optional. Without a configured provider the same orchestration runs with deterministic local modeling and repair. With Ollama or an OpenAI-compatible provider, the model can propose plans, model systems, and suggest repairs, but `normalize_plan()` removes unknown tools and restores mandatory classification, validation, packaging, and approval boundaries.

Every plan, tool, observation, validation, repair, result, and error event is persisted in the local state database. Credentials are redacted before persistence.
