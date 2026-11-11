# CI and GitHub releases

The fork's [Build and release workflow](https://github.com/smal-ai/UnityWebBrowser/actions/workflows/main.yml)
builds the native CEF engines and complete Unity packages. It does not need Unity
licence credentials, an npm publishing token, Azure VMs or self-hosted runners.

## Build matrix

| Package | Native architecture | GitHub-hosted runner |
| --- | --- | --- |
| Windows CEF | x64 | `windows-2022` |
| Linux CEF | x64 | `ubuntu-22.04` |
| macOS CEF | Intel x64 | `macos-15-intel` |
| macOS CEF | Apple Silicon ARM64 | `macos-15` |

These are three operating systems and four native-engine targets. Windows ARM64
Unity uses the x64 Windows engine under emulation. This workflow does not claim a
native Windows ARM64 or Linux ARM64 CEF build.

Runner architectures are documented in the [GitHub-hosted runners reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
The .NET 10 SDK builds the managed shared assembly and Native AOT engines.
The pinned CefGlue submodule selects the CEF version. Downloads are checked against
CEF's published SHA-1 sidecars. Final release packages use SHA-256 checksums.

## When it runs

- PRs targeting `main` and pushes to `main` run tests and build all packages.
- **Run workflow** provides a manual build without publishing anything.
- Pushing a version tag such as `v2.2.8-smal.1` builds and publishes a GitHub Release.
  A manual run on a branch does not publish a release.
- Release tags must identify commits already on `main` and match the package
  version. An optional suffix marks a prerelease. Update all seven package
  versions together before releasing a new base version.

After merging the workflow, create a release from the fork's `main`. The following
commands assume `fork` points to `https://github.com/smal-ai/UnityWebBrowser.git`:

```shell
git fetch fork main
git tag -a v2.2.8-smal.1 fork/main -m "Windows, Linux and macOS binaries"
git push fork refs/tags/v2.2.8-smal.1
```

Do not move an existing release tag or replace published assets. Use a new tag for
a changed build. The first example tag is a prerelease because it has a suffix.

## Download and install

Each release contains seven `.tgz` packages:

- `dev.voltstro.unitywebbrowser`: core runtime, including its compiled shared DLL.
- `dev.voltstro.unitywebbrowser.engine.cef`: CEF integration assets and source.
- `dev.voltstro.unitywebbrowser.communication.pipes`: optional pipes support.
- Four `dev.voltstro.unitywebbrowser.engine.cef.*` packages containing the native
  engine, CEF runtime, resources and notices for the target OS/architecture.

Select **Install package from tarball** in Unity Package Manager, then install the
downloaded core, CEF integration and required native packages. Keep the normal
registry configuration for third-party dependencies. See Unity's
[tarball installation instructions](https://docs.unity3d.com/6000.3/Documentation/Manual/upm-ui-tarball.html).
These releases do not publish packages to the upstream VoltUPR registry.

`SHA256SUMS` covers the seven packages and `release-manifest.json`. The manifest
records the source commit, CefGlue revision, CEF version, package versions, binary
architectures, native-file hashes and CLI smoke-test results. On Linux/macOS:

```shell
sha256sum --check SHA256SUMS
```

On macOS without `sha256sum`, use `shasum -a 256 -c SHA256SUMS`. On Windows, compare
`Get-FileHash -Algorithm SHA256 <file>` with the corresponding entry.

The tarballs retain executable permissions and macOS framework symlinks. They
are reproducibly packaged with normalised archive timestamps, not re-zipped file
trees with lost application-bundle metadata.

## Publication safeguards

All four engine builds, shared-code tests and packaging checks must pass. The
release starts as a draft and is published only after all nine assets are uploaded
and their server-side SHA-256 digests match. Build jobs have read-only repository
permissions. Only the tag-publication job has `contents: write`.

Retries verify and retain a complete existing release. Missing assets can be added
to a matching draft, but different existing assets are never overwritten. An
uncertain upload is inspected on retry, not automatically replaced.

## What CI does not certify

CI checks native architectures, required runtime files, CLI startup and the Windows
process adapter. It does not build a Unity player or prove desktop rendering on
each platform. The measured Windows ARM64 game-rendering profile is documented
[separately](../user/windows-arm64.md).

No Windows signing certificate or Apple Developer ID is configured. macOS builds
are not notarised. Applications shipping these binaries still need their own
signing, notarisation and content-security decisions.
