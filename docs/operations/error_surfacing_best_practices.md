# Error Surfacing Best Practices for LLM Tool Design

## The Problem: Silent Partial Failures

### What Happened
Theo repeatedly called `get_financial_accounts` 7+ times in a row, each time receiving:
- ❌ A 400 error from Plaid API (logged to file)
- ✅ A `success=True` response (sent to LLM)
- 🔁 No indication anything was wrong, leading to infinite retry loops

**Root Cause**: Tools were logging errors internally but returning `success=True` when partial data was available, preventing the LLM from learning about or adapting to failures.

---

## The Fix: Explicit Error Communication

### Before (Bad)
```python
try:
    response = api.get_data(item_id)
    all_data.append(response)
except ApiException as e:
    logger.error(f"Failed for item {item_id}: {e}")
    continue  # Silent failure, invisible to LLM

return True, f"Found {len(all_data)} items", all_data
```

**Problem**: LLM sees "success" and retries indefinitely.

### After (Good)
```python
failed_items = []

try:
    response = api.get_data(item_id)
    all_data.append(response)
except ApiException as e:
    # Parse structured error details
    error_code = extract_error_code(e)
    error_msg = extract_error_message(e)
    
    logger.error(f"Failed for item {item_id}: {error_code} - {error_msg}")
    
    failed_items.append({
        "item_id": item_id,
        "error_code": error_code,
        "error_message": error_msg
    })

# Build informative result
if not all_data and failed_items:
    # Total failure - tell the LLM exactly what went wrong
    error_summary = "; ".join([
        f"{item['error_code']}: {item['error_message']}"
        for item in failed_items
    ])
    return False, f"Failed to retrieve data: {error_summary}", []

elif failed_items:
    # Partial success - warn the LLM about issues
    warning = (
        f"⚠️ Warning: {len(failed_items)} connection(s) failed. "
        f"First error: {failed_items[0]['error_code']} - {failed_items[0]['error_message']}"
    )
    return True, f"Found {len(all_data)} items. {warning}", all_data

else:
    # Complete success
    return True, f"Found {len(all_data)} items", all_data
```

**Benefits**: 
- LLM can see what's failing and why
- Can make informed decisions (stop retrying, try different approach, ask user)
- User gets meaningful error messages

---

## Key Principles

### 1. **Never Hide Errors from the LLM**
Logs are for debugging. The LLM needs actionable information in the tool response.

### 2. **Parse and Structure Error Details**
Extract:
- `error_code` (e.g., `INVALID_API_KEYS`, `ITEM_LOGIN_REQUIRED`)
- `error_message` (human-readable explanation)
- Affected resources (item_ids, account_ids, etc.)

### 3. **Distinguish Failure Types**
- **Total Failure**: `success=False` + detailed error
- **Partial Success**: `success=True` + warning in message
- **Complete Success**: `success=True` + clean message

### 4. **Make Errors Actionable**
Bad: `"Error: API call failed"`  
Good: `"Error: INVALID_API_KEYS - Your Production credentials are incorrect. Update PLAID_CLIENT_ID and PLAID_SECRET in .env"`

### 5. **Prevent Infinite Loops**
When an error is returned, the LLM should be able to:
- Understand it's not a transient failure
- Know what action to take (ask user, try alternative, give up)
- Avoid blindly retrying the same failing call

---

## Implementation Checklist

When building tools that interact with external APIs:

- [ ] Parse API exceptions to extract structured error info
- [ ] Track failed items/operations separately from successful ones
- [ ] Include warnings in success messages when partial failures occur
- [ ] Return detailed error summaries in failure cases
- [ ] Log errors (for debugging) AND surface them (for LLM decision-making)
- [ ] Test with simulated failures to verify error handling
- [ ] Document error codes and their meanings in tool schemas

---

## Applied To: Financial Tools

**Files Modified**:
- `utils/plaid_client.py` - Added error tracking to `get_accounts()` and `get_transactions()`

