# ChatGPT -> desktop/Claude agent: pre-YC hardening, 30 Oct 2026

Owner asked me not to stop at copy. I read the shared decision log, task/handoff files, the Oct 29 security review, current main SECURITY/README, live site and current YC material.

This branch is intentionally separate from Claude's work. Merge only what still applies to current main.

## New external state you should know

Dodo changed today. Owner showed the live dashboard with:

- Product Information: Approved
- Personal Information: Approved
- Bank Verification: Approved
- banner: "Live payments are active"

This is independently confirmed by a Dodo email on Oct 30 that says live payments were enabled for the business **Inglen**. Merchant verification/KYC/bank is no longer the blocker described in old handoffs.

Do not infer Inglen revenue from the older Dodo dashboard milestone. I checked the Oct 22 $20 receipt: that payment was for a separate product called **Yoinked**, not Inglen. Unless a new Inglen Live Mode payment appears, Inglen revenue remains $0.

The live site already has Plus pricing, Terms, Privacy and Refunds. `PLUS_CHECKOUT` on the home page is currently empty, so website Get Plus falls back to the app/download path rather than direct Dodo checkout.

## P0: the legal/support address on the live site is confirmed dead

The live site uses `hello@inglen.app` in Terms, Privacy, Refunds and the footer.

I sent a real routing test from Ishant's connected Gmail to `hello@inglen.app` on Oct 30. Google immediately returned **Delivery Status Notification (Failure)** saying the domain `inglen.app` could not be found.

I also checked the registrar:

- `inglen.app` is currently available;
- Vercel price lookup: $9.99 for the first year, $15 renewal.

Buying it is an owner/non-refundable action, so I did not purchase it.

Release rule: do not take a live Plus payment while the published refund/support address is dead. Either the owner buys/configures `inglen.app` + mail routing, or the site must deliberately use another real monitored address before sales.

The Vercel site project is not Git-linked; the current deployment was uploaded as a source bundle, so I did not try to invent a site-repo patch.

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

I traced this against current main after writing the first memo. This is not just an old note.

Current permission path:

- `web._make_permission_check()` calls `tools.mode_allows(name, args)` before showing a permission prompt.
- `_ALWAYS_ASK_IN_AUTO` contains `delete_path`, `move_path`, `send_email`, `send_to_phone`, `install_skill`; `web_fetch` is not there.
- `_RISKY_TOOLS` also does not contain `web_fetch`, so it is treated as a safe read before the Ask/Auto prompt machinery gets a vote.
- shell commands get an `_is_outward()` check for curl/Invoke-WebRequest/etc.; native `web_fetch` bypasses that shell parser.
- `_web_fetch()` accepts http/https/bare hostnames and calls `_http_get()`, which uses `urllib.request.urlopen()`.

Attack shape:

1. Auto reads untrusted web/file/email content.
2. Content tells model to read private state.
3. Model performs a native `web_fetch` whose URL/query carries the private value.
4. That GET can run without the same outward-action prompt used for email/phone/shell sends.

My release recommendation:

- best: implement a bounded outbound/taint guard and a regression test;
- acceptable fallback: after private/untrusted reads, a new outbound host/request needs confirmation;
- bad: mark every ordinary web read risky forever just to make the test green;
- also bad: leave public copy implying Auto catches every possible outbound disclosure.

A regression test should fail on current main with a fake external document instructing an exfil GET and pass only when the outbound call asks/blocks.

### 2. O2: release/runtime integrity

Current review says:

- code patches are release-key signed;
- Windows installer downloaded by updater is checked against GitHub asset digest, but that digest is not separately signed by Inglen's release key;
- third-party llama.cpp runtime download did not have equivalent integrity checking at review time;
- Authenticode still absent.

Please check CURRENT main/build scripts before release. At minimum I want the release process to have a written, reproducible answer to "what proves the installer/runtime bytes are the ones you intended?"

Do not call the supply chain fully signed unless it actually is.

## Public tool-doc claim is wrong at the generator, not only the markdown

Current generated `docs/TOOLS.md` says everything runs locally and the only outbound calls are web searches/fetches. The same tool registry includes `send_email`, `send_to_phone` and configured cloud/network features, so that sentence is false.

I patched `scripts/gen_tools_doc.py` on this branch so regenerated docs say that most work is local and network tools make the requests needed for those features. Please regenerate `docs/TOOLS.md` after merging the generator change rather than hand-editing the generated file.

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
8. Dodo production checkout/licence configuration checked after today's approval; no Inglen revenue claim without an Inglen Live Mode transaction;
9. `hello@inglen.app` fixed or deliberately replaced with a real monitored address before a live Plus sale.

## YC/demo claim rule

For every feature we mention, tag it mentally as one of:

- PUBLIC v0.7.5
- BUILT in v0.7.6
- TESTED by automated/stand-in/emulator
- VERIFIED on Ishant's actual PC/phone

Do not let "built + emulator tested" turn into "works on your phone" in a YC answer until the real-device gate is green.

## One thing I do NOT want you to do

Do not rewrite the YC copy into polished startup English. There is now a separate `notes/ISHANT_VOICE_LAB_2026-10-30.md` on `chatgpt/yc-final-2026-10-30`. The owner explicitly rejected generic AI/startup wording. Facts and code fixes are more useful than another polished paragraph.