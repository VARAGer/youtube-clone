# Verification record — 2026-11-16

Only executed checks are recorded as passing. Native dialogue completeness is a
separate requirement from application verification.

## Environments and evidence

- Initial continuation: macOS arm64, a dedicated Colima Ubuntu 24.04.4 VM,
  Docker Engine 29.5.2, PostgreSQL 17.6, private MinIO built from source release
  `RELEASE.2025-10-15T17-29-55Z`, FFmpeg 7.1.5, Python 3.12.16.
- Container dependencies: FastAPI 0.115.12, SQLAlchemy 2.0.40, psycopg 3.2.6,
  boto3 1.38.0, from the pinned requirements. Unit tests use isolated SQLite/local
  storage; their results are not presented as PostgreSQL/S3 verification.
- Browser: Node.js 22.16.0, Playwright 1.58.2, real Chromium 145.0.7632.6 on
  macOS. Real HTTP APIs, files, worker processing and media decoding; no request
  mocks. Desktop 1280×900 and mobile 390×844 screenshots.
- Clean installation: separate Lima Ubuntu 24.04.5 arm64 VM, initially no Docker
  executable or installed Docker packages. The unmodified bootstrap path ran
  apt against Docker's repository and enabled its daemon. Docker 29.10.0 and
  Compose 5.7.1 were installed by `bash scripts/deploy.sh`.
- Logs, before/after restore counts, video hashes and the seven rendered PNGs
  are in `docs/evidence/`. These are execution evidence, not dialogue exports.

## Executed acceptance checks

- [x] Docker builds for the API/worker and pinned source-built MinIO.
- [x] One-command deployment from an extracted ZIP on Ubuntu with Docker already
      installed; separately, the real no-Docker Ubuntu apt bootstrap and deploy.
- [x] PostgreSQL/MinIO/app readiness and nine generated, processed demo videos
      (eight public, one private with an explicitly granted recipient).
- [x] Real HTTP upload, FFmpeg processing, complete MP4 response, exact byte and
      suffix Range responses, JPEG thumbnail, views, likes and comments.
- [x] Browser registration, upload, ready polling, media play, advancing playback
      time, seek to 1.8 seconds and resumed playback, likes/comments, literal
      script text rendering, search, channel and playlist creation.
- [x] Subscription and playlist persistence through real HTTP smoke tests.
- [x] Private metadata/media/thumbnail/comments denied to anonymous, unrelated
      signed-in viewers and staff without a grant. Grant and revocation checked
      on subsequent requests. Staff role does not override private video ACL.
- [x] Ordinary users cannot moderate or manage roles. Admin upgrade and demotion
      change permissions of an already-open session. Self-demotion is rejected.
- [x] Moderator report queue, hide/unhide, and hidden media denial even to owner.
- [x] Real malformed MP4 rejection by ffprobe and S3 source cleanup.
- [x] Actual PostgreSQL `FOR UPDATE SKIP LOCKED` with a held queue-head lock;
      two worker processes claim distinct jobs exactly once.
- [x] SIGKILL of a claimed worker preserves its processing job; a fresh lease is
      not stolen; a new process completes the job after the lease timestamp is
      made 36 minutes old. This accelerates expiry; it is not a 36-minute wait.
- [x] Native H.264/JPEG output verified from actual S3 objects.
- [x] Seven actual rendered desktop/mobile screenshots inspected by **GPT-6
      Luna**, repeated after fixes; no remaining critical visual findings.
- [x] Full backup and restore into fresh disposable PostgreSQL/MinIO volumes:
      all 12 table counts and complete HTTP video bytes match before/after.
      Known video: 118537 bytes, SHA-256
      `83d989e35e86e0082fbfa62ffbdcb2be1e7d3111cca981753901f61fa136763f`.
      Smoke, API and runtime checks passed again after restore. Protected stack
      container IDs were unchanged during the restore check.
- [x] Portable `scripts/verify_restore.py` executed with its project/volume
      guards, explicit disposable target, fresh-volume restore and follow-up
      checks; evidence is under `docs/evidence/restore-harness/`.
