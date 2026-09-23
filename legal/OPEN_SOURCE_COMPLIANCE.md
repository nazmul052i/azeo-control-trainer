# Open-source component compliance

The Windows distribution uses the community build of Qt for Python and selects
the **LGPL-3.0-only** licensing option for PySide6 Essentials and Shiboken6.
It does not bundle PySide6 Addons or Qt WebEngine. Other dependency licences
are listed, with exact versions and retained licence paths, in the generated
`THIRD_PARTY_NOTICES.md` beside this file.

## Replacement and debugging

Qt and the other shared libraries are dynamically loaded from
`runtime/Lib/site-packages`. You may replace the LGPL-covered DLL and `.pyd`
files there with compatible modified builds for your own use. No Azeo licence
term prohibits reverse engineering when it is necessary to debug a change to
an LGPL-covered component. The portable ZIP is the supported format for making
such replacements; verify that the replacement matches the packaged Python,
Windows architecture and Qt ABI.

## Corresponding source

The exact component versions are recorded in `portable-manifest.json`.
Corresponding source is available as follows:

* Qt and Qt for Python: use the matching release tag/archive from
  <https://download.qt.io/official_releases/> and
  <https://code.qt.io/cgit/pyside/pyside-setup.git/>.
* Python dependencies: download the source distribution matching the recorded
  version from <https://pypi.org/>. For example,
  `python -m pip download --no-binary=:all: NAME==VERSION`.

For at least three years after an Azeo binary release, any recipient may also
request the exact corresponding source for an LGPL-covered component, at no
charge other than reasonable physical distribution costs, by opening a source
request at <https://github.com/nazmul052i/azeo-control-trainer/issues>. Include
the Azeo version, build identifier, component name and component version from
the manifest.

Copies of the GNU GPL version 3 and GNU LGPL version 3 accompany the
distribution in this directory. Nothing in this notice changes or limits the
rights granted by those licences.
