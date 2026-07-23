# opstat binaries

Standalone PyInstaller builds are published on
[GitHub Releases](https://github.com/kmacvast/opstat/releases) by the release
workflow (`.github/workflows/release.yml`) on every version tag.

| File | Platform |
|------|----------|
| `opstat-linux-x86_64` | Linux x86_64 |
| `opstat-macos-arm64` | macOS Apple Silicon |
| `opstat-windows-x86_64.exe` | Windows x86_64 |

Intel macOS (x86_64) binaries are not built.

```bash
chmod +x opstat-linux-x86_64   # Linux / macOS
./opstat-linux-x86_64 --help
```

This directory is only a local staging area for `scripts/build_opstat.py`;
binaries are not committed to the repository.
