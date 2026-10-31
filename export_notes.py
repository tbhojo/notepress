#!/usr/bin/env python3
"""NotePress: export supported handwritten Apple Notes as paginated vector PDFs.

Uses the local Notes library and Apple frameworks. No Python packages required.
See README.md for the tested format and compatibility limits.
"""
# SPDX-License-Identifier: MIT
import argparse
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import os
from pathlib import Path
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import uuid

HERE = Path(__file__).resolve().parent
NOTES = Path.home() / 'Library/Group Containers/group.com.apple.notes'


def varint(data, index):
    value = shift = 0
    while index < len(data) and shift <= 63:
        byte = data[index]
        index += 1
        value |= (byte & 127) << shift
        if not byte & 128:
            return value, index
        shift += 7
    raise ValueError('Invalid protobuf integer')


def parse(data):
    index = 0
    fields = []
    while index < len(data):
        tag, index = varint(data, index)
        number, wire = tag >> 3, tag & 7
        if number < 1:
            raise ValueError('Invalid protobuf field')
        if wire == 0:
            value, index = varint(data, index)
        elif wire in (1, 5):
            count = 8 if wire == 1 else 4
            value = data[index:index + count]
            index += count
            if len(value) != count:
                raise ValueError('Truncated protobuf field')
        elif wire == 2:
            count, index = varint(data, index)
            value = data[index:index + count]
            index += count
            if len(value) != count:
                raise ValueError('Truncated protobuf field')
        else:
            raise ValueError('Unsupported protobuf wire type')
        fields.append((number, wire, value))
    return fields


def values(data, number):
    return [value for field, wire, value in parse(data) if field == number]


def first(data, number):
    result = values(data, number)
    if not result:
        raise ValueError('Missing field %d in drawing data' % number)
    return result[0]


def packed(data):
    result = []
    index = 0
    while index < len(data):
        value, index = varint(data, index)
        result.append(value)
    return result


