# Agent configuration used for this task

Integration owner: architecture, API contract, meaningful Git commits, runtime preparation, integration and final acceptance.
Backend agent: backend/; FastAPI, SQLAlchemy PostgreSQL, private S3, access control, database-backed ffmpeg worker.
Frontend agent: frontend/; same-origin vanilla HTML/CSS/JS YouTube-style responsive UI and real API integration.
Infrastructure agent: compose.yaml, scripts/, README.md, docs/; clean Ubuntu bootstrap, generated media seed, packaging.
Test agent: tests/; critical behavior and security regression tests against real application.
Screenshot review agent: model gpt-6-luna (explicit user choice); inspect rendered desktop/mobile screenshots and send actionable findings.

Agents receive root AGENTS.md and docs/contract.md. Directory ownership prevents overlapping edits. Integration owner reviews and commits; agents do not commit concurrently. Agents are invoked through the runtime collaboration API. Inherited runtime model applies except screenshot review.

This file records operational configuration, not a replacement for native dialogue exports. Original tool-generated sessions must be included unchanged with the submission.
