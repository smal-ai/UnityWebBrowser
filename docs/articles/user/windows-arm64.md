# Windows ARM64

This fork can render and interact with HTML inside a native Windows ARM64 Unity
IL2CPP player. Unity runs natively on ARM64. The published Windows x64 CEF engine
runs under Windows emulation. No native ARM64 CEF build is required for this
configuration.

## Tested configuration

| Component | Configuration |
| --- | --- |
| Unity | 6000.3.27f1, Windows ARM64 standalone player |
| Scripting backend | IL2CPP |
| Build | Non-development, Minimal managed stripping |
| UWB | 2.2.8 with this fork's runtime changes |
| CEF | Unmodified published Windows x64 engine package |
| Communication | TCP loopback |
| Graphics | Direct3D11, Microsoft Basic Render Driver |
| Content | Packaged, trusted local HTML fixture |
| CEF sandbox | Explicitly disabled on the browser client |

## Use in a game

1. Follow the normal [package setup](setup.md), including the CEF engine and
   `dev.voltstro.unitywebbrowser.engine.cef.win.x64` packages. Keep their published
   binaries unchanged.
2. Use this fork's core package sources. A registry installation of the original
   2.2.8 core package does not contain these fixes. A source checkout does not
   contain the generated shared DLL either. Build it before adding the core
   package to your game as a local package:

   ```shell
   dotnet build src/VoltstroStudios.UnityWebBrowser.Shared/VoltstroStudios.UnityWebBrowser.Shared.csproj -c ReleaseUnity -f netstandard2.1
   ```

   Add `src/Packages/UnityWebBrowser/package.json` through Unity Package Manager's
   **Add package from disk** command. Keep the checkout available at that path.
3. Configure the normal [Raw Image browser component](usage.md) with its CEF
   engine, TCP communication layer and input handler.
4. Configure your initial local HTML URL and explicitly select `noSandbox` before
   the client is initialised. For a component-managed browser, set the equivalent
   fields in the Inspector, or configure them in `Awake` before its `Start` method
   initialises the client.

   ```csharp
   browserClient.initialUrl = new System.Uri(System.IO.Path.Combine(
       UnityEngine.Application.streamingAssetsPath, "index.html")).AbsoluteUri;
   browserClient.noSandbox = true;
   ```

5. Place `index.html` and its assets under your game's `Assets/StreamingAssets/`.
   Build a Windows ARM64 standalone player with IL2CPP and Minimal managed
   stripping. UWB's normal build postprocessor copies the engine files into
   `<Game>_Data/UWB/`.

> [!WARNING]
> Disabling the sandbox is an explicit requirement of the tested configuration,
> not a new default. Only use this configuration for HTML and scripts you control.
> It does not establish safe browsing of arbitrary untrusted websites.

## Runtime changes

- The Windows adapter uses `CreateProcessW` rather than the unsupported Unity
  process-creation path. It redirects UTF-8 logs, inherits only standard-I/O
  handles and owns the engine's descendants through a kill-on-close Windows job.
- Process logging uses string callbacks. Linux and macOS wrap their existing
  process events without changing their lifecycle implementation.
- Pointer entry before browser readiness keeps the input coroutine alive, so
  typing does not require moving the pointer out and back in after startup.
- A focused Escape still reaches the page and explicitly exits page fullscreen
  when the windowless browser has a fullscreen element.

## Measured Unity-player results

The non-development ARM64 IL2CPP player passed:

- Visible HTML rendering, typing, Return/Escape and scrolling.
- An exact Unity → JavaScript → Unity string-nonce round-trip.
- Browser/window resize.
- Native focus loss and recovery without keystrokes leaking into the page.
- Page fullscreen entry and Escape exit.
- Native fullscreen and windowed restoration.
- Five browser open/close cycles, with no remaining owned engine processes.
- Startup input across four reopens without pointer re-entry after readiness.
- Cancellation before CEF readiness and controlled unexpected-engine-exit detection.

The actual native fullscreen client was 1504×692, matching the test monitor.
Unity's separate internal render buffer remained 800×600.

The [native process probe](../../../tests/WindowsProcessProbe/README.md) provides
repeatable adapter checks without requiring Unity or CEF. It does not replace
testing a real game build.

## Remaining limitations

- Sandbox-enabled emulated x64 CEF failed on the tested ARM64 host. A native-x64
  direct CEF control passed, but that is not a Windows x64 Unity-player result.
- Process cleanup completed, but the five-cycle run logged five socket-reset
  disposal messages and three VoltRpc finaliser `ObjectDisposedException` records.
  These upstream disposal-path defects are not fixed by this change.
- Human Alt+Tab, custom-object callbacks and maximum managed stripping were not
  tested. Native focus recovery and string callbacks were tested separately.
- No changes to CEF binaries, dependency versions, CI or game code are included.
