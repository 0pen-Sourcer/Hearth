# Public claim contradiction sweep — 30 Oct 2026

This is not a request to polish wording. These are places where two public surfaces currently tell materially different stories.

## P0 before public v0.7.6 / YC links

### 1. Inglen vs JARVIS

Decision/code reality:

- new installs default to assistant name **Inglen**;
- old `~/Jarvis` installs may keep JARVIS for compatibility;
- `JARVIS_*` environment aliases remain intentionally supported.

Current public-facing main still says JARVIS in places a new user will read:

- README hero: "Your own JARVIS, running on your own PC."
- README says "JARVIS is the assistant it comes with";
- README Brains / Team / Memory / Plus / FAQ use JARVIS as the current default;
- `docs/RELEASE_NOTES_v0.7.6.md` repeatedly describes Claude/Codex/etc as "JARVIS's brain", calls, Team behavior and setup around JARVIS despite the first paragraph correctly saying a new install calls it Inglen;
- USER_GUIDE/PHONE/SUPPORT/CONNECTORS/SKILLS and other public docs have person-facing JARVIS occurrences.

Fix rule: change new-user/product prose to Inglen. Keep legacy compatibility text only where it is actually about an old workspace/name/env alias.

### 2. README runtime defaults are stale

Current code/new-install decision vs README:

| Setting | Current new-install behavior | README currently says |
|---|---|---|
| workspace | `~/Inglen` | `~/Jarvis` |
| built-in llama.cpp port | 8790 | 1234 |
| default assistant name | Inglen | JARVIS |

README also says `~/Jarvis` in the FAQ. This can make a clean-install support problem look like user error.

### 3. Privacy absolute vs actual optional/background network

README opens Privacy with:

> Inglen runs on your machine and collects nothing.

The next paragraph documents an opt-in install count that sends an install id/version/OS/brain-kind once a day. The same README FAQ/site privacy also documents update/model-engine/model catalog/network requests.

Use the live-site privacy shape instead: chats/memory/files stay local; list what leaves only when a feature is used; explain the install count is opt-in/off by default. Avoid the absolute "collects nothing" sentence when the document itself lists an optional collection path.

Generated `docs/TOOLS.md` also says the only outbound calls are web search/fetch. That is false because the registry includes email/phone/connectors/cloud-generation paths. `scripts/gen_tools_doc.py` is patched on this branch; regenerate the markdown after merge.

### 4. Auto-mode public promise vs current native web_fetch path

README Safety says Auto "asks before anything that deletes, moves, sends, uploads, pushes, publishes or installs."

v0.7.6 release notes say:

> Deleting, moving or stopping things, sending or uploading anything, pushing, publishing, installing and changing security settings still ask.

Current code has a known counterexample:

- native `web_fetch` is not in `_RISKY_TOOLS`;
- it is not in `_ALWAYS_ASK_IN_AUTO`;
- `_web_fetch()` accepts arbitrary http/https/bare hostnames;
- `_http_get()` calls `urllib.request.urlopen()`;
- therefore an injected prompt can potentially put private data in an outbound GET URL without tripping the same send/upload permission floor.

Preferred fix is code + regression test. If not fixed before release, narrow the wording instead of keeping "sending/uploading anything still asks."

### 5. Live legal contact is not live

Site Terms/Privacy/Refund/footer publish `hello@inglen.app`.

A real Oct 30 message bounced immediately because `inglen.app` did not exist in DNS. Registrar check says `inglen.app` is currently available.

No live Plus sale should rely on the current contact/refund pages until the address is made real or deliberately replaced.

### 6. Dodo approved vs revenue

Dodo email on Oct 30 says live payments are enabled for business **Inglen**. Merchant approval is real.

The older $20 Dodo receipt was for **Yoinked**, not Inglen. Do not let dashboard milestone language become an Inglen revenue claim. Current Inglen revenue is $0 unless a new Inglen Live Mode transaction exists.

## P1 / release-claim discipline

### 7. Twenty-three helper adapters vs phone-session families

The README/release notes can honestly say there are adapters for twenty-three coding agents only if the wording keeps tested vs documented-adapter status clear.

Do not turn this into "twenty-three agents can be resumed from the phone." The current resumable phone-session families are the smaller set implemented through `agent_sessions` (Claude Code, Codex, OpenCode, Gemini CLI and Pi in the current YC notes).

### 8. Built/emulator-tested vs real-phone verified

Phone/Plus pages currently read like finished availability. Before release, tag every path internally as:

- BUILT;
- AUTOMATED/STAND-IN/EMULATOR TESTED;
- VERIFIED ON ISHANT'S ACTUAL PHONE.

Claims about screen-off calls, locked notifications/push and session continuation should match the last category only when that gate passes.

### 9. Signed updates vs signed installer/runtime

True: code-update patches are signed/hash-checked.

Not yet safe to compress into "Inglen updates/downloads are signed" unless the current release work also closes installer/runtime integrity:

- Windows installer is still not Authenticode-signed;
- updater verifies the GitHub asset digest but that digest is not independently signed by Inglen's release key in the Oct 29 review;
- third-party runtime download integrity needs re-check against current main.

## Final sweep before linking GitHub from YC

Search person-facing docs for these exact strings/shapes:

- `JARVIS`
- `~/Jarvis`
- `HEARTH_BUILTIN_PORT | 1234`
- `HEARTH_PERSONA_NAME | JARVIS`
- `collects nothing`
- `nothing leaves`
- `sending or uploading anything`
- `asks before anything risky`
- claims that all 23 adapters are tested

Do not globally replace internal compatibility names. This is a reader-facing sweep.