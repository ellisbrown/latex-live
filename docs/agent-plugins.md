# Agent plugins

latex-live can ask a program of yours, an *agent plugin*, to polish text in the
source pane. Select some text (or put the cursor in a paragraph), and run
**Polish with the agent** from the palette (Cmd/Ctrl+K), or **Polish with the
agent, with a note…** to add a wish such as "shorter". A card under the text
shows the revision as a word diff, with the plugin's reason. Accept it
(Cmd/Ctrl+Enter) and it goes into the text like typing, saved as usual and
undone with Cmd/Ctrl+Z; Reject (Esc) leaves the text as it was. Nothing is
written before you accept.

latex-live ships no plugin of its own: you choose the model and the tool that
calls it. A plugin is a few lines of code. This page has the protocol, what
latex-live does around a plugin, what it expects of one, and a prompt to have
your coding agent write one for you.

## Turning it on

Set `LATEX_LIVE_AGENT_CMD` to the plugin's command, in the VS Code task's
`options.env` ([setup](setup.md#environment-variables)), and restart the
preview:

```json
"options": { "env": { "LATEX_LIVE_AGENT_CMD": "/path/to/my-plugin --model fast" } }
```

The command is split as a shell would split it, but no shell runs it, so
`$VARS`, pipes and `~` don't work in it: use absolute paths, or a script. The
hook is only ever on in your own previews, and only for you: never in shared
mode, whatever the setting.

The plugin runs with a minimal environment: `PATH`, `HOME`, `USER`, `LOGNAME`,
`TMPDIR`, `TZ` and the locale. To pass it more (an API key, a proxy), name the
variables in `LATEX_LIVE_AGENT_ENV`, separated by commas, e.g.
`ANTHROPIC_API_KEY,HTTPS_PROXY`. latex-live's own settings (`LATEX_LIVE_*`)
are never passed.

## The protocol (version 1)

For each request latex-live runs the command once, writes one JSON object to
its stdin and closes it, and reads one JSON object from its stdout. The plugin
then exits with status 0.

The request:

```json
{
  "protocol": 1,
  "selection": "We utilize  a novel method~\\cite{doe2021}.",
  "paragraph": "In this section we describe our approach. We utilize  a novel method~\\cite{doe2021}. It works well.",
  "note": "shorter",
  "file": "sections/method.tex",
  "line": 12,
  "timeout": 90
}
```

The answer, with the text to put in place of the selection:

```json
{"replacement": "We use a novel method~\\cite{doe2021}.", "rationale": "Plainer verb; removed a double space."}
```

Or, when it can't revise the text:

```json
{"error": "the model is not available"}
```

As JSON Schema:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$defs": {
    "request": {
      "type": "object",
      "required": ["protocol", "selection", "paragraph", "note", "file", "line", "timeout"],
      "properties": {
        "protocol": {"const": 1},
        "selection": {"type": "string", "minLength": 1, "maxLength": 16000,
                      "description": "The exact LaTeX source to revise."},
        "paragraph": {"type": "string", "maxLength": 32000,
                      "description": "The paragraph around the selection, which contains it: context only."},
        "note": {"type": "string", "maxLength": 2000,
                 "description": "The author's wish for this revision, or empty."},
        "file": {"type": "string", "description": "The source file, relative to the paper's folder."},
        "line": {"type": "integer", "minimum": 1, "description": "The line where the selection starts."},
        "timeout": {"type": "integer", "description": "Seconds before the plugin is stopped."}
      }
    },
    "answer": {
      "oneOf": [
        {"type": "object", "required": ["replacement"],
         "properties": {"replacement": {"type": "string", "maxLength": 64000},
                        "rationale": {"type": "string", "maxLength": 2000}}},
        {"type": "object", "required": ["error"], "properties": {"error": {"type": "string", "minLength": 1}}}
      ]
    }
  }
}
```

More about the answer:

- Print only the JSON object: no code fences, no other output on stdout (log to
  stderr if you need to). Extra keys are ignored; an `error` wins over a
  `replacement`.
- The replacement is LaTeX source, put in place exactly as given. It may be the
  selection unchanged (the card says "No change") or empty (it removes the
  selection), and must have no NUL characters.
- A non-zero exit status is a failure: the card shows the status and the last
  line of stderr.
- Later protocol versions add keys rather than change these. Refuse a
  `protocol` you don't know with an `error`.

## What latex-live does around it

- It runs the plugin in an empty temporary folder, not the paper's, and removes
  the folder after.
- It stops the plugin, and every process it started (its process group), after
  `timeout` seconds (90), when it prints more than 256 KB, and when it exits.
- It runs one plugin at a time.
- It never sends text that `LATEX_LIVE_PROTECTED` marks: if the selection, the
  paragraph or the note holds the marker, it refuses before running the plugin.
  It also refuses an answer that holds the marker.

## What a plugin must do

The selection and the paragraph are the paper's text, which may come from
coauthors or from anything pasted into the paper; the note is typed in the
browser. Treat all three as untrusted data:

- Send them to the model as data, never as instructions: e.g. as one JSON object
  in the user message, with the rules in the system prompt, and tell the model
  to ignore instructions inside them.
- Never put them in a shell command line or evaluate them.
- Give the model no tools: no shell, no file access, no web search or fetch, no
  MCP servers, plugins or hooks. The only thing the plugin does is the model
  call. A coding-agent CLI must run with all of that turned off (see
  `examples/agent-claude.sh`), since its default is to act.
- Read no files, and keep secrets in the plugin's own configuration or in the
  variables of `LATEX_LIVE_AGENT_ENV`.
- Answer within `timeout` seconds, and print exactly one JSON object.

## Examples

- [`examples/agent-tidy.py`](../examples/agent-tidy.py): no model, just
  rules (it collapses repeated spaces and replaces "utilize"). A template for
  the reading and printing, and what latex-live's own checks use.
- [`examples/agent-claude.sh`](../examples/agent-claude.sh): Claude Code in
  print mode with no tools, no MCP servers, no settings files and no slash
  commands. Its arguments go on to `claude`, e.g. `--model sonnet`.

## Testing a plugin

From latex-live's folder, try a plugin on a sample request, as the viewer would
run it (the same folder, environment, timeout and checks of the answer):

```bash
cd /path/to/latex-live
python3 -m latexlive.agent --test "/path/to/my-plugin --model fast"
python3 -m latexlive.agent --test "/path/to/my-plugin" --note "shorter"
```

It prints the request and the answer, and exits with 0 if latex-live would
show it, else 1 with what was wrong. A path relative to the current folder is
tested as an absolute one, with a note: `LATEX_LIVE_AGENT_CMD` needs absolute
paths.

## A prompt for your coding agent

Paste this into Claude Code, Codex or another coding agent, with the parts in
angle brackets filled in:

> Write me an agent plugin for latex-live, the LaTeX preview in
> `<path to the latex-live checkout>`. Read `docs/agent-plugins.md` there
> first: it has the protocol, the safety rules and the examples. The plugin
> should polish the selection with `<the model, CLI or API to use, e.g. the
> llm CLI with my default model>`. Put it at `<where>`, as one script with no
> dependencies beyond that tool and the standard library. Check every flag you
> use against the tool's `--help` on this machine: the model must get no tools,
> no MCP servers, no plugins or hooks, and no file access. Send the selection,
> the paragraph and the note as data in the user message, never as
> instructions. Then test it with
> `python3 -m latexlive.agent --test "<command>"` from the latex-live folder,
> fix it until the test passes, and tell me the value to set
> `LATEX_LIVE_AGENT_CMD` to (and `LATEX_LIVE_AGENT_ENV`, if it needs
> variables such as an API key).
