# Theo - AI Companion Specifications
This is the only file written entirely by a human. The rest of the repo is AI generated. 

LAYER 1 - ChatBot:
-User sends a message in the web app, Theo responds back to that one message in the same room. 
-The message from user should be able to be anything allowed to be sent via the web app... Files, messages, embeds, pictures, etc (not audio)
-User should be able to choose any model from Google, OpenAI, xAI, Groq, or Anthropic, and message them in the web app.
$Files- model_call_google, model_call_xai, model_call_openai, model_call_anthropic, config (for choosing model + API keys for LLMs) 

---

LAYER 2 - ShortTermMemory: 
-Giving Theo a 'Context Window' that includes ChatHistory.
-One chat history file per room
-ChatHistory: {user|assistant|system}, timestamp, room name, message content
-ChatHistory has a 'max tokens' parameter in config file. once chat history exceeds max tokens, truncate to keep only as many recent chats as will fit in the 'max tokens'
#Prompt- System instructions, Chat history <-- replaces 'Current Prompt' 
$Files- chat_history, prompt_printer (crafts the prompt using each section of the prompt for the model_call)

---

LAYER 3 - LongTermMemory:
-Adding a "memory bank" to the prompt. 
-human-like: Every 10 minutes, ONLY IF there's a new message, a mini-model is prompted like normal, with the 'system instructions' being replaced with the 'scribe instructions', explaining that it (Theo) should extract useful, or important memories from the conversation. It should be instructed not to create duplicates or similar memories. 
	+What is stored: a string (the memory captured), time stamp, and embedding of the string. 
-verbatim: After every chat chunk (where the user, and assistant have both spoken once), the data is stored and embedded. 
	+What is stored: a chat chunk (with timestamps!) and associated embedding. 
-theo: This is Theo's primary memory type that he is able to CRUD himself using tools (added later). 
	+What is stored: a string (Theo's chosen memory), timestamp, importance ranking (0-100), and an associated embedding
			+If importance = 100, it is guaranteed to be put in his context window (always included, even if it would otherwise exceed the memory budget).
	-Memories are then surfaced to Theo using vector embeddings with a relevance-first mindset, blended with freshness. In practice: 1) Relevance (similarity to the current query), 2) Recency (a smaller boost so fresh context floats up), and for Theo memories also 3) Importance (0–100) adds weight.
	-Each memory type has a token budget. We pick the highest-value memories (by the above ranking) that fit the budget — except Theo importance=100 memories, which are always included.
#Prompt- System instructions, Memory bank, Current room, Chat history
$Files- human_memory, verbatim_memory, theo_memory, human_memories, verbatim_memories, theo_memories 
$$ -'memory'=code file, 'memories'=storage

---

LAYER 4 - Tools 
-Tools are what makes Theo extra special. Theo can run tools in parallel (or sequentially where required/indicated*) to answer questions better, perform autonomous actions, self-improve, and much more. 
-This fundamentally alters how Theo works... NOW when Theo gets a request, a response is not automatically sent to the user, until it contains **no tool calls** (with the exception of manually_send_message for obvious reasons). This allows Theo time to "think" without user visibility and process/generate a response with tools.

**CRITICAL REQUIREMENT**: ALL tools MUST use native provider function calling capabilities (OpenAI tools parameter, Anthropic tool_use, Google function_declarations, xAI tools). NO tools may use regex-based text parsing or detection. Each tool must have a properly defined function schema with strict parameter validation and be callable through the model provider's structured function calling API. docs/ has all the requisite documentation for *every* provider on this subject.

By category, in order of implementation, 

