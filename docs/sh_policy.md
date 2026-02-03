# bash Policy (Blacklist + Maintenance Override)

Theo's `bash` tool runs as an unprivileged user from the vault working directory and enforces a simple, explicit blacklist defined in `vault/policy.json`.

## Policy File

Location: `vault/policy.json`

Keys:
- `kind`: `blacklist` (reserved)
- `blocked_binaries`: array of binary names to block when they are the first program in a command (e.g., `sudo`, `reboot`).
- `blocked_command_patterns`: array of regex patterns applied to the full command string.
- `blocked_read_paths_glob`: array of glob-like paths that should not be read (heuristic tripwire by prefix).
- `blocked_write_paths_glob`: array of glob-like paths that should not be written (heuristic tripwire by prefix).
- `maintenance_override_file`: path of a file that, when present, temporarily disables the protected path checks (read/write), but patterns/binaries remain blocked.

Note: Prefixes beginning with `/vault` are automatically mapped to Theo's actual vault path.

## Execution Model

- Non-interactive execution with timeouts.
- CWD is the vault root; global filesystem access is allowed for normal work.
- Live project root is write-protected (reads allowed); changing directory into it is blocked.
- Environment variables are filtered. `HOME` is set to the vault.
- Preflight policy checks block dangerous commands before running.

## Maintenance Override

Create the file defined by `maintenance_override_file` when you intentionally want to allow commands that reference protected paths (e.g., to update core code under controlled conditions). Remove the file to restore protection.

Examples:

```bash
touch vault/.unlock_core   # temporarily allow protected path access
# ... run maintenance commands ...
rm vault/.unlock_core
```

## Examples

Allowed:
- `pipx install httpie`
- `ripgrep -n "foo" -S`
- `git clone https://... vault/projects/foo && make -C vault/projects/foo`

Blocked (examples):
- `sudo apt-get install ...` → blocked binary `sudo`
- `systemctl stop sshd` → blocked pattern
- `dd if=/dev/zero of=/dev/sda` → blocked pattern
- `rm -rf /` → blocked pattern
- `echo hi > $THEO_PROJECT_ROOT/x` → write-protected project root
