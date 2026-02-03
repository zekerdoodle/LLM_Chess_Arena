Integrating a Dedicated Coding Agent into Theo

To successfully integrate a “coding agent” tool (using the OpenAI Codex CLI) into Theo, we need to address authentication and invocation parameters. The goal is to offload coding tasks from Theo to an external Codex agent. Below we outline the necessary fixes and best practices:
1. Ensure Codex CLI is Installed and Accessible

Make sure the OpenAI Codex CLI is installed (e.g. via npm install -g @openai/codex) and that the codex command is on the system PATH. The code_agent tool will call this command via subprocess. The Theo code already checks for the CLI’s presence and will error if not found
GitHub
, so having it installed is a prerequisite.
2. Authenticate the Codex CLI Properly

Issue: The Codex CLI might be waiting for a web login (ChatGPT authentication) instead of using the API key, leading to no output. By default (as of Codex CLI v0.20+), if you have a ChatGPT Plus/Pro account, the CLI encourages “Sign in with ChatGPT” for access to latest models (e.g. GPT-5)
github.com
. This can cause the CLI to launch an interactive browser-based login flow, which our non-interactive tool cannot handle.

Solutions:

    Use OpenAI API Key mode: The Codex CLI still supports direct API key usage for pay-as-you-go billing. Export your OpenAI API key in the environment before running Theo, e.g.:

    export OPENAI_API_KEY="sk-..."  

    This environment variable tells Codex to use the API key for authentication
    github.com
    . In API-key mode, no interactive login is needed. Ensure that the environment variable is visible to the process running Theo (e.g. if Theo runs as a service, put the key in its env vars).

    Force API key usage if necessary: If Codex is still trying to do a web login despite the key, you may need to clear any cached ChatGPT auth. For example, if you previously logged in via web, remove the file ~/.codex/auth.json (this caches credentials) and/or run codex /logout once to clear ChatGPT auth
    github.com
    github.com
    . With no ChatGPT token available, Codex will fall back to the API key. Alternatively, explicitly tell Codex to prefer the API key by adding a config setting:

        Edit ~/.codex/config.toml to include preferred_auth_method = "apikey", or

        Launch Codex with the flag --config preferred_auth_method="apikey"
        github.com
        .

    This ensures the CLI skips the web login and uses your API key if it’s set
    github.com
    . (When preferred_auth_method="apikey" and an API key is present, the login screen is skipped entirely
    github.com
    .)

    ChatGPT authentication (optional alternative): If you do want to use ChatGPT Plus authentication (to access GPT-4/5 without API charges), you’ll need to complete the OAuth login flow. On a headless server, one approach is to run codex login locally (where you can open the browser) and then copy the generated auth.json to the server
    github.com
    github.com
    . However, for most cases using the API key is simpler and avoids interactive steps.

Bottom line: Make sure Codex CLI is authenticated in a non-interactive way. The simplest path is exporting the OPENAI_API_KEY and configuring Codex to use it. Once done, the CLI should run without pausing for web login (no more “login via web” prompts).
3. Invoke Codex in Non-Interactive Mode

Issue: The initial implementation of code_agent calls the CLI as:

cmd = ["codex", "--ask-for-approval", "never", "--sandbox", "danger-full-access", prompt]

Without the proper mode, this command may launch the interactive Text UI (TUI) of Codex, which won’t produce capturable output in a headless run. The symptom was “no output” because Codex likely waited in an interactive session or didn’t print to stdout in a normal way.

Solution – use the exec subcommand: The Codex CLI provides a non-interactive automation mode via the exec command. According to OpenAI’s documentation, using codex exec "Your prompt" runs Codex headlessly and exits after completing the task
github.com
. In contrast, codex "Your prompt" (without exec) starts the interactive TUI with that initial prompt
github.com
. For CI or tool integration, codex exec is the correct choice:

    Modify the tool command: Update the code_agent tool to include the exec flag. For example:

cmd = [
    "codex", "exec",
    "--ask-for-approval", "never",
    "--sandbox", "danger-full-access",
    prompt,
]

This tells Codex to run the given prompt non-interactively and immediately return output. We keep the flags --ask-for-approval never and --sandbox danger-full-access (more on these below) to ensure Codex runs fully autonomously with no prompts for approval.

Use --full-auto (optional shorthand): Codex CLI has convenience profiles. The example from OpenAI uses --full-auto for automation
github.com
. In a GitHub Action they show:

    codex exec --full-auto "update CHANGELOG for next release"

    The --full-auto flag presumably sets a high-autonomy profile (likely equivalent to no approvals with a safe sandbox) – though in our case we explicitly want no restrictions, so we used danger-full-access + never. You can continue with explicit flags as above, or use --full-auto if it fits your needs. In any case, including the exec subcommand is key to get output back in the subprocess.

After this change, the subprocess.run will execute Codex in headless mode and capture its stdout/stderr. We expect now that code_agent will return a summary of Codex’s output instead of an empty result. (Theo’s implementation already concatenates the last 200 lines of output into a summary string for the chat
GitHub
.)
4. Configure Codex Execution Parameters