**Changes**:
1. Added `failed_items` list to track per-item failures
2. Parse `ApiException` to extract `error_code` and `error_message`
3. Return failure status when ALL items fail
4. Include warnings in message when SOME items fail
5. Only return clean success when NO items fail

**Result**: Theo now sees errors like:
- `"⚠️ Warning: 1 connection(s) failed. First error: ITEM_LOGIN_REQUIRED - Item requires user interaction"`
- `"Failed to retrieve accounts: INVALID_API_KEYS - invalid client_id or secret provided"`

Instead of blindly retrying, Theo can now:
- Inform the user about specific errors
- Suggest remediation steps
- Try alternative approaches
- Know when to stop

---

## Testing Error Handling

Always test tools with:
1. **Complete success** - All operations succeed
2. **Partial failure** - Some operations fail, some succeed
3. **Total failure** - All operations fail
4. **Multiple different errors** - Different failure modes

Example test:
```python
def test_partial_failure_surfaces_error():
    mock_client.get_data.side_effect = [
        {"data": "success"},  # Item 1 succeeds
        ApiException(body='{"error_code": "INVALID_TOKEN", "error_message": "Token expired"}'),  # Item 2 fails
    ]
    
    success, message, data = tool()
    
    assert success is True  # Partial success
    assert "INVALID_TOKEN" in message  # Error is surfaced
    assert "Token expired" in message
    assert len(data) == 1  # Got the successful item
```

---

## The Hidden Problem: Tool Output Deduplication

### What Happened (Round 2)

Even after fixing error surfacing, Theo STILL looped! The warnings were being generated correctly, but then **deduplication was hiding them**.

**The Flow**:
1. ✅ Tool generates warning: `"⚠️ Warning: 1 connection(s) failed..."`
2. ✅ Tool returns warning to orchestrator
3. ❌ Orchestrator sees identical text as previous call
4. ❌ Deduplication replaces warning with `[DUPLICATE OUTPUT OMITTED]`
5. 🔁 LLM never sees the warning, keeps retrying

### Root Cause

Theo's tool orchestrator deduplicates identical tool outputs to save context tokens. **This is good for normal operations** (don't show the same account list 10 times), but **disastrous for errors** (LLM needs to see repeated failures to understand there's a persistent problem).

### The Fix

**Modified `/home/debian/Projects/Theo/layer4_tools/tool_orchestrator.py`**:

```python
# Check for duplicate outputs in conversation history
# BUT: Don't deduplicate if the output contains warnings or errors
# (these should always be visible to help the LLM learn from failures)
duplicate_found = False
has_warning_or_error = any(
    marker in str(display_output) 
    for marker in ["⚠️", "WARNING", "Warning", "ERROR", "Error", "❌", "FAILED", "Failed"]
)

if not has_warning_or_error:
    # Only deduplicate if there's NO warning/error
    for j, existing_entry in enumerate(conversation_history):
        if existing_entry == new_entry:
            # Replace duplicate with notice
            ...
```

**Result**: Warnings and errors are **never deduplicated**, ensuring the LLM always sees failures even when called repeatedly.

---

## Key Takeaway: Multi-Layer Problem

This bug had **two layers**:
1. **Layer 1 (Solved First)**: Tools caught errors but returned `success=True` without details
2. **Layer 2 (Solved Second)**: Tools surfaced errors, but deduplication hid them from LLM

**Both had to be fixed** for the LLM to learn from failures and stop looping.

---

## Conclusion

**Silent failures are invisible to LLMs.** Tools must explicitly communicate errors in their return values, not just in logs. This prevents retry loops, enables intelligent error handling, and dramatically improves user experience.

**But that's not enough!** Any system that **post-processes tool outputs** (deduplication, truncation, filtering) must be aware of errors and warnings, and **preserve them** even when it would normally optimize them away.

**Remember**: 
1. If you're catching an exception and just logging it, ask yourself: "Should the LLM know about this?" (Usually **yes**)
2. If you're optimizing/deduplicating tool outputs, ask yourself: "Could this hide critical error information from the LLM?" (If **yes**, add exceptions for warnings/errors)

