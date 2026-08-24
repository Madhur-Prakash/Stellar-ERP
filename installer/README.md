<div align="center">

# Windows installer

**Packages the Flutter desktop client into a single `PersonalERP-Setup.exe`.**

![Inno Setup](https://img.shields.io/badge/Inno_Setup-6.3_or_newer-2D6099?style=flat-square)
![Architecture](https://img.shields.io/badge/x64-only-6E7681?style=flat-square)
![Scope](https://img.shields.io/badge/install-per--user_by_default-4C8BF5?style=flat-square)

[Desktop client](../app_frontend/README.md) · [Root README](../README.md)

</div>

---

## Prerequisites

**1. Inno Setup 6.3 or newer** - <https://jrsoftware.org/isdl.php>

When installing Inno Setup, **leave "Install Inno Setup Preprocessor" checked**. It is
on by default, and it is the one option that matters: [`personal-erp.iss`](personal-erp.iss)
uses `#define` and `#if`, and without the preprocessor it will not compile.

6.3 is the floor because the script uses `ArchitecturesAllowed=x64compatible`. On an
older 6.x, change that line and the one below it to `x64`.

**2. A release build of the app**

```powershell
cd app_frontend
flutter build windows --release
```

The script refuses to compile if that output is missing, rather than producing an empty
installer that only fails on the machine you were installing it on.

**3. `VC_redist.x64.exe`, beside this file**

```bash
make installer-deps
```

Or download <https://aka.ms/vs/17/release/VC_redist.x64.exe> into `installer\` by hand -
about 25 MB, fetched once, and it stays there for every future build.

**It is deliberately not committed.** Git would carry those 25 MB forever, every future
version of the redistributable would add another 25 MB that cannot be removed without
rewriting history, and a committed copy goes stale while the `aka.ms` link always serves
the current one. So a fresh clone fetches it, and
[`personal-erp.iss`](personal-erp.iss) **fails the compile** with that URL in the message
rather than quietly building an installer without it - see
[Why the runtime is bundled](#why-the-runtime-is-bundled) for what that would cost.

---

## Building the installer

Either open [`personal-erp.iss`](personal-erp.iss) in the Inno Setup Compiler and press
**F9** (Build → Compile), or from the repository root:

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\personal-erp.iss
```

The result lands in `installer\dist\`. There are no options to answer while compiling -
every decision is in the script, so two builds of the same commit are identical.

---

## What the script already decides

| | |
| --- | --- |
| **Install scope** | Per-user by default (`%LocalAppData%\Programs\Personal ERP`), no UAC prompt. The wizard offers "for all users" for anyone who wants Program Files. The app writes nothing to its own folder, so it does not need admin. |
| **Architecture** | x64 only, matching what Flutter builds. A 32-bit or ARM machine is refused up front rather than installing something that cannot start. |
| **Payload** | The entire `Release` folder: the `.exe`, `flutter_windows.dll`, plugin DLLs, and `data\`. All of it is required - without `data\` the app exits silently. |
| **Upgrades** | Installing a newer version replaces the current one in place, and a running copy is closed first rather than failing the copy. |
| **Shortcuts** | Start menu always; desktop shortcut offered **unchecked**. |
| **Uninstall** | Standard entry in Add/Remove Programs. User preferences in `%AppData%` are deliberately left behind, so reinstalling does not lose settings. |

---

## What the person running the installer sees

Four screens: install-for-me-or-everyone, destination folder, the desktop-icon
checkbox, then install. Nothing needs to be typed.

**On some machines, one extra prompt appears** - a Windows UAC dialog part-way through,
while the status line reads *"Installing Visual C++ runtime…"*. It is expected, and this
is the whole story:

| | |
| --- | --- |
| **When it appears** | Only when the machine does not already have the MSVC 2015-2022 runtime. The script checks the registry first (`VCRedistInstalled`) and skips the step entirely otherwise - so most people never see it |
| **Why it needs admin** | The runtime is installed machine-wide, into `System32`. Our own install is per-user by default and needs no elevation; this one step does, which is why the prompt arrives mid-install rather than at the start |
| **What it is installing** | Microsoft's `vc_redist.x64.exe`, shipped inside the setup file. It runs silently and does not reboot |
| **If it is declined** | The install finishes and the app appears in the Start menu, but **it will not start** on that machine - it needs the runtime. Re-run the installer and accept, or install the runtime by hand from <https://aka.ms/vs/17/release/vc_redist.x64.exe> |

Nothing appears while *compiling* the installer - the prompt belongs to installation, on
the end user's machine, not to the build.

---

## Why the runtime is bundled

A Flutter release build is compiled with MSVC and links against its runtime -
`msvcp140.dll`, `vcruntime140.dll`, `vcruntime140_1.dll`. Look in the build folder and
they are not there: the executable declares them and expects Windows to have them.

Practically every Windows 10/11 machine does, because hundreds of applications install
it. But a freshly imaged laptop, a locked-down corporate build, a clean VM or a Server
install often does not - and the failure gives nobody anything to work with:

> The installer runs perfectly. The user double-clicks Personal ERP. **Nothing happens.**
> No window, no error, no message. They click again. Still nothing.

Windows cannot resolve the imports, so it kills the process before a single line of our
code runs - which means the app never gets the chance to say anything. It reads as a
broken build, and the report is "it doesn't open", with nothing to go on at either end.

Bundling turns "works on most computers" into "works on all of them". The cost is honest
and small: the setup file grows by roughly 25 MB, and machines that lack the runtime see
the one extra prompt described above.

---

## Two things worth doing before you hand this to anyone

**1. Code signing.** An unsigned installer triggers a SmartScreen "Windows protected
your PC" warning, and most people stop there. If you have a certificate:

```powershell
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 installer\dist\PersonalERP-Setup.exe
```

Sign `personalerp_desktop.exe` before compiling as well, so the warning does not simply
reappear on first launch.

**2. Check the API URL.** It is baked into the build, not read at runtime - see
[The API URL is baked in](#the-api-url-is-baked-in) below. Getting this wrong ships a
client that points at a machine the user does not have.

*(The Visual C++ runtime used to be a decision here. It is not any more - it ships in
the installer. See [Why the runtime is bundled](#why-the-runtime-is-bundled).)*

---

## The API URL is baked in

`app_frontend/.env` is bundled as a Flutter **asset**, so whatever it contains at build
time is compiled into the installer:

```
API_BASE_URL=https://erp.yourdomain.com
```

Check that before building a release, because it is not a runtime setting - pointing an
installed copy somewhere else means editing
`data\flutter_assets\.env` inside the installation directory, which is a plain text file
but hardly a supported workflow. If you need per-customer endpoints, build one installer
per endpoint.

---

## Versioning

The version appears in three places and they should agree:

| Where | Value |
| --- | --- |
| `app_frontend/pubspec.yaml` | `version: 1.0.0+1` |
| `installer/personal-erp.iss` | `#define AppVersion "1.0.0"` |
| The installed app | reports `AppVersion` in Add/Remove Programs and in the file's Properties |

The output filename is deliberately **not** versioned - it is always
`PersonalERP-Setup.exe`, so a download link never needs updating. Each build therefore
replaces the previous one; archive it first if you need to keep a specific release.

`AppId` must **never** change between versions - it is the identity Windows tracks the
installation under, and changing it makes the next release install alongside this one
instead of upgrading it.
