# Aurum home directory (`~/.aurum`)

Aurum stores its state under `~/.aurum` instead of Hermes' default `~/.hermes`. We do this
with the **supported `HERMES_HOME` override**, not by forking Hermes' 600+ `.hermes` literals —
the code default stays `~/.hermes`, so Hermes' test suite remains green, and the runtime home
is `~/.aurum` because the env var points there.

`hermes_constants.get_hermes_home()` resolves, in order: a context override → `HERMES_HOME`
env → `~/.hermes`. Setting `HERMES_HOME=~/.aurum` makes every host process (gateway, cli, the
cage broker) use `~/.aurum`.

## Set it once (no per-launch footgun)

**Windows (persistent, all future processes):**
```
setx HERMES_HOME "%USERPROFILE%\.aurum"
```
Open a new shell afterwards so the value is picked up.

**macOS / Linux (shell profile):**
```
echo 'export HERMES_HOME="$HOME/.aurum"' >> ~/.profile   # or ~/.zshrc / ~/.bashrc
```

A login start-script (the one that launches the gateway + `python -m aurum.cage.broker`)
should also `export HERMES_HOME` so the daemons inherit it regardless of how they're started.

## Migrate existing state (one-time)

If you have an existing `~/.hermes` (auth, memories, cron, hooks), move it once so the new
home has your state — do this when **no** Hermes/gateway process is holding it:
```
mv ~/.hermes ~/.aurum        # PowerShell: Move-Item $env:USERPROFILE\.hermes $env:USERPROFILE\.aurum
```

## Note on the in-container home

The cage's per-group state dir is set explicitly by `container/aurum/entrypoint.py`
(`HERMES_HOME=/workspace/group/.hermes`) and is independent of the host home — it is not
affected by this override and is intentionally left as-is.