def encode_int(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def field(number, value):
    if isinstance(value, int):
        return encode_int(number << 3) + encode_int(value)
    return encode_int((number << 3) | 2) + encode_int(len(value)) + value


class PaperDrawing:
    def __init__(self, database):
        with sqlite3.connect(str(database)) as connection:
            self.rows = {
                key: data[8:] if data[:4] == b'crdt' else data
                for key, data in connection.execute('SELECT Id, Data FROM Reference')
            }
        if b'\xff' not in self.rows:
            raise ValueError('Unsupported Paper database: no root record')
        canvas_ids = self.references(self.rows[b'\xff'])
        if len(canvas_ids) != 1:
            raise ValueError('Unsupported Paper canvas structure')
        canvas = self.record_fields(canvas_ids[0])
        # Fail instead of silently excluding images, shapes, or text boxes.
        if set(canvas) - {'bounds', 'frame', 'drawing'} or 'drawing' not in canvas:
            raise ValueError('This exporter supports pen-only Paper canvases; this canvas contains other elements')
        rect = first(canvas['bounds'], 4)
        if len(rect) != 32:
            raise ValueError('Unsupported canvas bounds')
        self.width = struct.unpack('>4d', rect)[2]
        drawing_ids = self.references(canvas['drawing'])
        if len(drawing_ids) != 1:
            raise ValueError('Unsupported drawing reference')
        drawing = self.record_fields(drawing_ids[0])
        if set(drawing) - {'canvasBounds', 'strokes'}:
            raise ValueError('Unsupported drawing elements')
        roots = self.references(drawing['strokes'])
        self.leaves = [leaf for root in roots for leaf in self.leaf_strokes(root)]
        if not self.leaves or len(self.leaves) != len(set(self.leaves)):
            raise ValueError('Empty drawing or unsupported repeated stroke references')
        # Per-instance caches avoid retaining data across batch exports.
        self.metadata = lru_cache(maxsize=None)(self._metadata)

    def references(self, data):
        result = []
        try:
            fields = parse(data)
        except ValueError:
            return []
        for number, wire, value in fields:
            if wire == 2:
                if value in self.rows:
                    result.append(value)
                else:
                    result.extend(self.references(value))
        return list(dict.fromkeys(result))

    def body(self, key):
        return first(self.rows[key], 1)

    def record_fields(self, key):
        body = first(self.body(key), 4)
        names = [value.decode('utf-8') for value in values(first(self.rows[key], 6), 2)]
        keys = packed(first(body, 1))
        registers = values(body, 2)
        if len(keys) != len(registers):
            raise ValueError('Unsupported record fields')
        result = {}
        for index, register in zip(keys, registers):
            kind = parse(register)[0][0]
            if kind == 1:
                value = first(first(register, 1), 2)
            elif kind == 9 and names[index] == 'strokes':
                value = register
            else:
                raise ValueError('Unsupported record value')
            result[names[index]] = value
        return result

    def leaf_strokes(self, key, depth=0):
        if depth > 50:
            raise ValueError('Drawing reference nesting is too deep')
        number, wire, value = parse(self.body(key))[0]
        if number == 4:
            if set(self.record_fields(key)) != {'inherited', 'properties'}:
                raise ValueError('Unsupported stroke record')
            return [key]
        if number in (1, 6):
            return [leaf for reference in self.references(value)
                    for leaf in self.leaf_strokes(reference, depth + 1)]
        raise ValueError('Unsupported stroke collection')

    @staticmethod
    def find_ink(data):
        try:
            fields = parse(data)
        except ValueError:
            return None
        if any(number == 2 and wire == 2 and value.startswith(b'com.apple.ink.')
               for number, wire, value in fields):
            return data
        for number, wire, value in fields:
            if wire == 2:
                ink = PaperDrawing.find_ink(value)
                if ink is not None:
                    return ink
        return None

    def _metadata(self, key):
        record = self.record_fields(key)
        if set(record) - {'inherited', 'ink', 'transform'}:
            raise ValueError('Unsupported inherited drawing properties')
        result = {}
        if 'inherited' in record:
            references = self.references(record['inherited'])
            if len(references) != 1:
                raise ValueError('Unsupported inherited reference')
            result.update(self.metadata(references[0]))
        if 'ink' in record:
            ink = self.find_ink(record['ink'])
            if ink is None:
                raise ValueError('Unsupported ink data')
            if first(ink, 2) != b'com.apple.ink.pen':
                raise ValueError('Only pen ink is supported; other brushes need separate rendering')
            result['ink'] = ink
        if 'transform' in record:
            raw = first(record['transform'], 4)
            if len(raw) != 48:
                raise ValueError('Unsupported stroke transform')
            result['transform'] = struct.unpack('>6d', raw)
        return result

    def save_classic_drawing(self, output):
        inks = []
        strokes = []
        stats = {'strokes': 0, 'masks': 0, 'transforms': 0}
        for leaf in self.leaves:
            record = self.record_fields(leaf)
            inherited = self.references(record['inherited'])
            if len(inherited) != 1:
                raise ValueError('Unsupported stroke inheritance')
            metadata = self.metadata(inherited[0])
            ink = metadata['ink']
            if ink not in inks:
                inks.append(ink)
            properties = first(record['properties'], 14)
            keys = packed(first(properties, 1))
            vals = values(properties, 2)
            if len(keys) != len(vals):
                raise ValueError('Unsupported stroke properties')
            properties = dict(zip(keys, vals))
            if set(properties) - {0, 2, 3, 4, 9}:
                raise ValueError('Unsupported stroke features; no PDF was produced')
            for enum_key in (2, 9):
                if first(properties[enum_key], 5) != 0:
                    raise ValueError('Unsupported stroke variant')
            path_ids = self.references(properties[3])
            if len(path_ids) != 1:
                raise ValueError('Unsupported stroke path reference')
            path = first(first(first(self.body(path_ids[0]), 10), 1), 2)
            if len(leaf) != 17:
                raise ValueError('Unsupported stroke identifier')
            stroke = (field(1, leaf[1:]) + field(2, b'\x08\x00\x10\x01\x18\x01')
                      + field(3, b'\x08\x00\x10\x01\x18\x00')
                      + field(4, inks.index(ink)) + field(5, path)
                      + field(8, first(properties[0], 1)))
            if 'transform' in metadata:
                transform = b''.join(encode_int((number << 3) | 5) + struct.pack('<f', value)
                                     for number, value in enumerate(metadata['transform'], 1))
                stroke += field(7, transform)
                stats['transforms'] += 1
            if 4 in properties:
                stroke += first(properties[4], 4)
                stats['masks'] += 1
            strokes.append(stroke)
            stats['strokes'] += 1
        content = (field(2, bytes(16)) + field(2, uuid.uuid4().bytes)
                   + field(3, b'\x08\x00\x10\x00\x18\x00')
                   + field(3, b'\x08\x01\x10\x01\x18\x01')
                   + b''.join(field(4, ink) for ink in inks)
                   + b''.join(field(5, stroke) for stroke in strokes)
                   + field(7, b'\x08\x00\x10\x00\x18\x00')
                   + field(8, uuid.uuid4().bytes))
        output.write_bytes(b'wrd\xf0\x01\x00\x08\x00' + content)
        return stats


def readonly(path):
    return sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)


