# Engineering rules
Stack: FastAPI, SQLAlchemy 2, PostgreSQL, private S3 bucket (MinIO), database-backed worker with ffmpeg, vanilla HTML/CSS/JS frontend served by API. Docker Compose deploys all services.
Security: HttpOnly session cookie, CSRF Origin checks on writes, server-side ACL on every metadata/media path, never publish bucket, hashes via scrypt, no user-selected roles. No secrets committed.
Agents own explicit directories; integration owner commits meaningful milestones. Tests must cover access control, lifecycle, moderation and real media range playback. Never claim unrun checks passed. Preserve native AI session files unchanged; generated notes are not transcripts.