Working Memory tools: 
Provides Theo a scratchpad to serve as a working memory. By default each entry lasts 5 exchanges, where an exchange is 1 user message + 1 assistant message. 
add_working_memory: Appends a (single OR multiline) entry to the live working memory that gets injected into every prompt henceforth (until expiration) 
Instructions for Theo: Working memory is a *private* + *ephemeral* log meant to be used as a versitile scratchpad. You can store frustrations, successes, summaries of tool outputs, meta-cognitive thoughts, todo lists, sub-questions, or anything that would be useful to retain for the present moment / conversation. These are ephemeral and are automatically wiped from memory after 5 turns by default (though this is a tweakable parameter). 

Bash tool: 
Allows theo to interact with his environment.
-File operations
-Executing python + tests 
-Run lints 
-Literally anything that doesn't toast himself accidentally or create severe security risks


Web room tools: 
manually_send_message - allows Theo to send a message, image, form, or file manually in a room. optional room_id parameter to target specific rooms. may be used mid-turn for updates on a current task

Image handling tools: 
generate_image - use a SOTA image gen model to generate an image. has a flag for 'don't send to user', which is by default, off. (aka; the image is auto-sent to user, which does *not* end the turn)
analyze_image - call a vision model (like gpt-5-mini, selectable in config.yaml) to analyze the image. The tool accepts {image file | prompt} where the 'prompt' defaults to: 'analyze this image, and provide a detailed textual analysis', and the prompt should ALWAYS be suffixed with 'respond only with a textual description in markdown format' (even when customized by Theo). The output of this vision model is returned to Theo as tool output, as well as saved as a {image_filename}_description.md file with the same name as the image file. Eg: image = image.png/jpg/etc, means text description is 'image_description.md'. When the user sends an image file, Theo should immediately call analyze image. Theo should also be able to manually select analyze image (in the event that he generates an image and wants to look at it). 

Misc
create_diff - generate a unified diff between current file and proposed changes

External code agent:
code_agent - runs the Codex CLI inside a clone only. Command used: codex --ask-for-approval never --sandbox danger-full-access "<prompt>"
  - Use highly specific, step-by-step prompts with explicit acceptance criteria and file targets.
  - Limitation: No internet access. To provide new docs: use url_retrieval → page_parser to save documentation into the vault, then reference those files in your code_agent prompt.
  - Always validate results: read changed files and run tests (execute_tests) before applying patches.

Agentic information hierarchy tools: 
create_task - schedule future work. requires: 'name', 'details', 'start_time'. optional: 'recurrence' (cron), 'silent' flag. Each task automatically owns a dedicated hidden room; see docs/operations/TASKS_SYSTEM_COMPLETE_2025-10-20.md for delivery rules.
update_task - update task fields (name, details, timing, silent flag, delivery). statuses: active (scheduled and will execute) / completed (finished or manually marked complete) / archived (archived by user, no longer active) / needs_attention (failed repeatedly, requires review).
list_task - returns active tasks or details for a specific task id.