def active_notes(titles=(), note_id=None):
    query = (
        'SELECT n.Z_PK, n.ZIDENTIFIER, n.ZTITLE1, COALESCE(f.ZTITLE2, \'\'), '
        'n.ZCREATIONDATE3, n.ZMODIFICATIONDATE1 '
        'FROM ZICCLOUDSYNCINGOBJECT n '
        'JOIN ZICCLOUDSYNCINGOBJECT f ON f.Z_PK=n.ZFOLDER '
        'WHERE n.ZTITLE1 IS NOT NULL AND COALESCE(n.ZMARKEDFORDELETION,0)=0 '
        'AND COALESCE(f.ZFOLDERTYPE,0)<>1 '
        "AND COALESCE(f.ZIDENTIFIER,'') NOT LIKE 'TrashFolder%'"
    )
    parameters = []
    if titles:
        query += ' AND n.ZTITLE1 IN (' + ','.join('?' for _ in titles) + ')'
        parameters.extend(titles)
    if note_id:
        query += ' AND n.ZIDENTIFIER=?'
        parameters.append(note_id)
    query += (' ORDER BY n.ZMODIFICATIONDATE1 DESC, n.ZCREATIONDATE3 DESC, '
              'n.ZTITLE1 COLLATE NOCASE, f.ZTITLE2, n.ZIDENTIFIER')
    with readonly(NOTES / 'NoteStore.sqlite') as connection:
        return connection.execute(query, parameters).fetchall()


def print_note_list(titles=()):
    matches = active_notes(titles)
    if not matches:
        print('No matching active notes found.')
        return

    def cell(value):
        return value.replace('\t', '\\t').replace('\r', '\\r').replace('\n', '\\n')

    def date(value):
        if value is None:
            return '-'
        timestamp = datetime(2001, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=value)
        return timestamp.astimezone().strftime('%Y-%m-%d %H:%M %Z')

    rows = [(cell(identifier), cell(folder), date(created), date(updated), cell(title))
            for _, identifier, title, folder, created, updated in matches]
    headings = ('NOTE ID', 'FOLDER', 'CREATED (LOCAL)', 'UPDATED (LOCAL)', 'TITLE')
    widths = [max(len(label), max(len(row[index]) for row in rows))
              for index, label in enumerate(headings)]
    for row in [headings] + rows:
        print('  '.join(value.ljust(width) for value, width in zip(row, widths)).rstrip())


def locate_note(title, note_id=None):
    matches = active_notes((title,), note_id)
    if len(matches) != 1:
        raise ValueError('Expected one note named %r; found %d. Run --list with the title to find note IDs, then select one with --note-id.' % (title, len(matches)))
    with readonly(NOTES / 'NoteStore.sqlite') as connection:
        attachments = connection.execute(
            'SELECT ZIDENTIFIER,ZTYPEUTI,ZACCOUNT1 FROM ZICCLOUDSYNCINGOBJECT '
            'WHERE ZNOTE=? AND COALESCE(ZMARKEDFORDELETION,0)=0', (matches[0][0],)).fetchall()
        if len(attachments) != 1 or attachments[0][1] != 'com.apple.paper':
            raise ValueError('Supported notes must contain exactly one Paper drawing and no other attachments')
        # A note with typed text must not silently lose that text.
        note_data = connection.execute('SELECT ZDATA FROM ZICNOTEDATA WHERE ZNOTE=?', (matches[0][0],)).fetchone()
        if note_data:
            import gzip
            document = gzip.decompress(note_data[0])
            # Document.field2 -> note wrapper.field3 -> body.field2 UTF-8 text.
            texts = []
            for note in values(document, 2):
                for body in values(note, 3):
                    texts.extend(values(body, 2))
            if not texts:
                raise ValueError('Could not verify that the note has no typed body text')
            if any(text.decode('utf-8').replace('\ufffc', '').strip() for text in texts):
                raise ValueError('This note also has typed text; this pen-only exporter would omit it')
        attachment_id, _, account_pk = attachments[0]
        account = connection.execute('SELECT ZIDENTIFIER FROM ZICCLOUDSYNCINGOBJECT WHERE Z_PK=?', (account_pk,)).fetchone()
        if not account:
            raise ValueError('Notes account could not be located')
        path = NOTES / 'Accounts' / account[0] / 'Paper/Bundles' / (attachment_id + '.bundle') / 'Database/data.sqlite3'
        if not path.is_file():
            raise ValueError('The drawing has not been downloaded to this Mac. Open it in Notes and retry.')
        return path


def renderer():
    binary = HERE / 'renderer'
    source = HERE / 'renderer.mm'
    if not binary.exists() or binary.stat().st_mtime < max(source.stat().st_mtime, (HERE / 'Info.plist').stat().st_mtime):
        print('Compiling the local renderer (first run only)...', flush=True)
        subprocess.run(['xcrun', 'clang++', '-std=c++17', '-fobjc-arc', '-framework', 'Foundation',
                        '-framework', 'AppKit', '-framework', 'CoreGraphics', '-framework', 'PencilKit',
                        str(source), '-Wl,-sectcreate,__TEXT,__info_plist,' + str(HERE / 'Info.plist'),
                        '-o', str(binary)], check=True)
    return binary


