# Credits and implementation notes

The Python reader, conversion code, command launcher, and Objective-C++ export
helper were developed for this project with AI assistance. No third-party source
code or example notes are vendored in this repository.

## Apple frameworks

Rendering comes from the copy of PencilKit, AppKit, Foundation, and Core Graphics
already installed with macOS. Apple's documentation for
[PKDrawing](https://developer.apple.com/documentation/pencilkit/pkdrawing-swift.struct)
and [PKStroke](https://developer.apple.com/documentation/pencilkit/pkstroke-swift.struct)
was used as a reference. These frameworks and the SDK headers are Apple's code;
the project's MIT license does not relicense or redistribute them.

The helper also calls private PencilKit selectors, including `newOutlinePaths`,
`_isHidden`, and ink-behavior methods. Its small `OutlinePoint`/container declaration
matches an observed outline-container layout for interoperability. It is not an
implementation of Apple's renderer or of a clipping library. No PencilKit binary,
Apple SDK header, or Clipper implementation is included.

Apple, macOS, Apple Notes, and PencilKit are names associated with Apple Inc.
Their mention describes compatibility, not affiliation or endorsement.

## Background reading

[sweetrb/apple-notes-mcp's technical notes](https://github.com/sweetrb/apple-notes-mcp/blob/main/TECHNICAL_NOTES.md)
helped with the Notes storage conventions and the distinction between classic
PencilKit and newer Paper drawings. That project's converter was not used or
copied. The Paper record traversal, saved-transform handling, and mask conversion
here were worked out against local drawing data.

The outline simplifier is a local implementation of the Ramer–Douglas–Peucker
polyline-simplification approach. It does not incorporate a third-party geometry
library. The native outline extraction itself remains Apple's implementation.

The MIT license text uses the standard
[SPDX MIT template](https://spdx.org/licenses/MIT.html). Python, SQLite, Apple's
compiler, and system frameworks are runtime/build dependencies rather than
bundled source. Their own licenses continue to apply to those components.
