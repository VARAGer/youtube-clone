# Security design and operational boundaries

* Passwords are one-way scrypt hashes; raw passwords are never stored.
* The session cookie is HttpOnly. Configure secure cookies and HTTPS in production.
* State-changing requests validate their Origin; the UI is served from the same
  origin. With PUBLIC_ORIGIN blank the expected origin is the request host;
  set it to the externally visible URL behind a reverse proxy.
* Roles are assigned through a protected admin endpoint or controlled bootstrap,
  never through registration input.
* Each media and thumbnail request resolves the current session and current ACL.
  The S3 bucket is private; no long-lived public URLs or leaked internal keys.
  Moderation reports redact private titles unless the staff member owns the video
  or currently has a recipient grant.
* Upload limits bound accepted data. ffprobe/ffmpeg validate the actual media;
  filename extensions and client MIME types do not establish trust.
* ffmpeg runs without shell expansion; server-generated object paths prevent
  traversal. Containers run the media processor without elevated privileges.
* SQLAlchemy parameterizes SQL. Frontend rendering uses safe text insertion for
  user-provided titles and comments.
* PostgreSQL and MinIO are internal services. The only host-bound service is
  the application port. Never publish the database or MinIO console remotely.

Before public hosting: terminate HTTPS at a trusted reverse proxy, set
PUBLIC_ORIGIN and COOKIE_SECURE=true, limit proxy request sizes/timeouts, and
place rate limits at the edge. Keep Ubuntu, Docker and FFmpeg patched. Use a
separate least-privilege S3 service account when connecting an external store.
The bundled MinIO root credential is internal to this self-contained deployment;
rotating it requires updating the app configuration as well.

This is a test implementation, not a production clone at YouTube's scale. There
is no malware scanner, email verification/recovery, adaptive bitrate ladder,
CDN, recommendation model, copyright detection, distributed tracing or a
multi-region disaster-recovery system. These are explicit scope limits, not
implemented features. Application-level security tests do not replace a
penetration test or host hardening.

Native AI session files may include prompts and command outputs. Automatic package
discovery selects sessions by project working directory; explicit continuation
files are selected by exact native session ID; it it never bundles account auth/config files from the user's home.
Generated secrets and local runtime stores are excluded from the submission.

MinIO source release `RELEASE.2025-10-15T17-29-55Z` includes the upstream fix
for service-account session-policy bypass (GHSA-jjjj-jwhf-8rgr). It is built
locally because historical official Docker registries are no longer reliable.
Upstream source and advisory: https://github.com/minio/minio/releases/tag/RELEASE.2025-10-15T17-29-55Z
and https://github.com/minio/minio/security/advisories/GHSA-jjjj-jwhf-8rgr .
This pin is reproducible; it is not a claim that every later vulnerability is fixed.
