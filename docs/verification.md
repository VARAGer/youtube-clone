# Verification record

This file records actual checks only. An unchecked item is not a passing result.
The integration owner updates results after executing the checks.

## Executed on 2026-11-16

- Local application suite: **31 tests and 3 subtests passed**, 1.54 seconds, using FastAPI TestClient, isolated SQLite and filesystem storage. Command: `python -m pytest -q --disable-warnings` with the available cached dependency runtime. Python 3.12, FastAPI 0.142.2, SQLAlchemy 2.1.3, pytest 9.2.0. These cached versions differ from the pinned container requirements; the pinned environment remains to be tested.
- JavaScript syntax: `node --check frontend/app.js` and browser spec succeeded.
- Shell syntax: `bash -n scripts/deploy.sh scripts/backup.sh` succeeded.
- Screenshot reviewer (GPT-6 Luna) reviewed responsive source only; fixed inaccessible mobile menu. No rendered screenshot was available and no visual acceptance is claimed.
- Runtime blockers: Docker unavailable; network package downloads fail; binding localhost returns EPERM; headless Chrome aborts under the execution sandbox; CUA reports Chrome unavailable. Therefore actual video encoding, PostgreSQL/S3 integration, clean Ubuntu bootstrap and browser playback are **not verified**.
- Native current-task AI session JSONL files were not found in the local Codex sessions directory. An official export of this task is required; reconstructed dialogue is not acceptable. The packager refuses a final submission by default without native exports.

## Required acceptance checks

- [ ] Fresh Ubuntu deployment using only `bash scripts/deploy.sh`
- [ ] All Compose services healthy, demo clips playable
- [ ] Register, log in, upload, observe processing, play, seek, like, comment
- [ ] Search, channel, subscription and playlist navigation
- [ ] Private video: anonymous and unrelated viewer blocked for metadata, thumbnail and Range
- [ ] Explicit recipient grant works; revocation takes effect on subsequent media requests
- [ ] Moderator can hide video; hidden media cannot play; user cannot moderate
- [ ] Admin role management restricted to admin
- [ ] Worker retry/restart behavior and malformed video rejection
- [x] Local application automated test suite passes (limitations above)
- [ ] Desktop and mobile screenshots inspected
- [ ] Native project AI sessions present unchanged; `.git` and commit history in ZIP
- [ ] ZIP extraction and deployment smoke test

## Reproducible commands

```sh
PYTHONPATH=backend python3 -m unittest discover -s tests -v
bash -n scripts/deploy.sh scripts/backup.sh
python3 -m py_compile scripts/seed.py scripts/package.py
docker compose config --quiet
docker compose exec -T app python scripts/smoke.py
python3 scripts/package.py
```

Tests using a local storage adapter or SQLite establish application behavior,
not PostgreSQL locking or S3 compatibility. Those properties require separate
runs against the actual Compose services. Ubuntu deployment, backup restore and
browser playback are manual integration checks unless explicitly automated.
