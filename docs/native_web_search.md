# Native Web Search Integration

## Overview

Theo now uses native web search capabilities for OpenAI and xAI models instead of the custom `web_search` tool. This provides better performance, more accurate results, and proper citation support.

## Implementation

### OpenAI Models

OpenAI models use the `web_search_preview` tool through the Responses API:

- **Enabled by default** when tools are present in the request
- **Configurable options** (optional in `config.yaml`):
  - `web_search_context_size`: "low", "medium" (default), or "high"
  - `web_search_user_location`: Object with `country`, `city`, `region`, `timezone` fields
  
**Example config.yaml:**
```yaml
web_search_context_size: medium
web_search_user_location:
  country: "US"
  city: "San Francisco"
  region: "California"
```

### xAI Models (Grok)

xAI models use native live search through `search_parameters` to **replace** the custom `web_search` tool.

#### Default Behavior

By default, xAI models have:
- **Native live search ENABLED** with `mode: "auto"` - model decides when to search
- Custom `web_search` tool hidden (replaced by native search)
- Access to `url_retrieval` and `page_parser` for additional research needs
- All other standard tools available

**Optional configuration in config.yaml:**
```yaml
# Disable native live search if you experience issues
xai_enable_live_search: false  # Default: true

# Customize data sources (optional)
xai_search_sources: ["web", "news", "x", "rss"]

# Control citation visibility (optional)
xai_return_citations: true  # Default: true
```

**Important Notes:**
- Live search uses `mode: "auto"` - the model intelligently decides when to search
- Search results are embedded in the model's response, not separate tool calls
- This provides seamless web search capability without explicit tool calls
- Works alongside function calling tools - the model uses search when needed

## Tool Filtering

The custom `web_search` tool is automatically hidden for OpenAI and xAI models:

- **Providers with native search**: OpenAI, xAI → `web_search` tool hidden
- **Other providers**: Anthropic, Google, Groq, Local → `web_search` tool available

This is handled automatically by `generate_tool_schemas(provider)` in `utils/tool_schemas.py`.

## Key Changes

### Files Modified

1. **utils/tool_schemas.py**
   - Added `provider` parameter to `generate_tool_schemas()`
   - Conditional hiding of `web_search` tool for OpenAI and xAI

2. **layer1_chatbot/model_call_openai.py**
   - Added `web_search_preview` tool configuration
   - Supports `search_context_size` and `user_location` parameters

3. **layer1_chatbot/model_call_xai.py**
   - Added `search_parameters` with `mode: "on"`
   - Supports custom data sources and citation control

4. **layer1_chatbot/base_model.py**
   - Pass `config` through kwargs to `_make_api_call()`

5. **layer4_tools/tool_orchestrator.py**
   - Pass `provider` parameter to `generate_tool_schemas()`

## Benefits

- **Better Performance**: Native search is optimized and FAST - use it for most information gathering
- **Accurate Citations**: OpenAI returns proper citations; xAI can too (if enabled)
- **Consistent Experience**: Same tool interface across all providers
- **No Breaking Changes**: Other providers continue to use the custom `web_search` tool
- **Flexibility**: `url_retrieval` + `page_parser` still available for when you need exact figures, verbatim quotes, or specific source URLs (though slower)

## Usage Guidelines

### When to Use Native Web Search (OpenAI/xAI)
✅ **Use for most information gathering:**
- General facts and current information
- Recent news and events
- Explanations and summaries
- Quick research and fact-checking
- **Fast and automatic** - the model handles it seamlessly

### When to Use url_retrieval + page_parser
⚠️ **Only use when you specifically need:**
- Exact numerical figures from a specific source
- Verbatim quotes for citations
- Complete article or documentation text
- Specific URL references for verification
- **Note: These are SLOWER tools** - avoid for general information

## Testing

Run the provider filtering test:
```bash
python3 -c "
from utils.tool_schemas import generate_tool_schemas
print('OpenAI:', 'web_search' in [s['name'] for s in generate_tool_schemas('openai')])
print('xAI:', 'web_search' in [s['name'] for s in generate_tool_schemas('xai')])
print('Anthropic:', 'web_search' in [s['name'] for s in generate_tool_schemas('anthropic')])
"
```

Expected output:
```
OpenAI: False
xAI: False
Anthropic: True
```

## References

- [OpenAI Web Search Documentation](https://platform.openai.com/docs/guides/tools-web-search)
- [xAI Live Search Documentation](https://docs.x.ai/docs/guides/live-search)


