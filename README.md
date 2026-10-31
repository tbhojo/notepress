# NotePress

Long handwritten Apple Notes can be a pain to get out of Notes. NotePress turns
a supported note into a normal, paginated PDF on your Mac. The handwriting stays
sharp when you zoom in because the PDF contains vector outlines.

It keeps pen colors, moved or scaled handwriting, and erased portions. It also
leaves the page background unpainted, though your PDF app may still show white
pages.

## Why bother?

Handwriting in Notes often lives on one long canvas. Depending on the note and
device, a normal PDF or print export can cut off after the first page, squeeze
everything onto one unreadable page, or flatten the writing into a blurry image.
A large enough note can also fail to export on an iPad altogether.

Splitting it up with the lasso works in a pinch, but it's tedious and easy to
miss a few strokes. NotePress reads the original drawing data and makes the
pages for you, so you can move your handwriting to another notes app or keep a
readable PDF copy.

## Give it a try

You'll need a Mac, Python 3, and Apple's Command Line Tools. If the tools aren't
installed, run `xcode-select --install` and finish Apple's installer first.
There are no Python packages to install. The small native renderer builds on
the first export.

Download or clone this repo, then double-click **Export Notes.command** and enter
the exact note title. PDFs go in the **Exports** folder next to the script.

Or open Terminal in the repo folder:

```sh
python3 export_notes.py "Lecture notes"
python3 export_notes.py "Lecture notes" "Meeting notes"
python3 export_notes.py "Lecture notes" --output ~/Desktop/notes.pdf
```

If macOS blocks access, enable Terminal in **System Settings → Privacy & Security
→ Full Disk Access**, then fully quit and reopen Terminal. If you launched the
script from another app, that app may need the permission instead.

Open the note in Notes first so its drawing has downloaded to this Mac. Pause
editing while it exports. Existing output files are left alone unless you pass
`--overwrite`.

Titles need to be unique among active notes. If there are duplicates, you can
select a known note UUID with `--note-id`. The exporter doesn't guess which copy
you meant.

## What works so far

The tested setup is macOS 15.8.2 with modern `com.apple.paper` drawings. Each note
must contain one pen-only drawing. Colored pen ink, saved transforms, and erasure
masks are supported. Other macOS versions haven't been verified.

Typed text, photos, scans, attached PDFs, multiple drawings, other brushes, and
older drawing formats aren't supported yet. The exporter stops when it finds an
unsupported feature rather than quietly leaving it out.

Pages use US Letter size. Breaks go in gaps where possible; a small overlap keeps
ink visible when a long diagram crosses a break. The result is a PDF you can
import elsewhere, not editable pen strokes in another app's native format.

This uses an undocumented Notes file format and private PencilKit methods.
An OS update could break it. Keep the original note and give the PDF a quick look
before relying on it.

## Your notes stay local

The script reads the selected note's metadata, copies its drawing database into
a temporary folder, and renders that copy. It doesn't edit Notes, call a cloud
service, or make network requests. Temporary copies are removed after the run.
Full Disk Access applies to the host app, not just this script; you can revoke
it when you're done exporting.

The repo includes source only. Exported PDFs, drawing databases, and the compiled
renderer are ignored by Git.

## Hacking on it

`export_notes.py` handles the Notes lookup and drawing conversion. `renderer.mm`
uses the installed Apple frameworks to make vector outlines and choose page
breaks. `Info.plist` gives that command-line helper a bundle identity.

Run the small regression suite with:

```sh
python3 -m unittest discover -s tests
```

The renderer checks that every visible stroke is assigned to an exported page
and reopens the PDF to verify its page count. Those checks don't replace a visual
review of an unfamiliar note.

MIT licensed. Credits and the boundary between project code and Apple framework
code are in [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md). This is an independent
project, not an Apple product.
