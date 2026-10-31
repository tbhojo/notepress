# SPDX-License-Identifier: MIT
"""Synthetic-library regression checks. No personal notes are used."""
import gzip
import contextlib
import io
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / 'export_notes.py'
spec = importlib.util.spec_from_file_location('notes_pdf_export', MODULE)
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class NoteLookupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = sqlite3.connect(str(self.root / 'NoteStore.sqlite'))
        self.addCleanup(self.db.close)
        self.db.executescript('''
            CREATE TABLE ZICCLOUDSYNCINGOBJECT (
                Z_PK INTEGER PRIMARY KEY, ZIDENTIFIER TEXT, ZTITLE1 TEXT, ZTITLE2 TEXT,
                ZCREATIONDATE3 REAL, ZMODIFICATIONDATE1 REAL,
                ZMARKEDFORDELETION INTEGER DEFAULT 0, ZFOLDER INTEGER,
                ZFOLDERTYPE INTEGER DEFAULT 0, ZNOTE INTEGER,
                ZTYPEUTI TEXT, ZACCOUNT1 INTEGER);
            CREATE TABLE ZICNOTEDATA (ZNOTE INTEGER, ZDATA BLOB);
            INSERT INTO ZICCLOUDSYNCINGOBJECT (Z_PK,ZIDENTIFIER) VALUES (1,'example-account');
            INSERT INTO ZICCLOUDSYNCINGOBJECT (Z_PK,ZIDENTIFIER,ZTITLE2)
                VALUES (2,'example-folder','Study');
            INSERT INTO ZICCLOUDSYNCINGOBJECT
                (Z_PK,ZIDENTIFIER,ZTITLE1,ZFOLDER,ZCREATIONDATE3,ZMODIFICATIONDATE1)
                VALUES (3,'example-note','Example',2,100000,100000);
            INSERT INTO ZICCLOUDSYNCINGOBJECT (Z_PK,ZIDENTIFIER,ZNOTE,ZTYPEUTI,ZACCOUNT1)
                VALUES (4,'example-drawing',3,'com.apple.paper',1);
        ''')
        self.db.execute('INSERT INTO ZICNOTEDATA VALUES (?,?)',
                        (3, gzip.compress(b'\x12\x08\x1a\x06\x12\x04\xef\xbf\xbc\n')))
        self.db.commit()
        self.drawing = (self.root / 'Accounts/example-account/Paper/Bundles'
                        / 'example-drawing.bundle/Database/data.sqlite3')
        self.drawing.parent.mkdir(parents=True)
        self.drawing.touch()
        self.patcher = patch.object(exporter, 'NOTES', self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_lookup_returns_requested_drawing(self):
        self.assertEqual(exporter.locate_note('Example'), self.drawing)

    def test_recently_deleted_duplicate_is_excluded(self):
        self.db.executescript('''
            INSERT INTO ZICCLOUDSYNCINGOBJECT (Z_PK,ZIDENTIFIER,ZFOLDERTYPE)
                VALUES (10,'TrashFolder-Example',1);
            INSERT INTO ZICCLOUDSYNCINGOBJECT (Z_PK,ZIDENTIFIER,ZTITLE1,ZFOLDER)
                VALUES (11,'deleted-example','Example',10);
        ''')
        self.db.commit()
        self.assertEqual(exporter.locate_note('Example'), self.drawing)

    def test_active_duplicates_require_explicit_choice(self):
        self.db.execute('INSERT INTO ZICCLOUDSYNCINGOBJECT '
                        '(Z_PK,ZIDENTIFIER,ZTITLE1,ZFOLDER) VALUES (5,?,?,2)',
                        ('another-example', 'Example'))
        self.db.commit()
        with self.assertRaisesRegex(ValueError, '--note-id'):
            exporter.locate_note('Example')
        self.assertEqual(exporter.locate_note('Example', 'example-note'), self.drawing)

    def test_typed_text_is_rejected_instead_of_omitted(self):
        self.db.execute('UPDATE ZICNOTEDATA SET ZDATA=? WHERE ZNOTE=3',
                        (gzip.compress(b'\x12\x09\x1a\x07\x12\x05hello'),))
        self.db.commit()
        with self.assertRaisesRegex(ValueError, 'typed text'):
            exporter.locate_note('Example')

    def test_existing_output_is_preserved_before_reading_notes(self):
        destination = self.root / 'existing.pdf'
        destination.write_bytes(b'keep this output')
        with patch.object(exporter, 'locate_note') as lookup:
            with self.assertRaisesRegex(ValueError, 'already exists'):
                exporter.export('Example', destination, False)
            lookup.assert_not_called()
        self.assertEqual(destination.read_bytes(), b'keep this output')

    def test_listing_shows_ids_for_duplicate_titles_and_excludes_trash(self):
        self.db.executescript('''
            INSERT INTO ZICCLOUDSYNCINGOBJECT
                (Z_PK,ZIDENTIFIER,ZTITLE1,ZFOLDER,ZCREATIONDATE3,ZMODIFICATIONDATE1)
                VALUES (5,'second-example','Example',2,200000,300000);
            INSERT INTO ZICCLOUDSYNCINGOBJECT (Z_PK,ZIDENTIFIER,ZFOLDERTYPE)
                VALUES (10,'TrashFolder-Example',1);
            INSERT INTO ZICCLOUDSYNCINGOBJECT (Z_PK,ZIDENTIFIER,ZTITLE1,ZFOLDER)
                VALUES (11,'deleted-example','Example',10);
        ''')
        self.db.commit()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exporter.print_note_list(['Example'])
        text = output.getvalue()
        self.assertIn('NOTE ID', text)
        self.assertIn('example-note', text)
        self.assertIn('second-example', text)
        self.assertIn('Study', text)
        self.assertIn('CREATED (LOCAL)', text)
        self.assertIn('UPDATED (LOCAL)', text)
        self.assertLess(text.index('second-example'), text.index('example-note'))
        self.assertNotIn('deleted-example', text)
        self.assertEqual(exporter.active_notes(['A different title']), [])

    def test_list_command_does_not_prompt_or_export(self):
        output = io.StringIO()
        with patch('sys.argv', ['export_notes.py', '--list', 'Example']), \
                patch('builtins.input', side_effect=AssertionError('must not prompt')), \
                patch.object(exporter, 'export') as export, \
                contextlib.redirect_stdout(output):
            self.assertEqual(exporter.main(), 0)
            export.assert_not_called()
        self.assertIn('example-note', output.getvalue())


if __name__ == '__main__':
    unittest.main()
