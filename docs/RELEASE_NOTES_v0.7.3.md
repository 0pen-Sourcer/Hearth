# Hearth v0.7.3-preview

This one is mostly about things that were quietly lying to you.

**If you're already on v0.7.2, you don't need this installer.** Open Hearth and it'll offer you a 0.7 MB patch instead of a gigabyte. That's the whole reason the patch system exists. If you're on v0.7.0 or v0.7.1 you'll need the installer once, and after that you're on the patchable layout for good.

## Sub-agents can actually work in parallel now

They never really did. Hearth never told llama.cpp to open more than one slot, so a "team" of sub-agents was a queue wearing a costume. It's a real setting now under Settings, Concurrent agents.

The part everyone gets wrong is that llama.cpp splits your context window across slots, so asking for 2 slots at 24K silently hands each agent 12K. Hearth treats your context as per-slot and multiplies for the total, and steps back down on its own if your card can't hold the extra KV cache. On 8 GB you'll probably stay at 1 or 2, and that's fine.

While wiring that up I found a race that only shows up once agents genuinely overlap: fork depth was tracked process-wide, so two agents running at once could clear each other's counter and nest deeper than the limit allows. Fixed.

## Sub-agents can't run destructive commands behind your back

If you'd turned on auto-approve, that also silently applied to sub-agents, which run with nobody watching. So a background agent could have run a delete you never saw.

Now auto-approve only covers commands you can actually see go past. If you genuinely want your agents to have that too, there's a separate switch, `HEARTH_SUBAGENT_AUTO_APPROVE=1`, so it's a thing you choose rather than a thing you inherit.

## Compaction was deleting your conversation

The worst bug in here. When a chat got long, Hearth summarizes the older turns to save room. On reasoning models that summary came back empty, because the whole token budget went to the model's thinking and nothing was left for the actual text. Hearth then replaced your entire earlier conversation with the words `[summary unavailable]`.

So the history was gone, nothing shrank, and it just compacted again on the next message. That's why some of you saw it compacting over and over.

Compaction now refuses to run at all unless it got a real summary back. If summarizing fails you keep the full history, which costs tokens but keeps the conversation.

## The CLI and the GUI can finally see each other

Load a model in one and the other knows. Before this, a server the CLI started looked like a stranger to the GUI, which is how you'd end up reading an error about LM Studio on a machine that has never had LM Studio installed. Eject works from either side now too.

The model name in the top bar also showed the wrong model when you had more than one on disk. It was comparing a full path against a filename, so it just flagged whichever model happened to be listed first.

## Voice

Voice mode could sit on "starting…" forever and never open the mic, because the thing that starts listening was only ever called from the wake word path. Clicking the mic button did nothing at all.

The dot grid is honest now. When it's speaking, the movement is your model's actual voice amplitude and nothing else, instead of a fixed pulse that kept going through silence. When nothing's happening, nothing moves. The desktop HUD stopped shimmering (it was repainting unbuffered, sixty times a second, with the background erased each frame) and it matches the in-app grid instead of being a squashed rectangle a third the size.

It's click-through on purpose so it never blocks what's under it. **Hold Ctrl to drag it.** That was never written down anywhere, which is why it felt broken.

## Smaller things

Images you attach now show up as a thumbnail in your own message instead of the text `[attached: whatever.png]`, capped so a huge screenshot doesn't eat the page.

The status readout said "thinking" for everything, including compaction and prompt processing. It says what's actually happening now.

Error messages stopped assuming LM Studio. If nothing is answering it says so, and if a model isn't found it tells you it's probably a stale saved name rather than telling you to load a model you already loaded.

Chat search used to return the most recent messages when it couldn't find anything, and present them as if they matched. It says when it found nothing now.

Tools the model pulled in on demand were never released, so a long session slowly dragged the prompt back up to full size and a new chat inherited whatever the last one opened. They're capped and reset per chat now.

Onboarding stopped asking which "brain" you want, which meant nothing to anyone, and stopped naming a model that isn't loaded.

## Known

The installer isn't code signed, so SmartScreen will show the blue unknown-publisher box. More info, then Run anyway.

Windows installer only. Linux runs from source. I still don't own a Mac.
