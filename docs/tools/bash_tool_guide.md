# Bash Tool Usage Guide

## Overview

The `bash` tool allows you to execute shell commands in an isolated, security-controlled environment. This guide covers best practices, path accessibility, and common patterns.

## Security Model

The bash tool uses a **blacklist-based security model** with the following protections:

- **Blocked Commands**: Dangerous binaries (rm -rf, dd, mkfs, etc.) are blocked
- **Protected Paths**: Critical system directories are read-only
- **Timeout Enforcement**: Commands are automatically terminated if they exceed time limits
- **Environment Isolation**: Only allowlisted environment variables are passed through

## Path Accessibility

### ✅ Writable Paths

1. **`/tmp/`** - Temporary files
   - Use for short-lived scratch files
   - System automatically cleans this directory
   - Example: `/tmp/my_temp_file.txt`

2. **`{vault_root}/clones/clone_YYYYMMDD_HHMMSS/`** - Code workspaces
   - Safe experimentation area for code modifications
   - Each clone is an isolated copy of the project
   - Example: `vault/backups/clones/clone_20251020_180434/test.py`

3. **Current Working Directory** - Typically vault root
   - Default CWD is configured in `config.yaml` under `tools.bash.cwd`
   - Usually points to the vault directory
   - Files created here are persistent

### ❌ Read-Only / Restricted Paths

1. **Project Root** - Theo's core codebase
   - Contains layer1_chatbot/, layer4_tools/, etc.
   - Use code tools (read_code_structure, create_diff, etc.) instead
   - Direct modification blocked for safety

2. **System Directories** - `/etc`, `/bin`, `/usr`, `/sys`, etc.
   - Critical system files
   - Modifications blocked by security policy

## Common Use Cases

### 1. Quick Data Processing

```bash
# Process JSON data
echo '{"key": "value"}' | jq '.key'

# Count lines in a file
wc -l somefile.txt

# Search for patterns
grep -r "function_name" clones/clone_20251020_180434/
```

### 2. File Operations in /tmp

```bash
# Create temporary file
echo "test data" > /tmp/test_output.txt

# Process and move
cat /tmp/input.txt | sort | uniq > /tmp/output.txt
```

### 3. Environment Inspection

```bash
# Check current directory
pwd

# List files
ls -la

# Check available disk space
df -h
```

### 4. Working with Clones

```bash
# Run tests in a clone
cd clones/clone_20251020_180434 && python -m pytest tests/

# Check file structure
find clones/clone_20251020_180434/layer4_tools -name "*.py"

# Count lines of code
find clones/clone_20251020_180434 -name "*.py" -exec wc -l {} + | tail -1
```

## Best Practices

### ✅ DO

- Use `/tmp/` for temporary computations
- Work in clone directories for code experiments
- Use relative paths when working in vault
- Check exit codes and stderr for error handling
- Keep commands focused and single-purpose

### ❌ DON'T

- Don't try to modify the live project root directly
- Don't use blocked commands (rm -rf, dd, mkfs, etc.)
- Don't rely on persistent state in `/tmp/`
- Don't create long-running background processes
- Don't hardcode absolute paths (use relative or environment variables)

## Error Handling

### Common Errors

1. **"Permission denied"**
   - You're trying to write to a read-only path
   - Solution: Use `/tmp/` or a clone directory

2. **"Command not found"**
   - Binary is not in PATH or doesn't exist
   - Solution: Check if the command is available with `which command_name`

3. **"No such file or directory"**
   - Path doesn't exist
   - Solution: Use `pwd` to check current directory, `ls` to list files

4. **"Command failed (exit N)"**
   - Command executed but returned non-zero exit code
   - Check stderr output in tool response for details

## Advanced Features

### Environment Variables

Pass custom environment variables (must be in allowlist):

```python
bash(
    command="echo $MY_VAR",
    env={"MY_VAR": "test_value"}
)
```

### Standard Input

Pipe data into commands:

```python
bash(
    command="grep 'pattern'",
    stdin="line1\nline2\nline3"
)
```

### Timeout Control

Set custom timeout (default is system-configured):

```python
bash(
    command="sleep 5 && echo done",
    timeout=10000  # milliseconds
)
```

## Integration with Other Tools

### Bash + Code Tools

```bash
# 1. Create a clone
create_clone()

# 2. Make modifications in clone via bash
bash("echo 'print(42)' >> clones/clone_20251020_180434/test.py")

# 3. Review with code tools
read_code_structure("clones/clone_20251020_180434/test.py")

# 4. Create diff and preview
create_diff("clones/clone_20251020_180434/test.py", "...")
```

### Bash + File Tools

```bash
# 1. Generate data with bash
bash("echo 'data,value\n1,100\n2,200' > /tmp/report.csv")

# 2. Send to user
send_file(filename="report.csv", path="/tmp/report.csv")
```

## Security Notes

The bash tool is designed with Theo's autonomy in mind while maintaining safety:

- **Intentional flexibility**: You have broad command access within safe boundaries
- **Audit trail**: All commands are logged with execution time and output
- **Fail-safe defaults**: Unknown/dangerous patterns are blocked
- **Project protection**: Core codebase modifications require explicit code tools

## Troubleshooting

### How do I persist data?

Use vault-relative paths or the send_file tool:

```bash
# Option 1: Write to vault (check CWD first)
bash("pwd")  # Typically vault root
bash("echo 'data' > my_file.txt")

# Option 2: Use send_file tool
bash("echo 'data' > /tmp/my_file.txt")
send_file(filename="my_file.txt", path="/tmp/my_file.txt")
```

### Where does bash execute by default?

Check your current working directory:

```bash
bash("pwd")
```

This is typically configured to the vault root, but can be customized in `config.yaml`.

### Can I run Python scripts?

Yes, but use the project's Python interpreter:

```bash
# Check available Python
bash("which python python3")

# Run script in clone
bash("cd clones/clone_20251020_180434 && python -m pytest")
```

## Related Tools

- **Code Tools**: `read_code_structure`, `run_quick_lint`, `execute_tests`, `code_agent`
- **File Tools**: `send_file`, `page_parser`
- **Clone Management**: `create_clone`, `preview_patch`

## Summary

The bash tool is your Swiss Army knife for system-level operations:
- Use `/tmp/` for temporary work
- Use clones for code experimentation
- Respect read-only boundaries
- Integrate with other Theo tools for complete workflows

For code modifications, prefer the specialized code tools. For everything else, bash has you covered! 🚀

