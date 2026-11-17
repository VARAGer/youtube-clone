"""Synthetic real-media fixtures, generated locally with FFmpeg.

No downloaded assets. The cover-first MP4 has normal H.264/AAC tracks and an
embedded JPEG cover whose metadata precedes the tracks in moov. FFmpeg exposes
that attached picture as stream 0, so callers must select a playable video
stream rather than blindly mapping the first video-type stream.
"""
from pathlib import Path
import struct
import subprocess
import tempfile


def _boxes(data, start=0, end=None):
    """Yield (type, offset, size, header_size) for bounded ISO BMFF boxes."""
    end = len(data) if end is None else end
    cursor = start
    while cursor < end:
        if end - cursor < 8:
            raise ValueError('Truncated MP4 box header')
        size, kind = struct.unpack_from('>I4s', data, cursor)
        header = 8
        if size == 1:
            if end - cursor < 16:
                raise ValueError('Truncated extended MP4 box header')
            size = struct.unpack_from('>Q', data, cursor + 8)[0]
            header = 16
        elif size == 0:
            size = end - cursor
        if size < header or cursor + size > end:
            raise ValueError('MP4 box exceeds its parent')
        yield kind, cursor, size, header
        cursor += size
    assert cursor == end


def _cover_before_tracks(data):
    top = list(_boxes(data))
    _, start, size, header = next(box for box in top if box[0] == b'moov')
    children = list(_boxes(data, start + header, start + size))
    cover_box = next(box for box in children if box[0] == b'udta')
    _, offset, length, box_header = cover_box
    meta = next(box for box in _boxes(data, offset + box_header, offset + length)
                if box[0] == b'meta')
    _, offset, length, box_header = meta
    # meta is a FullBox: skip its four version/flags bytes before its children.
    ilst = next(box for box in _boxes(data, offset + box_header + 4, offset + length)
                if box[0] == b'ilst')
    _, offset, length, box_header = ilst
    assert any(box[0] == b'covr' for box in _boxes(data, offset + box_header, offset + length))
    ordered = [box for box in children if box is not cover_box]
    first_track = next(index for index, box in enumerate(ordered) if box[0] == b'trak')
    ordered.insert(first_track, cover_box)
    # Move whole child boxes only. Chunk offsets remain valid because moov has
    # exactly the same length and every top-level box keeps its original offset.
    result = (data[:start + header]
              + b''.join(data[position:position + length] for _, position, length, _ in ordered)
              + data[start + size:])
    assert len(result) == len(data)
    assert list(_boxes(result)) == top
    changed_children = list(_boxes(result, start + header, start + size))
    assert [data[p:p + n] for kind, p, n, _ in children if kind == b'trak'] == [
        result[p:p + n] for kind, p, n, _ in changed_children if kind == b'trak']
    for kind, position, length, _ in top:
        if kind == b'mdat':
            assert result[position:position + length] == data[position:position + length]
    assert next(i for i, box in enumerate(changed_children) if box[0] == b'udta') < next(
        i for i, box in enumerate(changed_children) if box[0] == b'trak')
    return result


def make_cover_first_mp4(path):
    """Write and return Path: three seconds, 72 H.264 frames, AAC, JPEG cover.

    Requires ffmpeg on PATH with libx264/AAC/MJPEG encoders. On pinned FFmpeg
    7.1 the resulting streams are JPEG attached_pic=1 at index 0, playable H.264
    at index 1, and AAC at index 2. Media payloads and absolute chunk offsets
    are unchanged by the metadata reordering.
    """
    path = Path(path)
    with tempfile.TemporaryDirectory(prefix='youtube-media-fixture-') as directory:
        root = Path(directory)
        base, cover, combined = root / 'base.mp4', root / 'cover.jpg', root / 'covered.mp4'

        def ffmpeg(*arguments):
            subprocess.run(['ffmpeg', '-hide_banner', '-v', 'error', '-nostdin',
                            *map(str, arguments)], check=True, capture_output=True, timeout=90)

        ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24:duration=3',
               '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3',
               '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', '-y', base)
        ffmpeg('-f', 'lavfi', '-i', 'color=c=red:size=100x100', '-frames:v', '1', '-y', cover)
        ffmpeg('-i', base, '-i', cover, '-map', '0', '-map', '1', '-c', 'copy',
               '-disposition:v:1', 'attached_pic', '-movflags', '+faststart', '-y', combined)
        result = _cover_before_tracks(combined.read_bytes())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(result)
    return path