- [x] Four packaging regressions: unrelated session exclusion, exact native byte
      preservation, compressed native files, complete Git history and secret
      exclusion after ZIP extraction. Git integrity checked with `git fsck`.
- [x] Runtime/source snapshot `fa249b2` deployed from an extracted ZIP on the
      clean Ubuntu VM; all original commits are ancestors and Git integrity
      passed. Final packaging integrity and native hashes are checked separately.
- [ ] Original pre-handoff dialogue export: **unavailable**. The user confirmed
      that they do not have it. It has not been reconstructed or replaced by
      summaries. Therefore a complete original-dialogue bundle cannot be claimed.

## Final full run

On the extracted final runtime/source snapshot `fa249b2`, clean Ubuntu:
**36 API tests passed** in 2.291 seconds; the updated real HTTP smoke passed,
including anonymous direct MinIO bucket/object denial (403) and private title
redaction in moderation reports. All isolated PostgreSQL/S3/FFmpeg concurrency,
recovery and malformed-input checks passed. Browser acceptance against that
same deployment: **3 passed in 19.6 seconds**. GPT-6 Luna inspected its seven
final PNGs with no critical or noticeable cosmetic findings.

Documentation, evidence, session provenance and packaging metadata can follow
this runtime/source commit without changing the tested application code.
The complete pre-existing Git history is retained; no commits were rewritten.

## Fixes found by actual checks

1. Missing S3 objects and deletion between HEAD/GET returned server errors;
   they now return 404. A source-cleanup outage after publication no longer
   unpublishes successful encoded output.
2. Docker scripts could not import `app`; the image now sets `PYTHONPATH=/app`.
   Repeated deploys could reuse an obsolete seed profile image; deploy rebuilds
   it explicitly.
3. The watch page was underneath the desktop sidebar. Correct margins and a
   geometry regression prevent overlap. Selects have stable accessible labels;
   browser submit locators are scoped to the active dialog.
4. Mobile admin role controls were outside the visible table. Mobile rows now
   show channel/email/role as cards, with an in-viewport selector check.
   Empty/offscreen toast artifacts are hidden; captures wait for success toasts
   to dismiss and reset scroll before taking full-page screenshots.
5. `sudo docker` dropped the exported Compose project name on clean Ubuntu;
   deploy now passes it explicitly with `--project-name`.
6. A moderation report could reveal a video's title after it became private.
   Unauthorized staff now see a neutral title; grant and revocation are covered
   by a new regression and real HTTP smoke scenario.

Node 26 caused Playwright archive/teardown hangs in this environment; browser
checks were rerun on Node 22, which the frontend tooling and CI now specify.
The clean Ubuntu build was deliberately stopped once for host disk pressure;
only task-created build/download caches were cleared, then deployment resumed.
Neither interrupted run is counted as a successful full run.

## Reproduction

```sh
bash scripts/deploy.sh
docker compose exec -T app python -m unittest discover -s tests -v
docker compose exec -T app python scripts/smoke.py
docker compose exec -T app python scripts/integration_runtime.py
python3 scripts/test_package.py -v
cd frontend
npm ci --ignore-scripts
npx playwright install --with-deps chromium
BASE_URL=http://localhost:8080 npm test
```

Use Node 22. `E2E_VIDEO` can provide a real MP4 fixture instead of host FFmpeg;
`PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` can select a compatible Chromium binary.
For an external deployment, supply its seeded `DEMO_PASSWORD` to the browser
test process; credentials are never stored in the submission.

Restore verification intentionally deletes only an explicitly disposable
project's guarded volumes; run it on a separate seeded deployment as documented
in `scripts/verify_restore.py --help`, never on data to retain.

The old pre-handoff local result was 31 tests on cached, differing dependency
versions. It is historical context and is superseded by this continuation's
pinned-container and real-service checks. These checks cover this assignment;
they do not establish production security, load capacity, x86_64 behavior or
other Ubuntu releases.
