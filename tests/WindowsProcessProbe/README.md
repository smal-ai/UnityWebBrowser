# Windows process regression probe

This dependency-free .NET 10 executable links the actual `IProcess.cs` and
`WindowProcess.cs` runtime sources. It does not need Unity, CEF, a game project or
administrator permissions.

## Run

From the repository root on 64-bit Windows with the .NET 10 SDK:

```powershell
dotnet run --project tests/WindowsProcessProbe/WindowsProcessProbe.csproj -c Release
```

Each run uses a new temporary directory and prints JSON Lines results. An optional
first argument selects a different output directory, which must not already exist.
The probe only terminates processes that it creates. Outputs are retained for
inspection rather than deleting an existing directory on a repeated run.

The eight checks cover:

1. Win32 structure layouts.
2. Redirected stdout/stderr, split UTF-8 output, EOF and exit-code propagation.
3. Quoted Unicode arguments.
4. A controlled loopback handshake.
5. Failed startup and repeated disposal.
6. Cancellation before startup.
7. Job-owned descendant cleanup and repeated kill.
8. Descendant cleanup after a natural parent exit.

## Native ARM64 executable

On a Windows build machine with the .NET 10 Native AOT prerequisites installed:

```powershell
dotnet publish tests/WindowsProcessProbe/WindowsProcessProbe.csproj -c Release -r win-arm64 --self-contained true -p:PublishAot=true -o artifacts/windows-process-probe-arm64
```

Copy that output directory to the ARM64 Windows machine, then run:

```powershell
.\artifacts\windows-process-probe-arm64\WindowsProcessProbe.exe
```

The native adapter passed all eight checks in a Windows ARM64 Native AOT run.
This is a process-adapter test, not a Unity IL2CPP rendering test. The separate
[Windows ARM64 guide](../../docs/articles/user/windows-arm64.md) records the
actual Unity-player results.

On a non-Windows 64-bit host, the probe only builds and checks structure sizes. It
explicitly reports Windows process execution as `NOT RUN`.
