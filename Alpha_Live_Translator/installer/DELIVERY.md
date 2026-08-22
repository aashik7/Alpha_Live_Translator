# Handing the installer over without the SmartScreen screen

The operator running the installer saw:

> **Windows protected your PC**
> Microsoft Defender SmartScreen prevented an unrecognized app from starting.
> Running this app might put your PC at risk.

This is not a defect in the installer, and nothing inside the `.iss` can turn
it off. It is worth being precise about what actually triggers it, because the
two cheap-sounding "fixes" for it do not work.

## What actually triggers it

Explorer runs SmartScreen's application-reputation check when **both** of these
are true:

1. **The file carries a Mark of the Web.** Whatever downloaded it — a browser,
   Outlook, Teams, OneDrive — writes an alternate data stream named
   `Zone.Identifier` containing `ZoneId=3` ("came from the internet"). A file
   copied from a USB stick or a network share never gets one.
2. **The file has no code signature with reputation.** Microsoft scores the
   signing certificate, and secondarily the file hash, against its own
   reputation service.

Measured on the build machine:

| check | result |
| --- | --- |
| `Get-AuthenticodeSignature` on the 1.0.1 installer | `NotSigned` |
| streams on the freshly built file | `:$DATA` only — no Mark of the Web |
| after writing `Zone.Identifier` / `ZoneId=3` | tag present |
| after `Unblock-File` | tag gone |
| `signtool.exe` on the build machine | not installed |

So the freshly built file is clean; the copy that reached the other PC picked
up the mark in transit. Break **either** condition and the screen is gone.

## Two things that do not work

- **A self-signed certificate.** SmartScreen scores the publisher against
  Microsoft's reputation service, not against the local trust store. Signing
  with a certificate nobody has ever seen leaves the reputation at zero, and
  installing a root certificate on the target machine is exactly the sort of
  step this hand-over was supposed to avoid.
- **Version metadata.** Filling in company, product and copyright does not
  change the screen. It is worth doing anyway — see below — but not for this.

## Route A — deliver without a Mark of the Web (free, works today)

Put `AlphaLiveTranslator-Setup-<version>.exe` on a **USB stick** or a **shared
network folder** and copy it across from there. No mark is written, so
SmartScreen never runs and the operator sees Next / Next / Install only.

If the file has to travel by email or chat, the operator can clear the mark in
one step after downloading it:

- right-click the `.exe` → **Properties** → tick **Unblock** → OK

or, if it has already been zipped, unblock the **zip** first and then extract:
files extracted from an unblocked archive inherit no mark. Note that in Windows
11 extracting a *still-blocked* zip propagates the mark to everything inside
it, so the order matters.

`README-INSTALL.txt` is written next to the installer by
`installer/build_installer.py` and says all of this in operator language.

## Route B — buy a code-signing certificate (the real fix)

This is the only thing that removes the screen for a file that arrived over the
internet, on any machine, without the operator doing anything.

- An **OV** certificate is the cheap tier, but SmartScreen reputation on it
  builds up over downloads and time, so early copies can still be flagged.
- An **EV** certificate, or Microsoft's **Azure Trusted Signing**, is
  recognised immediately.
- Either way the issuer has to validate the organisation's identity first,
  which takes days, not hours. Confirm current pricing and turnaround with the
  issuer before committing to a date — do not plan a delivery around it
  otherwise.

The build is already wired for it. Put the command in the git-ignored
`installer/keys.local.ini`:

```ini
[signing]
command = "C:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x64\signtool.exe" sign /fd sha256 /tr http://timestamp.digicert.com /td sha256 /f "C:\certs\alpha.pfx" /p <password> $f
```

`$f` is where Inno Setup substitutes the file being signed and the build
refuses to start without it, because a missing placeholder makes Inno sign
nothing and report success. `signtool.exe` comes with the Windows SDK and is
not currently installed on the build machine.

One caveat to know before putting a password in there: the command reaches Inno
Setup as a `/S` argument, so for the length of the compile it is visible in the
build machine's process list — the same exposure the API keys already have.
Prefer a certificate in the Windows certificate store (`/n "<subject>"`) or a
cloud-signing provider over a `.pfx` with `/p <password>` on the command line.

With that present the build passes `/DSignCommand=1`, which turns on both
`SignTool` and `SignedUninstaller=yes` in `alpha.iss` — the uninstaller is
generated at install time and needs signing too, otherwise Windows warns again
when the operator eventually removes the app. The signing command can contain a
certificate password, so it is filtered out of Inno's output the same way the
API keys are.

## What changed in the build regardless

The 1.0.1 installer shipped with an **empty `FileVersion` and copyright** —
read back out of its version resource, not guessed. Inno defaults the company
from `AppPublisher` and the product name from `AppName`, but leaves those two
blank unless `VersionInfoVersion` is given a strictly numeric four-part
version, which `AppVersion` ("1.0.1") is not.

That is now derived by `version_quad()`, passed as `/DVersionQuad`, and the
build **reads the resource back out of the finished exe and fails if it did not
take**. A half-empty version resource is a long-standing antivirus heuristic,
and Properties → Details is the one place the operator can check what they have
been handed.

Fixed in the same pass: the build used to report `sorted(glob(...))[-1]` as
"the installer", so rebuilding 1.0.0 while 1.0.1 sat in the output folder would
have pointed at the older file — handing over the wrong build with a success
message.
