#!/usr/bin/env python3
"""Verify backup/restore against an explicitly disposable Compose deployment.

The target checkout must already be deployed and seeded. This check stops the
selected project, backs it up, deletes only its guarded named volumes, restores
it, compares every database table count and an actual HTTP video's SHA-256,
then runs smoke, API and worker integration checks. Docker connection settings
are inherited from the caller; credentials and rendered Compose config are
never written to evidence. Keep the target checkout's .env throughout restore.

Example:
  python3 scripts/verify_restore.py --checkout /tmp/extracted/youtube \\
    --project youtube-archive --protected-project youtube-clone \\
    --backup-dir /tmp/archive-backup --evidence-dir /tmp/restore-evidence \\
    --disposable
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess


BASELINE_CODE = '''
import hashlib,json,urllib.request
from sqlalchemy import select,func
from app.db import Base, SessionLocal
from app.models import Video
with SessionLocal() as db:
    counts={table.name:db.scalar(select(func.count()).select_from(table)) for table in Base.metadata.sorted_tables}
    video=db.scalar(select(Video).where(Video.status=='ready',Video.visibility=='public',Video.hidden==False).order_by(Video.id))
    assert video is not None, 'A seeded public ready video is required'
    vid=video.id
with urllib.request.urlopen(f'http://127.0.0.1:8000/api/videos/{vid}/media',timeout=20) as response:
    payload=response.read()
    assert response.status==200 and len(payload)>100
print(json.dumps({'counts':counts,'public_video_id':vid,'media_bytes':len(payload),'media_sha256':hashlib.sha256(payload).hexdigest()},sort_keys=True))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--checkout', required=True, type=Path, help='Already deployed extracted repository')
    parser.add_argument('--project', required=True, help='Disposable Compose project name')
    parser.add_argument('--protected-project', required=True, help='A different project that must remain untouched')
    parser.add_argument('--backup-dir', required=True, type=Path, help='New or empty backup directory, outside checkout')
    parser.add_argument('--evidence-dir', required=True, type=Path, help='Directory for logs and before/after evidence')
    parser.add_argument('--disposable', action='store_true', help='Explicitly allow deletion of the guarded project volumes')
    args = parser.parse_args()
    if not args.disposable:
        parser.error('--disposable is required: this verification deletes the target project volumes')
    if args.project == args.protected_project:
        parser.error('Target project must differ from --protected-project')
    if not all(re.fullmatch(r'[a-z0-9][a-z0-9_-]*', value) for value in (args.project, args.protected_project)):
        parser.error('Use valid lowercase Compose project names')
    checkout, backup, evidence = (path.resolve() for path in (args.checkout, args.backup_dir, args.evidence_dir))
    if not (checkout / 'compose.yaml').is_file() or not (checkout / 'scripts/backup.sh').is_file():
        parser.error('--checkout must contain compose.yaml and scripts/backup.sh')
    if backup == checkout or checkout in backup.parents:
        parser.error('--backup-dir must be outside checkout so recreation cannot remove the backup')
    if backup.exists() and (not backup.is_dir() or any(backup.iterdir())):
        parser.error('--backup-dir must be new or empty; existing backups are never overwritten')
    evidence.mkdir(parents=True, exist_ok=True)
    # Files may contain operational data; credentials/configuration are not logged.
    os.chmod(evidence, 0o700)
    env = dict(os.environ, COMPOSE_PROJECT_NAME=args.project)
    # The mandatory guard inspects effective resolved configuration in memory.
    # Existing caller PORT/BIND_ADDRESS and Docker connection settings are retained.
    compose = ['docker', 'compose', '--project-name', args.project]

    with (evidence / 'restore.log').open('w') as log:
        def run(command, *, stdin=None, capture=False):
            result = subprocess.run(command, cwd=checkout, env=env, input=stdin,
                                    stdout=subprocess.PIPE if capture else log,
                                    stderr=log, check=True)
            log.flush()
            return result.stdout if capture else None

        def identities(project):
            return sorted(run(['docker', 'ps', '-q', '--filter',
                               'label=com.docker.compose.project=' + project], capture=True).decode().splitlines())

        def guard():
            config = json.loads(run(compose + ['config', '--format', 'json'], capture=True))
            if config.get('name') != args.project:
                raise RuntimeError('Disposable project name guard failed')
            expected_volumes = set()
            for volume in config.get('volumes', {}).values():
                name = volume.get('name', '')
                if volume.get('external') or not name.startswith(args.project + '_'):
                    raise RuntimeError('Disposable named-volume prefix/external guard failed')
                expected_volumes.add(name)
            if not expected_volumes:
                raise RuntimeError('Disposable deployment must have dedicated named volumes')
            # Refuse to stop containers with bind mounts into host data, and
            # verify every actual mount belongs to the guarded project volumes.
            container_ids = run(compose + ['ps', '-aq'], capture=True).decode().splitlines()
            if not container_ids:
                raise RuntimeError('Target must already be deployed and seeded')
            containers = json.loads(run(['docker', 'inspect', *container_ids], capture=True))
            for container in containers:
                if container.get('Config', {}).get('Labels', {}).get('com.docker.compose.project') != args.project:
                    raise RuntimeError('Container project label guard failed')
                for mount in container.get('Mounts', []):
                    if mount.get('Type') != 'volume' or mount.get('Name') not in expected_volumes:
                        raise RuntimeError('Disposable container mount guard failed')
            # Cross-project attachment would make volume deletion unsafe.
            protected_ids = identities(args.protected_project)
            if protected_ids:
                protected = json.loads(run(['docker', 'inspect', *protected_ids], capture=True))
                for container in protected:
                    if any(mount.get('Name') in expected_volumes for mount in container.get('Mounts', [])):
                        raise RuntimeError('Target shares a volume with protected project')
            return protected_ids

        def baseline():
            return json.loads(run(compose + ['exec', '-T', 'app', 'python', '-c', BASELINE_CODE], capture=True))

        protected_before = guard()
        before = baseline()
        (evidence / 'before.json').write_text(json.dumps(before, indent=2) + '\n')
        run(['bash', 'scripts/backup.sh', str(backup)])
        if not all((backup / name).is_file() and (backup / name).stat().st_size > 0
                   for name in ('database.dump', 'objects.tar.gz')):
            raise RuntimeError('Backup files are missing or empty; refusing volume deletion')
        if guard() != protected_before:
            raise RuntimeError('Protected project changed during backup; refusing volume deletion')
        run(compose + ['down', '--volumes'])
        run(compose + ['up', '-d', '--wait', 'db', 'minio'])
        run(compose + ['stop', 'minio'])
        minio_id = run(compose + ['ps', '-aq', 'minio'], capture=True).decode().strip()
        if not minio_id:
            raise RuntimeError('Restored MinIO container missing')
        run(['docker', 'run', '--rm', '--volumes-from', minio_id,
             '-v', str(backup) + ':/backup:ro', 'alpine:3.22',
             'tar', '-xzf', '/backup/objects.tar.gz', '-C', '/data'])
        run(compose + ['exec', '-T', 'db', 'pg_restore', '-U', 'youtube', '-d', 'youtube',
                       '--clean', '--if-exists'], stdin=(backup / 'database.dump').read_bytes())
        run(compose + ['up', '-d', '--wait'])
        after = baseline()
        (evidence / 'after.json').write_text(json.dumps(after, indent=2) + '\n')
        if before != after:
            raise RuntimeError('Restored database table counts or HTTP media digest differ; inspect before/after.json')
        if identities(args.protected_project) != protected_before:
            raise RuntimeError('Protected project container identities changed')
        print('PASS: fresh-volume restore preserves all DB counts and exact HTTP media SHA-256', flush=True)
        checks = {}
        for name, command in (
            ('smoke', ['python', 'scripts/smoke.py']),
            ('api', ['python', '-m', 'unittest', 'discover', '-s', 'tests', '-v']),
            ('runtime', ['python', 'scripts/integration_runtime.py']),
        ):
            with (evidence / (name + '.log')).open('w') as output:
                subprocess.run(compose + ['exec', '-T', 'app'] + command, cwd=checkout,
                               env=env, stdout=output, stderr=subprocess.STDOUT, check=True)
            checks[name] = 'passed'
            print('PASS: ' + name, flush=True)
        if identities(args.protected_project) != protected_before:
            raise RuntimeError('Protected project container identities changed during verification')
        result = {'project': args.project, 'protected_project': args.protected_project,
                  'protected_container_ids_unchanged': True, 'restore': 'passed',
                  'checks': checks, 'baseline': after}
        (evidence / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print('Evidence: ' + str(evidence), flush=True)


if __name__ == '__main__':
    main()
