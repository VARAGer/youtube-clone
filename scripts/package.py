#!/usr/bin/env python3
"""Package repository history and unchanged native AI session/export files.

Automatic discovery selects Codex sessions by session_meta.cwd only. Explicit
--session-file accepts an exact native continuation session whose working
folder differs from the original checkout; the caller must select this project
only. Compressed JSONL files are copied in their original compressed form.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NATIVE_SUFFIXES = ('.jsonl', '.jsonl.zst', '.json', '.zip')


@contextmanager
def session_stream(path):
    if path.name.endswith('.jsonl.zst'):
        # Read metadata only; do not decompress/rewrite native source files.
        process = subprocess.Popen(['zstd', '-dc', str(path)], stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, encoding='utf-8')
        try:
            yield process.stdout
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
            process.wait()
    else:
        with path.open('r', encoding='utf-8') as stream:
            yield stream


def session_metadata(path):
    try:
        with session_stream(path) as stream:
            for _ in range(8):
                line = stream.readline()
                if not line:
                    break
                entry = json.loads(line)
                if entry.get('type') == 'session_meta':
                    return entry.get('payload', {})
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    return None


def project_session(path):
    metadata = session_metadata(path)
    if not metadata or not metadata.get('cwd'):
        return False
    candidate = Path(metadata['cwd']).resolve()
    return candidate == ROOT.parent or candidate == ROOT or ROOT in candidate.parents


def copy_snapshot(source, target):
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    dest = target / source.name
    if dest.exists() and dest.read_bytes() != data:
        # Preserve the actual extension, including compressed/native exports.
        suffix = next((s for s in NATIVE_SUFFIXES if source.name.endswith(s)), source.suffix)
        stem = source.name[:-len(suffix)] if suffix else source.name
        dest = target / f'{stem}-{digest[:12]}{suffix}'
    if not dest.exists():
        dest.write_bytes(data)
    return dest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-root', type=Path, default=Path.home() / '.codex' / 'sessions')
    parser.add_argument('--session-file', action='append', type=Path, default=[],
                        help='Exact project continuation Codex JSONL (or JSONL.zst); native metadata is required')
    parser.add_argument('--native-export', action='append', type=Path, default=[],
                        help='Exact project-only official AI-tool export; copied unchanged')
    parser.add_argument('--output', type=Path, default=ROOT.parent / 'outputs' / 'youtube-submission.zip')
    parser.add_argument('--allow-missing-sessions', action='store_true', help='Create a clearly incomplete development archive')
    args = parser.parse_args()
    candidates = []
    if args.session_root.exists():
        candidates = sorted(args.session_root.rglob('*.jsonl')) + sorted(args.session_root.rglob('*.jsonl.zst'))
    sessions = [p for p in candidates if project_session(p)]
    for source in args.session_file:
        if not source.is_file() or not session_metadata(source):
            raise SystemExit(f'Native Codex session metadata missing: {source}')
    for export in args.native_export:
        if not export.is_file() or not export.name.endswith(NATIVE_SUFFIXES):
            raise SystemExit(f'Native export must be an existing JSON/JSONL/JSONL.zst/ZIP: {export}')
    target = ROOT / 'ai-sessions'
    existing = [p for p in target.glob('*') if p.is_file() and p.name.endswith(NATIVE_SUFFIXES)]
    sources = sessions + args.session_file + args.native_export
    if not sources and not existing and not args.allow_missing_sessions:
        raise SystemExit('No native project sessions found. Export this task from its AI tool and rerun with --session-root or --session-file. Refusing to label an incomplete submission complete.')
    git_dir = ROOT / '.git'
    if not git_dir.is_dir():
        raise SystemExit('A full .git directory is required; package a standalone checkout, not a linked worktree.')
    subprocess.run(['git', 'fsck', '--no-dangling'], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    target.mkdir(exist_ok=True)
    for source in sources:
        copy_snapshot(source, target)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    excluded = {'.env', 'backups', '.venv', 'node_modules', '__pycache__', '.pytest_cache', '.DS_Store', 'work', 'outputs', 'test-results', 'playwright-report'}
    output = args.output.resolve()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(ROOT.rglob('*')):
            relative = path.relative_to(ROOT)
            if any(part in excluded for part in relative.parts) or path.resolve() == output or path.is_symlink() or not path.is_file():
                continue
            archive.write(path, Path('youtube') / relative)
    print(f'Archive: {output}')
    print(f'Native session/export files: {len([p for p in target.iterdir() if p.is_file() and p.name.endswith(NATIVE_SUFFIXES)])}; Git history included unchanged.')
    print('Native files are byte-exact snapshots; their presence alone does not establish that every original conversation was exported.')
    if not sources and not existing:
        print('INCOMPLETE: native AI sessions are missing.')


if __name__ == '__main__':
    main()
