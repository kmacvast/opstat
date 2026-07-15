# Staging area for standalone opstat binaries

Build outputs from `scripts/build.sh`, `scripts/build.bat`, or
`scripts/build_opstat.py` are copied here as:

- `opstat-linux-x86_64`
- `opstat-macos-x86_64` / `opstat-macos-arm64`
- `opstat-windows-x86_64.exe`

CI attaches the same names to GitHub Releases when you push a `v*` tag.
Do not commit large binaries unless you intentionally want them in git history;
prefer Releases for distribution.
