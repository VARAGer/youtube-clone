#!/usr/bin/env python3
"""Package exact repository history and project-native Codex sessions.

Only session_meta.cwd is inspected for selection. Session files are copied byte
for byte, never converted into notes or reconstructed transcripts. Optional
--session-root accepts a directory from an official export when local native
sessions are unavailable. Existing ai-sessions contents are never replaced.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def project_session(path):
    try:
        with path.open('r', encoding='utf-8') as stream:
            for _ in range(8):
                line = stream.readline()
                if not line:
                    break
                entry = json.loads(line)
                if entry.get('type') == 'session_meta':
                    cwd = entry.get('payload', {}).get('cwd', '')
                    if not cwd:
                        return False
                    candidate = Path(cwd).resolve()
                    # This task began at ROOT.parent; do not include arbitrary other sessions.
                    return candidate == ROOT.parent or candidate == ROOT or ROOT in candidate.parents
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-root', type=Path, default=Path.home() / '.codex' / 'sessions')
    parser.add_argument('--native-export', action='append', type=Path, default=[], help='An exact project-only export file from the original AI tool; copied unchanged')
    parser.add_argument('--output', type=Path, default=ROOT.parent / 'outputs' / 'youtube-submission.zip')
    parser.add_argument('--allow-missing-sessions', action='store_true', help='Create a clearly incomplete development archive')
    args = parser.parse_args()
    sessions = [p for p in args.session_root.rglob('*.jsonl') if project_session(p)] if args.session_root.exists() else []
    native_already = list((ROOT / 'ai-sessions').glob('*.jsonl'))
    for export in args.native_export:
        if not export.is_file():
            raise SystemExit(f'Native export is not a file: {export}')
    exports_already = [p for p in (ROOT / 'ai-sessions').glob('*') if p.is_file() and p.suffix in ('.json', '.jsonl', '.zip')]
    if not sessions and not native_already and not exports_already and not args.native_export and not args.allow_missing_sessions:
        raise SystemExit('No native project sessions found. Export this task from its AI tool and rerun with --session-root. Refusing to label an incomplete submission complete.')
    target = ROOT / 'ai-sessions'
    target.mkdir(exist_ok=True)
    for source in sessions + args.native_export:
        dest = target / source.name
        if dest.exists() and dest.read_bytes() != source.read_bytes():
            # Native active sessions append; keep a byte-exact snapshot with a content suffix.
            digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
            dest = target / f'{source.stem}-{digest}.jsonl'
        shutil.copyfile(source, dest)
    if subprocess.check_output(['git', 'rev-parse', '--is-inside-work-tree'], cwd=ROOT, text=True).strip() != 'true':
        raise SystemExit('Repository required')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    excluded = {'.env', 'backups', '.venv', 'node_modules', '__pycache__', '.pytest_cache', '.DS_Store', 'work', 'test-results', 'playwright-report'}
    output = args.output.resolve()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(ROOT.rglob('*')):
            relative = path.relative_to(ROOT)
            if any(part in excluded for part in relative.parts) or path.resolve() == output or path.is_symlink() or not path.is_file():
                continue
            archive.write(path, Path('youtube') / relative)
    print(f'Archive: {output}')
    print(f'Native session/export files: {len([p for p in target.iterdir() if p.is_file()])}; Git history included unchanged.')
    if not sessions and not native_already and not exports_already and not args.native_export:
        print('INCOMPLETE: native AI sessions are missing.')


if __name__ == '__main__':
    main()
