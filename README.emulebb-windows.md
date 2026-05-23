# eMuleBB Windows upnpc packaging

This fork publishes repeatable Windows packages for the MiniUPnP `upnpc`
command-line client.

Build a release package from the repository root:

```powershell
python tools\package_windows.py --platform x64 --clean --require-clean
python tools\package_windows.py --platform ARM64 --clean --require-clean
```

Use `--output-root <path>` to place artifacts outside the repository, for
example under the eMuleBB workspace `state\release` directory.

Each package contains:

- `upnpc.exe`
- `README.txt`
- `LICENSE-MiniUPnP.txt`
- `MANIFEST.json`

The script also writes sidecar `.manifest.json` and `.sha256.txt` files next to
the ZIP. The executable is built with the Visual Studio 2022 toolset and static
MSVC runtime for `x64` and `ARM64`.
