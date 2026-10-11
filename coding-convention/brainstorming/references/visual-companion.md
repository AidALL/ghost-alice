# Visual Companion Guide

A browser-based visual brainstorming companion that shows mockups, diagrams, and options.
## Contents

- [When to Use](#when-to-use)
- [How It Works](#how-it-works)
- [Starting a Session](#starting-a-session)
- [The Loop](#the-loop)
- [Writing a Content Fragment](#writing-a-content-fragment)
- [Available CSS Classes](#available-css-classes)
- [Browser Event Format](#browser-event-format)
- [Design Tips](#design-tips)
- [Filename Convention](#filename-convention)
- [Cleanup](#cleanup)
- [References](#references)


## When to Use

Decide per question, not per session. Test: is the content easier to understand by seeing it than by reading it?

Use the browser when the content itself is visual:

- UI mockups: wireframes, layouts, navigation structure, component design
- Architecture diagrams: system components, data flow, relationship maps
- Side-by-side visual comparison: comparing two layouts, two color schemes, two design directions
- Design refinement: when the question is about look and feel, spacing, or visual hierarchy
- Spatial relationships: state machines, flowcharts, or entity relationships drawn as diagrams

Use the conversation when the content is text or a table:

- Requirements and scope questions: "What does X mean?" or "Which feature is in scope?"
- Conceptual A/B/C choices: picking among approaches described in words
- Tradeoff lists: pros/cons, comparison tables
- Technical decisions: choosing an API design, data modeling, or architecture approach
- Clarifying questions: anything whose answer is words, not a visual preference

A question about a UI topic is not automatically a visual question. "What kind of wizard do you want?" is conceptual, so use the conversation. "Which of these wizard layouts is right?" is visual, so use the browser.

## How It Works

The server watches a directory for HTML files and serves the most recent one to the browser. When you write HTML content to `screen_dir`, the user can view it in the browser and click an option to select it. The selection is recorded as JSON in `state_dir/events`, where it can be read on the next turn.

Content fragment vs. full document: if the HTML file starts with `<!DOCTYPE` or `<html`, the server serves it as is (injecting only the helper script). Otherwise the server automatically wraps it in the frame template, adding the header, CSS theme, selection indicators, and all interaction infrastructure. By default, write a content fragment. Write a full document only when you need complete control over the page.

## Starting a Session

Resolve every `scripts/` path below relative to the loaded brainstorming skill directory, and invoke it through the host's exposed shell tool. Bind the project path from the accepted task, not the shell's incidental working directory.

```bash
# Start the server with persistence for the project (mockups are saved)
scripts/start-server.sh --project-dir /path/to/project

# Returns: {"type":"server-started","port":52341,"url":"http://localhost:52341",
#           "screen_dir":"/path/to/project/.coding-convention/brainstorm/12345-1706000000/content",
#           "state_dir":"/path/to/project/.coding-convention/brainstorm/12345-1706000000/state"}
```

Retain the startup JSON and its `url`, `screen_dir` and `state_dir`. Bind `STATE_DIR` to that exact returned `state_dir` and `SESSION_DIR` to its parent after confirming that `screen_dir` is the sibling `content` directory. These are execution-local bindings for this one returned session, not global defaults. Tell the user to open the returned URL only after successful startup. Missing or inconsistent returned paths leave dependent writes and cleanup unavailable; do not guess another session directory.

Finding the connection info: the server writes the startup JSON to `$STATE_DIR/server-info`. If you start the server in the background and do not capture stdout, read this file to obtain the URL and port. When using `--project-dir`, look for the session directory under `<project>/.coding-convention/brainstorm/`.

Note: if you pass the project root as `--project-dir`, the mockups persist in `.coding-convention/brainstorm/` and survive a server restart. Without it, the files go to `/tmp` and get cleaned up. Remind the user to add `.coding-convention/` to `.gitignore` (if it is not there yet).

Select the process mode from the actual host execution capability:

- The script uses foreground mode when `CODEX_CI` is set or its Windows environment checks match, unless `--background` explicitly overrides that choice.
- `--foreground` keeps the server attached to the shell session. Use the host's exposed persistent terminal or background-execution mechanism when that session must survive a tool return.
- The default background path starts the server and checks that it remains alive. A returned startup error is not a usable session; follow its persistent-terminal recovery instruction.

Do not infer process lifetime from a product name. Read the returned startup JSON or the exact session's `state/server-info` before presenting a URL.

If the returned URL is unreachable and the accepted environment permits non-loopback binding, use the host and URL hostname appropriate to that environment. The following is an example, not authorization to expose a server:

```bash
scripts/start-server.sh \
  --project-dir /path/to/project \
  --host 0.0.0.0 \
  --url-host localhost
```

Use `--url-host` to control which hostname the printed URL JSON shows.

## The Loop

1. Confirm the server is alive, then write HTML to a new file in `screen_dir`:
   - Before each write, confirm that `$STATE_DIR/server-info` exists. If it is missing or `$STATE_DIR/server-stopped` exists, the server has shut down, so restart it with `start-server.sh` before continuing. The server auto-stops after 30 minutes of inactivity.
   - Use the [Filename Convention](#filename-convention) for each new screen
   - Use the host's available file-writing tool and avoid printing the full HTML into the conversation
   - The server serves the most recent file automatically

2. Tell the user what to expect and end the turn:
   - Include the current URL when presenting a new screen
   - Give a short summary of what is on the screen (for example, "Showing 3 layout options for the home page")
   - Ask the user to respond in the conversation: "Take a look and let me know what you think. If you like, click to select an option."

3. The next turn, after the user responds in the conversation:
   - If it exists, read `$STATE_DIR/events`, which contains JSON lines of the user's browser interactions (clicks and selections)
   - Merge it with the user's conversation text to get the complete picture
   - The conversation message is the primary feedback. `state_dir/events` provides structured interaction data

4. Iterate or proceed: if the feedback changes the current screen, write a new file (for example, `layout-v2.html`). Move to the next dependent question when the user's response settles the current decision.

5. Unload when returning to the conversation: if the next step does not need the browser (for example, a clarifying question or a tradeoff discussion), push a waiting screen to clear the content you posted:

   ```html
   <!-- filename: waiting.html (or waiting-2.html, etc.) -->
   <div style="display:flex;align-items:center;justify-content:center;min-height:60vh">
     <p class="subtitle">Continuing in the conversation...</p>
   </div>
   ```

   This keeps the user from staring blankly at a resolved choice. When the next visual question comes up, push a new content file as usual.

6. Continue only while an unsettled visual decision remains; stop the server through Cleanup when the companion is no longer needed.

## Writing a Content Fragment

Write only the content that goes inside the page. The server wraps it in the frame template automatically, adding the header, theme CSS, selection indicators, and all interaction infrastructure.

Minimal example:

```html
<h2>Which layout is better?</h2>
<p class="subtitle">Consider readability and visual hierarchy</p>

<div class="options">
  <div class="option" data-choice="a" onclick="toggleSelect(this)">
    <div class="letter">A</div>
    <div class="content">
      <h3>Single column</h3>
      <p>A clean, focused reading experience</p>
    </div>
  </div>
  <div class="option" data-choice="b" onclick="toggleSelect(this)">
    <div class="letter">B</div>
    <div class="content">
      <h3>Two columns</h3>
      <p>Sidebar navigation with main content</p>
    </div>
  </div>
</div>
```

This is enough on its own. You do not need `<html>`, CSS, or `<script>` tags. The server provides all of them.

## Available CSS Classes

CSS classes that the frame template provides to your content:

Options (A/B/C choice)

```html
<div class="options">
  <div class="option" data-choice="a" onclick="toggleSelect(this)">
    <div class="letter">A</div>
    <div class="content">
      <h3>Title</h3>
      <p>Description</p>
    </div>
  </div>
</div>
```

Multi-select: add `data-multiselect` to the container to let the user select several options. Each click toggles. The indicator bar shows the count.

```html
<div class="options" data-multiselect>
  <!-- Same option markup: the user can select/deselect several -->
</div>
```

Cards (visual design)

```html
<div class="cards">
  <div class="card" data-choice="design1" onclick="toggleSelect(this)">
    <div class="card-image"><!-- mockup content --></div>
    <div class="card-body">
      <h3>Name</h3>
      <p>Description</p>
    </div>
  </div>
</div>
```

Mockup container

```html
<div class="mockup">
  <div class="mockup-header">Preview: dashboard layout</div>
  <div class="mockup-body"><!-- mockup HTML --></div>
</div>
```

Split view (side-by-side)

```html
<div class="split">
  <div class="mockup"><!-- left --></div>
  <div class="mockup"><!-- right --></div>
</div>
```

pros/cons

```html
<div class="pros-cons">
  <div class="pros"><h4>Pros</h4><ul><li>Benefit</li></ul></div>
  <div class="cons"><h4>Cons</h4><ul><li>Drawback</li></ul></div>
</div>
```

Mockup elements (wireframe building blocks)

```html
<div class="mock-nav">Logo | Home | About | Contact</div>
<div style="display: flex;">
  <div class="mock-sidebar">Navigation</div>
  <div class="mock-content">Main content area</div>
</div>
<button class="mock-button">Action button</button>
<input class="mock-input" placeholder="Input field">
<div class="placeholder">Placeholder area</div>
```

Typography and sections

- `h2`: page title
- `h3`: section title
- `.subtitle`: secondary text under the title
- `.section`: a content block with a bottom margin
- `.label`: small uppercase label text

## Browser Event Format

When the user clicks an option in the browser, the interaction is recorded in `$STATE_DIR/events` (one JSON object per line). The file is cleaned up automatically when you push a new screen.

```jsonl
{"type":"click","choice":"a","text":"Option A - simple layout","timestamp":1706000101}
{"type":"click","choice":"c","text":"Option C - complex grid","timestamp":1706000108}
{"type":"click","choice":"b","text":"Option B - hybrid","timestamp":1706000115}
```

The full event stream shows the user's exploration path: they may click several options before confirming. A click records interaction, not necessarily a final decision. Use the user's conversation response as the primary instruction, and do not infer hesitation or consent from a click sequence.

If `$STATE_DIR/events` does not exist, no browser-event evidence is available. Use the user's conversation text; do not infer that they never interacted.

## Design Tips

- Match fidelity to the question: a wireframe for layout, a refinement question for refinement.
- Explain the question on each page: not "is this right?" but "which layout looks more professional?"
- Iterate before proceeding: if the feedback changes the current screen, write a new version.
- At most 2-4 options per screen.
- Use real content when it matters: for a photo portfolio, use real images (Unsplash). Placeholder content hides design problems.
- Keep mockups simple: focus on layout and structure, not pixel-perfect design.

## Filename Convention

- Use meaningful names: `platform.html`, `visual-style.html`, `layout.html`
- Do not reuse filenames: each screen is a new file.
- Iteration: add a version suffix, `layout-v2.html`, `layout-v3.html`.
- The server serves the most recent file by modification time.

## Cleanup

```bash
bash <loaded-skill-directory>/scripts/stop-server.sh "$SESSION_DIR"
```

Use the exact `SESSION_DIR` retained from startup. Read exit status and JSON `status`: `stopped` confirms the process stopped, `not_running` reports no recorded running process, and `failed` leaves cleanup unresolved. If the session used `--project-dir`, the mockup files persist in `.coding-convention/brainstorm/` for later reference. Only `/tmp` sessions are deleted on stop. Cleanup status is separate from whether the design is accepted.

## References

- Frame template (CSS reference): `scripts/frame-template.html`
- Helper script (client side): `scripts/helper.js`
