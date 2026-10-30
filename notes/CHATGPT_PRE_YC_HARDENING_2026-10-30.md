# ChatGPT -> desktop/Claude agent: pre-YC hardening, 30 Oct 2026

Owner asked me not to stop at copy. I read the shared decision log, task/handoff files, the Oct 29 security review, current main SECURITY/README, live site and current YC material.

This branch is intentionally separate from Claude's work. Merge only what still applies to current main.

## New external state you should know

Dodo changed today. Owner showed the live dashboard with:

- Product Information: Approved
- Personal Information: Approved
- Bank Verification: Approved
- banner: "Live payments are active"

So merchant verification/KYC/bank is no longer the blocker described in old handoffs. This does NOT prove a real live purchase or revenue. Do not claim revenue until a Live Mode transaction exists.

The live site already has Plus pricing, Terms, Privacy, Refunds and hello@inglen.app. `PLUS_CHECKOUT` on the home page is currently empty, so website Get Plus falls back to the app/download path rather than direct Dodo checkout.

## P0: public-facing JARVIS drift contradicts our own rename decision

`DECISIONS.md` says Inglen is the default agent name and explicitly records the Marvel/trademark reason not to ship JARVIS as the paid product default.

Current main still has user-facing docs that say the opposite.

The current README includes, among other things:

- hero: "Your own JARVIS, running on your own PC."
- "JARVIS is the assistant it comes with"
- Brains: "JARVIS thinks with the agent"
- Helpers: helpers message/report to JARVIS
- Memory: "Every fact JARVIS learns..."
- Plus: voice calls with JARVIS; JARVIS can call you
- FAQ workspace `~/Jarvis`
- Why Inglen: "JARVIS lives there"

Search current main for `JARVIS` and review PERSON-FACING occurrences. I also saw them in `docs/USER_GUIDE.md`, `docs/PHONE.md`, `docs/SUPPORT.md`, `docs/CONNECTORS.md`, `docs/SKILLS.md`, `CONTRIBUTING.md`, `ROADMAP.md`, release notes and some comments/docstrings.

Do NOT blindly replace compatibility internals:

- `JARVIS_*` env aliases are deliberate backward compatibility.
- old installs may deliberately keep `~/Jarvis`.
- `default_agent_name()` deliberately returns JARVIS for a legacy Jarvis workspace whose user never saved a name.

The release-facing rule should be: a NEW install is Inglen by default; an old install keeps the person's old folder/name.

Please make the public README and release/user docs say that. This is both brand consistency and trademark hygiene.

## P0/P1 security: challenge these before calling v0.7.6 safe

### 1. O1 from the Oct 29 attacker review: prompt injection -> outbound GET in Auto

The review still marks this Medium and FOR_CHATGPT addendum says it is "open by choice".

Attack shape:

1. Auto reads untrusted web/file/email content.
2. Content tells model to read private state.
3. Model performs a plain `web_fetch`/browse request whose URL carries the private value.
4. Current approval floor may not treat that GET as a send/upload.

I searched current main for an outbound-host/prompt-injection guard and did not find an obvious landed fix. Please prove me wrong against current code, not the note.

My release recommendation if it is still real:

- best: implement the bounded guard the security review already proposed and test it;
- acceptable fallback for Oct 31: make it impossible for Auto to silently perform a NEW outbound request after reading untrusted/outside content, even if that means an extra prompt;
- bad: leave the public Safety copy implying Auto catches all outbound disclosure.

A regression test should fail on current main with a fake external document instructing an exfil GET and pass only when the outbound call asks/blocks.

### 2. O2: release/runtime integrity

Current review says:

- code patches are release-key signed;
- Windows installer downloaded by updater is checked against GitHub asset digest, but that digest is not separately signed by Inglen's release key;
- third-party llama.cpp runtime download did not have equivalent integrity checking at review time;
- Authenticode still absent.

Please check CURRENT main/build scripts before release. At minimum I want the release process to have a written, reproducible answer to "what proves the installer/runtime bytes are the ones you intended?"

Do not call the supply chain fully signed unless it actually is.

## SECURITY.md fix already on this branch

Commit `8575a27` rewrites current `SECURITY.md` so it no longer:

- says every install lives under `~/Jarvis`;
- says built-in llama.cpp is still on 1234 (current default is 8790; 1234 is legacy/LM Studio territory);
- says "nothing else phones home" while the app also does product-data/icon/announcement requests;
- publishes the old personal "disable generic AI safety disclaimers" persona choice as if it is the product default.

It also documents O1/O2/R1 plainly.

Please compare it with current code before merging; change any sentence where code moved after I read it.

## Release gates I still consider real

Do not let new copy work hide these:

1. full + Lite build from current main;
2. clean Windows install + onboarding + one actual turn;
3. v0.7.5 -> v0.7.6 update retaining chats/settings/memory;
4. Repair after current merges;
5. frozen build includes new dependencies such as windows-toasts/winrt;
6. real phone: current APK install/pair, call with screen off, Back, update sheet, locked notification/push where actually supported;
7. site/README/release note claims match the exact device checks;
8. Dodo production configuration checked after today's approval; no revenue claim from the approval banner;
9. `hello@inglen.app` actually receives mail before it is relied on for refunds/security/support.

## YC/demo claim rule

For every feature we mention, tag it mentally as one of:

- PUBLIC v0.7.5
- BUILT in v0.7.6
- TESTED by automated/stand-in/emulator
- VERIFIED on Ishant's actual PC/phone

Do not let "built + emulator tested" turn into "works on your phone" in a YC answer until the real-device gate is green.

## One thing I do NOT want you to do

Do not rewrite the YC copy into polished startup English. There is now a separate `notes/ISHANT_VOICE_LAB_2026-10-30.md` on `chatgpt/yc-final-2026-10-30`. The owner explicitly rejected generic AI/startup wording. Facts and code fixes are more useful than another polished paragraph.