# Skills — write once, share with a link

A **skill** teaches Hearth a repeatable workflow: "to make a slide deck, follow
these steps and use this script." It's just a folder with a `SKILL.md`. The
model sees a one-line summary of every installed skill in its prompt, and loads
the full instructions only when it actually uses one — so you can have dozens
installed without bloating context.

Skills are different from tools and plugins:

| | What it is | Best for |
|---|---|---|
| **Tool** | A built-in callable (`read_file`, `run_command`, …) | The 92 things Hearth can already do |
| **Plugin** | A Python file that adds a *new* tool | "I need a brand-new callable right now" |
| **Skill** | Prose + scripts that orchestrate existing tools | "I know the 8 steps to do X — here they are" |

---

## Anatomy of a skill

```
my-skill/
├── SKILL.md          # required — frontmatter + instructions
├── scripts/          # optional — helpers the steps run (.py / .ps1 / .sh)
├── references/       # optional — docs the model loads on demand
└── assets/           # optional — templates, fonts, icons
```

`SKILL.md`:

```markdown
---
name: clean-downloads
description: Sort the Downloads folder into per-type subfolders (use when the user says their Downloads is a mess)
version: 1.0.0
author: yourname
tools: [list_directory, create_directory, move_path]
---

# Clean up Downloads

1. `list_directory` on the user's Downloads folder.
2. Group files by extension into Documents / Images / Installers / Archives / Other.
3. `create_directory` for each group that has files.
4. `move_path` each file into its group. Never overwrite — if a name clashes, append a number.
5. Report what moved where. Don't touch folders the user already made.
```

**Frontmatter fields**

- `name` — lower-kebab-case slug (what `load_skill` and `/skill` use).
- `description` — one line; this is the *only* thing the model sees until it
  loads the skill, so make it a clear "use this when…".
- `version`, `author` — optional, shown at install.
- `tools` — optional but **recommended**: the tools the skill expects to call.
  Hearth shows this at install so the user knows what the skill will touch. If
  any are risky (`run_command`, `write_file`, `delete_path`, …) the installer
  flags the skill as one that can run code / change files.

---

## Authoring

Fastest way — let Hearth scaffold it:

```
/skill new clean-downloads
```

That drops a `clean-downloads/` folder with a filled-in `SKILL.md` template under
`~/Jarvis/skills/`. Edit the body, drop any helper scripts in `scripts/`, and
it's live immediately (`/skill list` to confirm). You can also just ask Hearth in
chat — when it notices you doing the same multi-step thing twice, it can author a
skill for you.

---

## Sharing

A skill is a folder, so sharing is just publishing that folder.

1. Put your skill folder in a GitHub repo (the repo root, or a subfolder).
2. Share the repo. Anyone with Hearth installs it with one line:

   ```
   /skill install yourname/your-repo
   ```

   or, if the skill is in a subfolder:

   ```
   /skill install yourname/your-repo/clean-downloads
   ```

   They can also paste the GitHub link into chat and Hearth will offer to install
   it. A specific branch or tag: `yourname/your-repo@v2`.

3. Add it to the community index so others find it:
   **[awesome-hearth-skills](https://github.com/0pen-sourcer/awesome-hearth-skills)**
   — open a PR with a one-line entry.

---

## Safety (read before installing other people's skills)

A skill runs with the **same access as Hearth itself** — there is no sandbox.
Its `SKILL.md` is instructions the model follows, and any `scripts/` it ships run
via `run_command`. So treat an installed skill like any other code you downloaded.

What protects you:

- **Install shows you what it does** — declared tools, the scripts it ships, and a
  warning if it can run shell commands or modify files. You confirm before
  anything lands on disk.
- **Installing only places files.** The skill's scripts don't run until the skill
  is actually used, and *running them still goes through `run_command`'s
  permission prompt* — so you get a second look before any code executes.
- Prefer skills from the curated index or authors you trust. Read the `SKILL.md`
  (`~/Jarvis/skills/<name>/SKILL.md`) if unsure.

---

## Commands

```
/skill                       list installed skills (bundled + yours)
/skill install <src>         install from owner/repo, a github URL, or ./path
/skill new <name>            scaffold a new skill under ~/Jarvis/skills/
/skill remove <name>         uninstall a skill you added
```

The model can do the same in chat via `list_skills`, `load_skill`,
`create_skill`, and `install_skill`.
