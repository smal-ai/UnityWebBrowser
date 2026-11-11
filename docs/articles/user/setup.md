# Setup 

Welcome! To start using UWB in your project, there is first some initial setup that needs to be done first.

## Prerequisites

```
Unity 2021.3.x
```

Newer Unity versions should work, but are untested!

## Platform Support

At a base level, UWB supports all major desktop platforms (Windows, Linux and MacOS). However, different engines may have different platform support. Platform different engines support are listed in the [engines section](engines.md).

> [!WARNING]
> This fork uses a native process launcher for Windows players. Windows ARM64 IL2CPP has been tested with the published x64 CEF engine running under emulation. See [Windows ARM64](windows-arm64.md) for the required configuration and measured results.
>
> Linux and macOS still use `System.Diagnostics.Process`. These changes do not establish IL2CPP support on those platforms. The tested Windows configuration uses Minimal managed stripping, not maximum stripping.

## VoltUPR Setup

VoltUPR is a custom Unity registry that we provide for hosting some of our packages, including all of UWB's packages. To use it, your project needs to be configured to use the VoltUPR registry.

To setup the registry with your project, [see here](https://upr.voltstro.dev/-/web/about). The VoltUPR page also lists some other info that you may be interested in.

**HOWEVER**, an additional scope needs to be added. You need to make sure `com.cysharp.unitask` is added (more details are provided in the [UniTask part](#unitask)). Once you are done configuring your projects registries, your configuration should look like:

![Registry](~/assets/images/articles/user/setup/Registry.webp)

> [!NOTE]
> If you are using [UnityNuGet](https://github.com/bdovaz/UnityNuGet) already, and you choose not to use VoltUPR as a `org.nuget.*` scope mirror, then don't have the `org.nuget` scope defined for VoltUPR.

### UniTask

The reason why we need to add the additional `com.cysharp.unitask` scope to VoltUPR is because UWB depends on [UniTask](https://github.com/Cysharp/UniTask). VoltUPR does provide a mirror copy of UniTask (from OpenUPM),
however you may already have UniTask installed either via [OpenUPM](https://openupm.com/packages/com.cysharp.unitask/), or via [Git](https://github.com/Cysharp/UniTask#install-via-git-url). If you do have it installed already,
and you don't want to use VoltUPR's mirror of it, then DO NOT define the additional scope as apart of VoltUPR.

> [!WARNING]
> If you already have UniTask installed via Git, please make sure it is the latest version!

## Packages Installation

Once you have VoltUPR registry added to your project, you can install the packages via the Unity package manager GUI.

![Packages](~/assets/images/articles/user/setup/Packages.webp)

To know what packages you need, checkout the [packages section](packages.md).

### Standard Loadout

Most developers will probably only need the 'Core' package, and a single engine with it's native Windows binaries. A basic installation might look like this.

![Packages](~/assets/images/articles/user/setup/StandardPackages.webp)

If you plan on providing builds of your game for Linux and MacOS, install their native engine packages as well.

## Usage

Once you have all the required packages that you might need, you can move onto the [usage section](usage.md).