def export(title, destination, overwrite, note_id=None):
    started = time.perf_counter()
    if destination.exists() and not overwrite:
        raise ValueError('Output already exists: %s (use --overwrite to replace it)' % destination)
    source = locate_note(title, note_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Everything after this snapshot operates on local temporary copies.
    with tempfile.TemporaryDirectory(prefix='apple-notes-export-') as temp:
        temp = Path(temp)
        snapshot = temp / 'drawing.sqlite3'
        # Snapshot source files without requiring SQLite to create a shared-memory
        # sidecar beside the live Notes database. Retry if Notes changes them.
        source_files = [source, Path(str(source) + '-wal')]
        def fingerprint():
            return [(file.stat().st_ino, file.stat().st_size, file.stat().st_mtime_ns)
                    if file.exists() else None for file in source_files]
        for attempt in range(3):
            before = fingerprint()
            for original, signature in zip(source_files, before):
                target = snapshot if original == source else Path(str(snapshot) + '-wal')
                if signature is not None:
                    shutil.copyfile(str(original), str(target))
                elif target.exists():
                    target.unlink()
            if before == fingerprint():
                break
        else:
            raise RuntimeError('Notes changed during the snapshot; wait for editing/sync to finish and retry')
        with sqlite3.connect(str(snapshot)) as check:
            if check.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('The drawing snapshot failed its integrity check')
        drawing = PaperDrawing(snapshot)
        classic = temp / 'original-strokes.drawing'
        stats = drawing.save_classic_drawing(classic)
        print('%s: %d strokes, %d moved strokes, %d erasure masks' %
              (title, stats['strokes'], stats['transforms'], stats['masks']), flush=True)
        # Keep the previous output intact until rendering and coverage checks pass.
        with tempfile.TemporaryDirectory(prefix='.notes-export-', dir=str(destination.parent)) as stage:
            staged = Path(stage) / 'export.pdf'
            result = subprocess.run([str(renderer()), str(classic), str(drawing.width), str(staged), title],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode:
                raise RuntimeError('Renderer failed:\n' + result.stderr + result.stdout)
            summary = [line for line in result.stdout.splitlines() if line.startswith(('Visible strokes', 'Verified'))]
            print('\n'.join(summary), flush=True)
            with staged.open('rb') as stream:
                is_pdf = stream.read(5) == b'%PDF-'
            if not is_pdf:
                raise RuntimeError('Renderer did not produce a PDF')
            os.replace(str(staged), str(destination))
    print('Saved %s (%.1f MB) in %.1f seconds' %
          (destination, destination.stat().st_size / 1e6, time.perf_counter() - started), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('notes', nargs='*', help='Exact note titles; quote names containing spaces')
    parser.add_argument('--output', type=Path, help='Choose the PDF filename and location for one note')
    parser.add_argument('--output-dir', type=Path, default=HERE / 'Exports',
                        help='Folder for exported PDFs (default: Exports beside the script)')
    parser.add_argument('--overwrite', action='store_true',
                        help='Allow replacing an existing output PDF; never changes the source note')
    parser.add_argument('--note-id', help='Select one note UUID from --list; also provide its exact title')
    parser.add_argument('--list', dest='list_notes', action='store_true',
                        help='Show active note IDs, folders, created/updated dates, and titles; newest edits first; optionally filter by exact title')
    args = parser.parse_args()
    if args.list_notes:
        if args.output or args.note_id or args.overwrite:
            parser.error('--list cannot be combined with --output, --note-id, or --overwrite')
        try:
            print_note_list(args.notes)
        except (OSError, ValueError, sqlite3.Error) as error:
            print('ERROR: %s' % error, file=sys.stderr)
            print('If macOS blocked Notes access, grant Full Disk Access to the app running this command, then quit and reopen that app.', file=sys.stderr)
            return 1
        return 0
    titles = args.notes or [input('Apple Notes title: ').strip()]
    if args.note_id and len(titles) != 1:
        parser.error('--note-id requires exactly one note')
    if args.output and len(titles) != 1:
        parser.error('--output requires exactly one note')
    errors = 0
    for title in titles:
        safe = re.sub(r'[/\\:\x00-\x1f]', '_', title).strip() or 'Untitled'
        destination = (args.output or args.output_dir / (safe + '.pdf')).expanduser().resolve()
        try:
            export(title, destination, args.overwrite, args.note_id)
        except (OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.CalledProcessError, RecursionError) as error:
            errors += 1
            print('ERROR: %s' % error, file=sys.stderr)
            if isinstance(error, sqlite3.Error) or (isinstance(error, OSError) and error.errno == 1):
                print('If macOS blocked Notes access, grant Full Disk Access to the host app (usually Terminal, or the app that launched this command), fully quit it, and reopen it.', file=sys.stderr)
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
