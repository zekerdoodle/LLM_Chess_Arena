# bash Tool: Pure Blacklist Security

The bash tool now uses **pure blacklist security** for maximum flexibility while maintaining essential safety guardrails.

## How It Works

### Pure Blacklist Approach
- **All commands are allowed** by default
- **Only dangerous patterns are blocked** via regex blacklist
- **Maximum flexibility** for AI assistant operations
- **Sudo operations** still require explicit allowlisting (higher privilege)

### What Changed
- ❌ **Removed allowlist dependency** - no more "command not allowed" errors
- ✅ **Kept robust blacklist** - dangerous operations still blocked
- ✅ **Maintained sudo controls** - elevated privileges still restricted
- ✅ **All safety features preserved** - timeouts, environment filtering, sandboxing

## Configuration

Current `config.yaml` setup:

```yaml
tools:
  bash:
    # Pure blacklist security + write-protected project root
    cwd: vault                  # Sandboxed execution directory
    timeout_seconds: 45         # Command timeout protection
    
    # Legacy allowlist (kept for backward compatibility but not enforced)
    allowlist: [...]  # No longer restricts commands
    
    # Sudo operations always require explicit allowlisting
    sudo_allowlist:
      - systemctl
      - docker
      - apt-get
      
    # Blacklist patterns (core restrictions)
    blocklist:
      - "re:\\bshutdown\\b"      # Block system shutdown
      - "re:\\breboot\\b"        # Block system reboot  
      - "re:rm\\s+-rf\\s+/"      # Block dangerous file deletion
      - "re:mkfs\\w*\\b"         # Block filesystem formatting
      # ... more safety patterns
```

## What This Enables

Theo can now freely use commands like:
```bash
# System diagnostics
whoami, hostname, uname -a, uptime, free -h, df -h

# Process management  
ps aux, top, htop, pgrep, pkill

# Network tools
ping, curl, wget, netstat, ss, nmap

# Development tools
git, make, cmake, npm, pip, cargo, go

# Text processing
awk, sed, perl, sort, uniq, cut, paste

# System exploration
find, locate, which, whereis, ldd, file

# And thousands more...
```

While still being protected from:
```bash
# Dangerous operations (blocked)
reboot, shutdown, poweroff
rm -rf /, rm -rf *
mkfs.*, dd if=/dev/zero of=/dev/sd*
insmod, rmmod, modprobe
```

## Security Model

1. **Execution Sandbox**: All commands run in `vault/` directory
2. **Timeout Protection**: Commands automatically killed after timeout
3. **Environment Filtering**: Only safe environment variables passed through
4. **Regex Blacklist**: Pattern matching blocks dangerous command sequences
5. **Sudo Restrictions**: Elevated privileges require explicit allowlisting
6. **Non-Interactive**: No TTY allocation prevents interactive exploits

## Benefits

- 🚀 **Maximum Flexibility**: Theo can use any safe system tool
- 🛡️ **Maintained Safety**: Dangerous operations still blocked
- 📈 **Better Productivity**: No more "command not allowed" friction
- 🔧 **Future-Proof**: New tools work automatically without config updates
- 🎯 **Focused Security**: Energy spent on actual threats, not false positives

The pure blacklist approach trusts that most commands are harmless and focuses security efforts on blocking genuinely dangerous patterns. This provides the flexibility Theo needs while maintaining robust protection against system damage.