(Not a tool, but relevant to agentic information hierarchy tools) Rooms & Tasks:
standard room - When a user clicks '+' (or any variation of a 'new chat' button), a standard room is created. A name is generated by a cheap model who reads the initial message + standard prompt details (similar to dynamic optimizer). Users can interact in these rooms and navigate through the side bar to them. 
task room - When a task is created, it gets assigned a room. All outputs as a result of the tasks are sent to this room. 
	- scenario 1, during normal convo, theo creates a task: when a task is created in the middle of a conversation, the room should be duplicated, where one instance is the persistent standard room (the room in which the task was created doesn't change), and the other instance is assigned to the task. all outputs related to that task are sent to it's own room. however, theo should be able to select 'new room (lose exact context)', 'same room (reuse current room)', or 'specific room (insert ID)'. 
		- In ANY case, Theo should know what's happening so that the user can be informed by him about it. example theo output regarding room selection: "I created that task which copied these chats over into a new room! If you want any changes, pop over to the tasks view and open that tasks room!"  
	- scenario 2, theo creates tasks on his own (via other tasks): when a task is created by theo during his own work, by default, the task will be assigned to the same room in which the task was created in, however, theo should be able to select 'new room (lose exact context)', 'same room (explicitly reuse)', or 'specific room (insert ID)' 
The core difference in the scenario above is: IF the task is created inside a 'standard' room, the 'default' for which room is used for tasks is a 'duplicate'. IF the task is created inside a 'task' room, the default is 'same room'. 
inbox - If a task is not explicitly set as silent, all outputs related to that task will be directed to the 'inbox' so that the user can read through as well as inside the tasks room. If a task is explicitly set as silent, all outputs for that task are directed ONLY into the tasks room.
- Users should be able to interact with any room. Standard rooms are only selectable via the side panel (and inbox when a task is active inside the standard room), and tasks rooms are only selectable inside the tasks > Manage tasks section (and inbox of course) 

manually_send_message - single entry point for Theo to send user-facing messages. Supports optional 'channel_id', 'delivery' (default/inbox), file attachments, and 'schedule' ({start_time, recurrence}) for reminders.

(Notifications now piggyback on manually_send_message; standalone notification tools are deprecated. See docs/operations/TASKS_SYSTEM_COMPLETE_2025-10-20.md.)

#Remove python analysis tool; replaced with 'bash'

Web search tools:
web_search - perform one or multiple web searches (parallel-capable). Good for quick, general information gathering. For verbatim or quotable content, use url_retrieval → page_parser.
url_retrieval - find relevant URLs and basic metadata for a query
page_parser - fetch a URL, parse content to Markdown, optionally save to vault

Self-patching tools: 
create_clone - copies Theo's source code into a subfolder.
preview_patch - preview diffs between a clone and live code without applying changes.
apply_patch - after theo completes his coding + testing process, he can apply the patch to himself. before applying, Theo ensures recent backups are in place automatically. changes are copied onto his actual source code, and Theo reboots. after theo reboots, he is prompted with a system message indicating the status of the patch, like 'patch succesful, and reboot applied. you're now running your new code!' OR 'patch failed, rollback successful', allowing him to continue work or close out tasks, etc. 

Information tools: 
add_memory - add a theo memory with 'memory string', 'importance 0-100' (last_modified timestamp is automatically generated and stored)
update_memory - update a theo memory (can update content, importance, or both)
delete_memory - delete a theo memory

Form tools: 
define_form - create a form for the user to enter data easily. 
show_form - at any time, Theo can show a user a form he's created.
save_form_submission - store user responses to a form in long-term storage for Theo to review.
list_form_submissions - list recent submissions for a form, optionally filtered by room or time.



---

LAYER 5 - Webapp
a standard LLM chat type interface. it has a dark and a light mode. user can create new 'rooms', chat with theo, fill out forms, see scheduled tasks, send and receive files, images, and chats. 
everything is icons (minimal explainer text), everything is *instantly* responsive, webapp can send notifications even when it's closed for user (especially on mobile devices)
- "the orb" is a dynamic circle that is the first phase of theo's embodiment. it moves with processing, and shows tool icons only as a tool is being used live, and is visibly appealing  
accepts interruptions; if theo is working on a multistep task, the user can type and send another message that immediately appends to the turn so that theo has a live view at current chat history. this does NOT reprompt from scratch, but just appends the new user message for full context. 
- Entirely icon based. There is no instruction text, and black/white icons (svgs) make it clear what each button does
- Black and white (except for the orb) entirely
- Code like font 
- Highly responsive, deduplicated, and consistent code (not using several frameworks when it could just be one). 
- During early processing, keep the orb chip textless and stream the GPT reasoning summary inside the message view.
- The reasoning teleprompter auto-scrolls while showing at most two lines of summary text at a time; no rotating placeholder wording. <------|
- As much space as possible is taken up by the chat window, aka 'room drawer' on the left can be minimized
- Chat titles are animated and renamed immediately
- Nothing requires a refresh! 
- Forms are fully supported and crisp and easy to use and submit
- Markdown is FULLY supported
- Tokens are printed exactly as they stream (dozens of times a second!) on final response
- Tool icons are svg's and there's a unique one for every tool


---

Prompt Configuration: 

{System instructions}
{Available tools + docstrings}
{Memory bank}
(Working Memory) <- only when working memory items exist
{Chat history}

---

Appendix: 

Task: Theo can make sure he calls himself for a future time to complete something. A task just adds a message to chat history with the task information, and makes a call like normal. Can be 'silent' where Theo chooses that the final model reply doesn't need to be sent to the user. However, even in silent mode, model reply is appended to chat history.

Dynamic context optimizer: A mini-model is prompted through the 'model_call' mechanism, with only the 'max budget' and 'system instructions' changing to request a structured output for 
-How much total context do we need (minimum, small, medium, etc)?
-Importance of current chat history? 
-Importance of using memories (from other chats)? 
This determines what percentage of the 'max context' (set in config) we allocate based on the complexity of the current chat, as well as what fraction of that is 'memory' versus 'chat history' The memory fractions change to the memory types all totalling to 100% (eg: 40% verbatim, 30% Theo, 30% human like), aka removing chat history from the equation, since the fraction of 'memory' to 'chat history' is now decided by the Dynamic Context Optimizer. The mini-model is always given only 10% of max budget. Uses an openAI model with structured outputs

Self-patching: Theo can update himself using an advanced, protected mechanism: 0. Backup self 1. Clone self 2. Make edits / Run tests 3. Repeat until tests pass 4. Apply patch

Backup/Restore mechanisms: A backup is made to git every 10 minutes on a schedule. Before any 'apply_patch' is called, a backup is also made. If upon restart of Theo, his patch causes catastrophic failure that prevents startup, even after several retries, a restore mechanism is to be in place that simply pulls the most recent backup. There are 2 separate backup needs: the 'vault' is separate from the 'code'. The vault should NEVER be pulled from git with the restore mechanism. The push can include the vault, but the pull can NOT. Since code changes would never impact the vault, this maintains continuity when Theo is prompted upon startup. 

Vault: The central location for all "user" data. Things stored here include: 
-Chat history
-Memory (all types)
-tasks
-Any generated artifacts
Basically, anything that is injected into or generated by a prompt is stored in vault.

Chat History: The most important part of Theo's prompt. Contains -
-Chats (from _and_ to user) and the rooms they were made in
-Tool calls/outputs made
Rules - 
-Verbatim memories that already exist in the chat history, are *NOT* shown (prevent duplication) and do *NOT* count towards the token budget (making space for more valid verbatim memories) 
-Duplicate tool outputs are omitted (keeping only the most recent one). This is *not* tool caching, as the tool outputs will always actually be generated, but before being appended to chat history, and thus, before being appended to the prompt, (the chronological FIRST) true duplicates are replaced with text that indicates that subsequent tool calls already contain that information, and that the previous tool output was omitted for deduplication. 

Backup/Restore: Use gitpython for code backups (push every 10 min via scheduler on server startup). Vault backups separate (e.g., zip to vault/backups/vault_timestamp.zip). On apply_patch failure, auto-restore from latest git pull (code only, not vault). After restart, a wake-up status message is appended to the most recent room.

Dynamic Context Optimizer: Use a cheap model (e.g., OpenAI's smallest) for mini-calls. Structured output: JSON with {'context_size': 'small/medium/large', 'history_importance' etc}.

CLI Entry Point: In theo.py, use click for commands: start (with interactive setup for keys/model if config incomplete), stop, restart, logs (tail theo.log), status (show running/model), fresh-start (reset vault with confirmation), config (edit fields).

Testing Milestones: Per layer/sub-checkpoint, include unit tests and manual verification steps.

---

Config: 
Config file should have places to select - 
-Primary model
-Fallback model
-System instructions
-Max token budget
-Percentage allocated to each dynamic prompt segement; Theo memory, Human-like memory, Verbatim memory, Chat history
