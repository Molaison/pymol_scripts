# Installable plugin package validation

## Scope and acceptance

- `just` and `just package` build the current working tree into
  `dist/PymolPlugins-<version>.zip` using Python 3's standard library.
- The ZIP has one `pymol_plugins/` package containing its Plugin Manager entry,
  version metadata, canonical runtime, thin plugin entries, and instructions.
- Installation and loading must work outside the source repository.
- Validation covers archive contents, isolated loading, and the available
  Windows PyMOL installation. It does not replace feature or visual acceptance.

## Initial defect

The draft builder put the bootstrap `__init__.py` at the archive root.
Windows PyMOL 3.1.3.1's `pymol.plugins.installation.extract_zipfile` selects a
directory containing `__init__.py`, so it selected the inner `pymolplugins/`
runtime instead. The bootstrap and menu initializer were omitted from the
installed plugin. A stable outer `pymol_plugins/` directory preserves both.

## Validation

Both `just` and `just package` completed successfully. The final artifact is
`dist/PymolPlugins-0.2.0.zip`, with 78 files and 351,631 bytes. Its SHA-256 is
`8929f0246155ccaf52fcd81b559c11004082512091a54ed1cfc1468edb8502dc`.
The ZIP contains one top-level `pymol_plugins/` directory.

### WSL

```bash
python3 -m unittest discover -s tests/architecture -v
git diff --check
```

All 20 architecture/packaging tests passed on Python 3.13.5 in 2.473 seconds.
The new packaging checks build through the CLI into a temporary directory,
compare bundled Python files with the working tree, and import the extracted
bundle in an isolated `python -I` process outside the repository. They verify
command registration and menu callbacks with a fake PyMOL API. The whitespace
check also passed.

### Windows PyMOL

From `/mnt/c`, the exact invocation was:

```bash
/mnt/d/Softwares/Pymol3.1/python.exe -I \
  '\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins\tests\host\plugin_package_smoke.py' \
  '\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins\dist\PymolPlugins-0.2.0.zip' \
  --output '\\wsl.localhost\Debian\home\admin\gitrepo\PymolPlugins\output\plugin-package-host.json'
```

The process exited successfully under Windows Python 3.10.16 and PyMOL
3.1.3.1. The real `installPluginFromFile` installed the ZIP into a temporary
Windows-local plugin directory and reported success. The installed package
reported version 0.2.0 and loaded all 11 canonical modules, 71 canonical
commands, and 8 compatibility commands with `status=ok`. All five toolkit
menu items were created in real Qt menu widgets through PyMOL's menu adapter.
Structure Annotator imported successfully from the installed bundle.

Machine-readable evidence is in `output/plugin-package-host.json` (generated,
git-ignored). The harness uses the real installer, command API, and Qt menu
adapter with a temporary GUI container and captured installation dialogs.
It does not launch the full PyMOL desktop or perform manual scene/rendering
or annotation-panel acceptance. User plugin preferences are not saved, and
the temporary installation is removed after the check.

## Outcome

Packaging and independent installation checks passed. The deliverable is
ready to select directly in Plugin Manager; installation does not need a
source checkout. No packaging blockers remain.
