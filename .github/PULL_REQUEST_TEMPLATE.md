<!-- Thanks for sending a PR! A small note: keep this template's headings; remove the prompts under them. -->

## What this changes

<!-- One paragraph. What did you do, and why. -->

## How I tested it

<!-- Concrete. "Ran `python -m hearth.dev_tools`, called `call new_tool {...}`, got expected output." Beats "it works on my machine." -->

## Screenshots / transcript (if user-facing)

<!-- A CLI session capture or screenshot makes reviews 10x faster. -->

## Checklist

- [ ] My change matches the style of the surrounding code
- [ ] I ran `python -c "import hearth"` and it still imports
- [ ] If I added a tool, it's in `TOOL_DEFINITIONS` and works in the dev_tools REPL
- [ ] If I touched the persona, I augmented it — didn't rewrite the existing voice
- [ ] If I added a dep, it's optional / lazy-imported (Hearth installs in one step today; don't break that)
- [ ] Docs / README updated if user-facing behavior changed
- [ ] Tested on the OS my change affects (and noted which one below)

**Tested on:** <!-- Windows 10 / Windows 11 / macOS / Ubuntu / etc. -->

## Related issues

<!-- Closes #N, refs #M -->