Theo’s code_agent tool currently uses fixed parameters when calling Codex CLI:

    Sandbox Mode: --sandbox danger-full-access gives Codex full autonomy inside the working directory (the clone)
    GitHub
    . This means the agent can read/write any file in the clone and run commands without restriction (effectively disabling Codex’s usual safety sandbox)
    github.com
    . This is powerful but dangerous, so it’s only used on an isolated clone (not your live project). The Theo docs explicitly warn to use clones and not the live project for the code agent
    GitHub
    .

    Approval Mode: --ask-for-approval never ensures Codex will not pause to ask for human approval on any action
    github.com
    . Since Theo is fully automated, this is necessary (Theo can’t click “YES” on a prompt). It essentially runs Codex in a fully autonomous mode. The Codex docs confirm that --ask-for-approval never combined with a permissive sandbox yields full auto behavior (to be used only in safe environments)
    github.com
    .

    Model Selection: By default, Codex will choose a model for you. The CLI uses the OpenAI “Responses API” under the hood, with a default model (OpenAI mentioned o4-mini as a default, which likely corresponds to a GPT-4 variant)
    github.com
    . If needed, you can specify a model explicitly with the -m/--model flag
    github.com
    – for example, --model gpt-4.1 or --model gpt-5. Theo’s tool doesn’t currently expose this as an option; it will use whatever default or configured model the Codex CLI has. If you want Theo to be able to choose models (e.g. a faster but less powerful model vs. a larger one), you could add a parameter for it and pass --model accordingly. In most cases, leaving it default or configuring it in ~/.codex/config.toml is fine.

    Timeout: Theo’s code_agent takes an optional timeout_seconds (default 300s)
    GitHub
    . If Codex takes longer than this, the subprocess will be killed. Ensure the timeout is generous enough for complex coding tasks. You can adjust this value when calling the tool if needed.

Can Theo adjust these parameters? Currently, No – the tool schema only allows Theo to provide the prompt and clone_path (and optionally a timeout)
GitHub
GitHub
. The sandbox and approval settings are hard-coded for safety and simplicity. Theo (the AI agent) will always invoke Codex in full-autonomy mode on a clone. This is by design to minimize complexity: Theo doesn’t need to decide on sandbox levels or approval modes – it always runs with maximum permissions in an isolated environment.

If you foresee scenarios where a more restrictive mode is desired (e.g. only let Codex suggest changes without writing, or run in read-only analysis mode), you could extend the tool. For instance, you might add a parameter like "sandbox_mode": {"type":"string","enum":["read-only","workspace-write","danger-full-access"]} and have the code use it to build the command. But be cautious exposing too much flexibility to an LLM; it’s often safer to keep the tool’s behavior fixed. In this case, Theo’s documented workflow is: create a clone, run codex with full autonomy, then review and test the changes before applying
GitHub
. That provides a safety net instead of trying to limit Codex’s actions upfront.
5. Verify the Workflow in Practice

With authentication fixed and the invocation updated, the coding agent tool should now work reliably. Here’s the expected workflow and best practices to use it:

    Clone before coding: Always have Theo use the create_clone tool to make a sandbox copy of the project. The code_agent will refuse to run on a non-clone path for safety
    GitHub
    GitHub
    . (Theo’s docs also emphasize never to run the code agent on the live repo
    GitHub
    .)

    Run code_agent(prompt, clone_path): Theo supplies a clear coding prompt describing the task, and the path of the clone. For example, Theo might prompt Codex: “Implement a new function in module X to do Y, following these requirements...”. The Codex CLI will execute autonomously, making edits and running any commands it deems necessary (because we gave it full access). It will produce output detailing what it did. Theo’s code_agent captures this and returns a summary of the stdout/stderr
    GitHub
    along with the full output in a dictionary.

    Review Codex output: Theo (or you) should examine the result. The output might include diffs of changes, logs of commands run, or error messages if something went wrong. Theo can present this to the user or parse it. Do not blindly trust success – treat it as a report. (The tool’s guidance says to use the output as a report, not proof of success
    GitHub
    .)

    Test and apply changes: Ideally, Theo should run execute_tests on the clone to ensure everything still passes, or use read_file to inspect critical files that were changed. This follows the recommended flow: “...→ review stdout/stderr → execute_tests on the clone → preview_patch/apply_patch if satisfied.”
    GitHub
    . Theo can use preview_patch to see a diff of the clone vs. main, and if the changes look good, use apply_patch to merge them into the main codebase. This step ensures the Codex agent’s work is validated before it affects the actual project.

By implementing the above steps, you take the coding load off Theo and delegate it to the Codex CLI agent safely. In summary:

    Fix authentication so Codex runs without interactive login (use API key mode or pre-auth via auth.json)
    github.com
    github.com
    .

    Call Codex in the correct mode (codex exec) so that it executes headlessly and returns output
    github.com
    .

    Keep appropriate parameters: full autonomy (--ask-for-approval never) and clone sandbox (--sandbox danger-full-access) for maximum capability within an isolated clone
    github.com
    .

    Allow Theo to provide prompt (and clone path), but keep the dangerous operations constrained to clones and always review/test the results before applying
    GitHub
    .

With these fixes, the “coding agent” tool should function reliably, allowing Theo to focus on higher-level reasoning while Codex handles the heavy lifting of code generation and modification. 🚀

Sources:

    Theo’s code_agent implementation and usage guidelines
    GitHub
    GitHub

    OpenAI Codex CLI documentation on authentication and usage
    github.com
    github.com
    github.com
    github.com

    Codex CLI reference for non-interactive mode and parameter options
    github.com
    github.com
