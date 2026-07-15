# Pre-built opstat binaries (v0.1.2)

Standalone PyInstaller builds from the `v0.1.2` GitHub Actions release workflow.

| File | Platform |
|------|----------|
| `opstat-linux-x86_64` | Linux x86_64 |
| `opstat-macos-arm64` | macOS Apple Silicon |
| `opstat-windows-x86_64.exe` | Windows x86_64 |

`opstat-macos-x86_64` (Intel Mac) is pending; the `macos-13` builder was still queued when these were published.

```bash
chmod +x opstat-linux-x86_64   # Linux / macOS
./opstat-linux-x86_64 --help
```

Prefer [GitHub Releases](https://github.com/kmacvast/opstat/releases) once the full matrix completes.
