# Continue this project in Codex

> Historical handoff checkpoint. The continuation executed real Docker,
> PostgreSQL/S3/FFmpeg, browser and archive restore checks; current results and
> remaining limitations are recorded in `docs/verification.md`. Keep the original
> limitations below as historical context rather than current claims.

This is a handoff note, not an original AI conversation export. It must never
be represented as a substitute for native dialogue files required by the assignment.

## User's goal and working requirements

Complete the YouTube clone hiring assignment and verify that it actually works
before presenting a final report. Preserve the complete Git history, make
meaningful incremental commits, and do not squash or rewrite existing history.
Delegate useful work to agents. The user explicitly requires the screenshot
review agent to use **gpt-6-luna**. Other agents use the session's default model.

The final ZIP must contain all source, infrastructure, docs, tests, seed scripts,
the complete `.git` directory and all commits, native unmodified AI dialogues for
this assignment only, and the agent instructions/configuration used. Never
fabricate conversations or substitute copied messages or summaries for exports.

## Existing repository

Work in this existing checkout; do not start over:

`/Users/aleksandr/Documents/Codex/2026-11-16/youtube-claude-cursor-chatgpt-10-youtube/youtube`

Read `AGENTS.md`, `README.md`, `docs/contract.md`, and `docs/verification.md` first.
At the start of handoff there were six implementation commits through
`1fb4c84`; this handoff note is an additional commit. Preserve them all.

Implemented: FastAPI, SQLAlchemy/PostgreSQL, private S3/MinIO, durable DB job
queue and FFmpeg worker; public/unlisted/private videos with recipient grants;
roles and moderation; media Range responses; feed/watch/channel/search/upload/
registration/login/studio/moderation/admin screens; comments, likes, playlists,
subscriptions; Ubuntu deployment, synthetic demo clips, HTTP smoke tests,
Playwright scenarios, CI, backups and submission packaging.

## Actual evidence and limitations

The local application suite passed **31 tests and 3 subtests**, using FastAPI
TestClient, SQLite and filesystem storage. The worker lifecycle tests mock the
encoder. Cached versions used for these tests differed from pinned Docker
requirements. Syntax checks passed for Python, shell, frontend JS and the
browser spec; Compose YAML was structurally parsed. These checks do not prove
the actual deployment or browser playback.

NOT YET VERIFIED: Docker builds, clean Ubuntu bootstrap, actual PostgreSQL locks,
S3 compatibility, FFmpeg processing, browser playback/seeking, visual screenshots,
backup restoration and running the extracted final ZIP.

The previous managed environment had no Docker and denied network DNS and
localhost binding; headless Chrome aborted and CUA Chrome was unavailable.
Recheck the new environment rather than assuming those restrictions persist.
Do not bypass restrictions. If capabilities remain unavailable, report the exact
missing capability without claiming completion.

## Next execution steps

1. Check Git status, Docker engine/Compose, network and browser capability.
2. Run `bash scripts/deploy.sh` in a suitable environment. On clean Ubuntu the
   script installs Docker, generates secrets and starts/seeds the full stack.
3. Verify all services and actual seeded videos. In particular validate the
   source-built pinned MinIO release and the app's pinned dependencies.
4. Run `docker compose exec -T app python -m unittest discover -s tests -v` and
   `docker compose exec -T app python scripts/smoke.py`.
5. Run `frontend` Playwright tests against the running stack. The CI workflow
   contains fixture generation commands. Tests use real APIs and media, not mocks.
   Give actual desktop/mobile screenshots to the gpt-6-luna review agent.
6. Correct failures and review UX, permissions, private metadata/media/thumbnail/
   comments, grant/revocation, roles, moderation, malformed inputs and restarts.
7. Record only executed outcomes in `docs/verification.md`; commit milestones.
8. Obtain exact native exports for the original chat and continuing Codex/agent
   sessions. The original task's native JSONL files were not found in local
   `~/.codex/sessions`; an official project-only export is still required.
9. Run `scripts/package.py` with the native exports, inspect the ZIP including
   `.git`, extract it and verify one-command deployment again before delivery.

The user's latest instruction is to move continuation into Codex. Automatic
task creation was attempted but the MCP request was rejected, so this file
supports a manual continuation in the existing repository.
