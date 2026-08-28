# v0.7.6 launch posts

Rules applied throughout: no em-dashes, no prose colons, no competitor comparisons, no marketing voice, no star-begging. Always include the GitHub link, someone searching the name alone landed on an unrelated plugin.

Link everywhere: https://github.com/0pen-Sourcer/Hearth

---

## Product Hunt

**Name:** Hearth
**Tagline:** A local JARVIS that actually runs your computer
**Topics:** Artificial Intelligence, Open Source, Productivity, Windows

**Description (the box under the tagline):**

Hearth runs on a model on your own machine and controls the actual computer. It opens apps, reads and writes your files, drives a browser you can watch, lays your windows out where you want them, talks and listens, and remembers you between sessions. No account, no cloud required, no telemetry. Windows installer, MIT licensed.

**First comment (post this yourself, right after launching):**

I built this at 17 because every local AI I tried could only chat.

You can run a great model on your own GPU now, but the good interfaces either just talk to you, or they are coding tools locked inside a code folder. Nothing used the computer the way a person does. So Hearth does that. It opens your apps, edits your files, drives a real browser you can watch it use, arranges your windows, and remembers what you told it last week. Everything stays on your machine unless you explicitly ask it to search the web.

This release was mostly unglamorous reliability work. Making it type into the right window instead of whichever one grabbed focus, letting it click a control by number rather than guessing a pixel, and getting it to admit when it cannot see the screen instead of inventing an answer.

It is free and MIT. If you have a decent GPU sitting idle, I would genuinely like to know what you would want it doing while you are away from the keyboard.

---

## LinkedIn

Lead with the number, not the pitch.

> 305 people have installed something I built alone at 17.
>
> Hearth is a local-first AI for Windows. It runs on a model on your own machine and controls the actual computer, files, apps, a browser, the desktop itself. Nothing leaves your PC.
>
> The last release was almost entirely unglamorous work. Making it type into the right window, remember things without being told to, and say honestly when it cannot see the screen instead of guessing.
>
> That is most of what building something real turns out to be.
>
> Free and open source: github.com/0pen-Sourcer/Hearth

---

## r/LocalLLaMA

Lead with the technique, not the product. This crowd engages on how it works.

**Title:** How I got reliable computer-use out of small local models, numbered controls instead of pixel guessing

**Body:**

I have been building a local-first assistant that drives a Windows desktop, and the thing that finally made it reliable was not a bigger model.

Three problems kept killing it. The model would click a control, then type seconds later after focus had already moved, so the keystrokes went somewhere else. Bringing a window to the front reported success even when Windows silently refused, so everything after that acted on the wrong window. And clicking meant estimating a pixel from a screenshot, which small models are bad at.

What fixed it, in order of how much it mattered:

Numbered controls. The accessibility tree already knows where every control is, so I draw a number on each one in the screenshot and the model picks a number. This is set-of-mark grounding, the same idea as OmniParser and UI-TARS, except it needs no detector model because the coordinates are already known. A 9B model picking from a list beats a much larger one estimating coordinates.

Atomic click and type. Clicking and typing are one operation now, so there is no window for focus to drift through.

Focus verification. Every keystroke checks that the intended window is actually in front, and refuses if it is not.

The remaining hard case is browsers, since Chrome keeps its renderer accessibility off until something asks for it, so a snapshot returns tabs and toolbar buttons with none of the page. For that it attaches to a Chrome running with the DevTools port and reads the DOM directly, which also avoids the bot-detection you get from a fresh automated profile.

Everything runs against any OpenAI-compatible endpoint. Code is MIT: github.com/0pen-Sourcer/Hearth

---

## Broadcast (in-app, for existing installs)

Title: `v0.7.6 is out`

Body:
> Desktop control got a lot steadier. It verifies which window is in front before typing, clicks controls by number instead of guessing pixels, and can arrange your windows in one go. Memory now saves facts on its own again, and loading a model shows real progress. Update from Settings.
