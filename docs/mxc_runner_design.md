# MXC runner — design note (verified 2026-06-11, runner NOT yet built)

Microsoft eXecution Containers (**MXC**) is a policy-driven, layered-isolation sandbox for
agent workloads (Windows / WSL / Linux / macOS backends: ProcessContainer, Windows Sandbox,
LXC/Bubblewrap/Seatbelt, MicroVM/Hyperlight, WSLC) behind one JSON config schema. It is a
candidate **second cage backend** beside `docker_runner` (`aurum/aurum/cage/broker.py`).

## Verified facts (do not trust secondhand notes — these were checked)

- The SDK is **TypeScript/Node-only**: `@microsoft/mxc-sdk` on **npm**, Node ≥ 18. API:
  `createConfigFromPolicy` / `spawnSandboxFromConfig` (+ policy helpers).
- **It is NOT on PyPI.** The PyPI package named `mxc` (0.1.0) is **"ProperMX CLI"** by an
  unrelated third party. `pip install mxc` is a supply-chain footgun — never do it, never
  let a script or doc suggest it. The lesson generalises: **verify the artifact, not the
  name** — an alpha package on npm is the same supply-chain surface as the PyPI trap, just
  one ecosystem over (see the integrity-pinning requirement below).
- **Public preview**: Microsoft states schemas/APIs may change before 1.0, generated
  policies are currently **over-permissive in known cases**, and **no MXC profile should be
  treated as a security boundary yet**.
- Outbound **network filtering is Linux-only today; not yet enforced on Windows**.

## The three invariants the runner must preserve (from the existing docker cage)

1. **MountJail stays the single source of truth.** `build_docker_argv` translates the
   jail's allowlist into `-v` flags today; the MXC runner translates the SAME MountJail
   output into the policy config's `filesystem.readonlyPaths` / `readwritePaths`. One
   translation function per backend — never a second security model.
2. **Secrets ride stdin, never config.** The "nothing to steal in the cage" property holds
   because secrets travel inside the stdin `ContainerInput` blob — never argv, never env,
   never `docker inspect`-able. The MXC port must keep secrets OUT of the policy config and
   the process `commandLine`; a careless port that moves a secret into a config field is
   the one way this integration breaks the containment story.
3. **Flag the Windows egress gap loudly.** `CAGE_NETWORK_ENV` already documents egress as a
   deployment decision with no-durable-secret as the backstop; that reasoning ports
   directly. The MXC runner's docstring must state that on Windows
   `network.allowOutbound=false` may not be enforced yet, so **PK's taint/exfil blocking
   (AURUM_ERR_007/009) remains load-bearing there**. This is why Aurum's governance sits
   ABOVE the sandbox rather than treating the sandbox as the whole answer.

## The interesting integration: AG band → MXC isolation tier

MXC exposes a composable isolation spectrum; Aurum already computes the right signal for
choosing a point on it. Map the governed capability class's **live AG band** to the
isolation tier requested for the turn — e.g. a summarisation turn (advisory/readonly band)
runs in a light process container; a code-exec or commit_outward turn (code/full band, or
any tainted turn) requests the heavier backend (Windows Sandbox / MicroVM). A few dozen
lines once the runner exists; the band lookup (`kernel.ag.band(cc)`) and the taint state
are already public surface.

## Build trigger + shape (deliberately deferred)

Build when **either** MXC reaches GA **or** Windows egress enforcement + a stable config
schema land — not before: building against a moving preview API in a different language
runtime buys risk, not security (preview profiles are explicitly not boundaries).

Shape when built: a Python `mxc_runner` beside `docker_runner` that (a) translates
MountJail → policy JSON, (b) invokes a **pinned, vendored Node shim** that calls
`@microsoft/mxc-sdk` (`spawnSandboxFromConfig`), (c) speaks the same stdin
`ContainerInput` / `AURUM_OUTPUT` sentinel contract, (d) sits behind an explicit runner
flag (docker stays the default). MXC's arrival must never be an argument for relaxing any
Aurum enforcement — governance composes with sandboxes; it is not replaced by them.

**Pin the shim's dependency by INTEGRITY HASH, not just version.** A semver pin
(`"@microsoft/mxc-sdk": "0.x.y"`) still trusts the registry to serve the same bytes for
that name+version forever — exactly the trust the PyPI `mxc` squat shows is misplaced, one
ecosystem over. Concretely: commit the shim's `package-lock.json` with its `integrity`
(sha512) fields and install with `npm ci` ONLY (which fails closed on any integrity
mismatch — never `npm install` in CI/deploy); preferably also vendor the SDK tarball and
record its sha512 beside it, so the build needs no registry fetch at all. An alpha-channel
package whose schemas churn before 1.0 is the highest-risk moment for a substituted or
compromised release — the integrity hash is what turns "trust the name" into "verify the
artifact".

Sources: [microsoft/mxc](https://github.com/microsoft/mxc) ·
[@microsoft/mxc-sdk on npm](https://www.npmjs.com/package/@microsoft/mxc-sdk) ·
[Windows Developer Blog — platform security for AI agents](https://blogs.windows.com/windowsdeveloper/2026/06/02/windows-platform-security-for-ai-agents/) ·
[PyPI `mxc` = unrelated ProperMX CLI](https://pypi.org/pypi/mxc/json)
