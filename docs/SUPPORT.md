# Support Hearth

Hearth is free, open-source (MIT), and built by one high-schooler in spare time. There's no company, no funding, no cloud bill being subsidized — just someone who wanted a local Jarvis and decided to share it.

If Hearth saved you time, made you smile, or just feels like the thing you wished existed — a small tip genuinely helps me keep building (and helps with the very real cost of being a student who wants to keep doing this).

**No pressure, ever.** A GitHub ⭐, a bug report, or a PR is just as valuable as a coffee. Maybe more.

---

## Ways to support (pick any)

| Option | Link | Notes |
|---|---|---|
| ⭐ **Star the repo** | top of [the repo](https://github.com/0pen-sourcer/hearth) | Free. Costs you a click. Helps more than you'd think — it's the #1 discovery signal on GitHub. |
| ☕ **Ko-fi** | _(coming soon)_ | One-time or monthly. Accepts UPI + cards. |
| 💜 **GitHub Sponsors** | _(coming soon)_ | Monthly, shows a badge on your profile. |
| 🪙 **UPI (India)** | _(coming soon)_ | Direct, instant, no fees. |
| 🐛 **Contribute** | [issues](https://github.com/0pen-sourcer/hearth/issues) | Report bugs, suggest features, send PRs. |

---

## For the maintainer — how to actually set this up (India, high-schooler)

> Notes-to-self for wiring up the donation options safely. Delete this section before launch if you want, or leave it — transparency is fine.

### ⚠️ Safety first

**NEVER put your raw bank account number (SBI account no. + IFSC) on a public GitHub.** That's a fraud and social-engineering magnet — people can use it for scams, fake "refund" cons, and worse. Bank account numbers are not designed to be public.

What IS safe to share publicly:
- **A UPI ID / VPA** (like `yourname@oksbi`, `yourname@paytm`). These are *designed* to receive money from strangers — that's the whole point. Worst case someone sends you money. There's no "pull" risk.
- **A Ko-fi / Buy-Me-a-Coffee / GitHub Sponsors page.** These sit between you and the donor — the donor never sees your bank details.

### Option A — Ko-fi (recommended, lowest friction)

1. Sign up at [ko-fi.com](https://ko-fi.com) — free, no monthly fee, takes 0% on donations (they ask for an optional tip to *them*).
2. **Age:** Ko-fi's TOS requires 18+ for payouts in most regions. If you're under 18, use a parent/guardian's account with their permission, or wait. (You said you're a high-schooler — check this honestly; getting an account frozen for TOS violation is worse than waiting.)
3. Connect **Stripe** or **PayPal** for payouts. Stripe supports India; PayPal works but has higher fees on INR.
4. Your page becomes `ko-fi.com/yourusername`. Put `yourusername` in `.github/FUNDING.yml` under `ko_fi:`.
5. GitHub shows a "Sponsor" button automatically once FUNDING.yml has it.

### Option B — UPI direct (India-native, instant)

1. You already have a UPI ID from PhonePe / GPay / Paytm / BHIM (it looks like `name@okhdfcbank` or `9999999999@paytm`).
2. **A UPI VPA is safe to publish.** It can only receive, never withdraw.
3. Generate a UPI QR code (your UPI app → "My QR" / "Receive money" → share/save the image).
4. Drop the QR image at `docs/support-upi.png` and link it here. Or just write the VPA in plaintext.
5. Add to FUNDING.yml: `custom: ['upi://pay?pa=yourvpa@bank&pn=YourName']` — though most desktop users can't click a `upi://` link, so a QR image + the plaintext VPA on this page works better.

### Option C — GitHub Sponsors (most credibility, more setup)

1. Apply at [github.com/sponsors](https://github.com/sponsors). India is supported via **Stripe Connect**.
2. Requires: 18+, a bank account Stripe can pay into, ID verification.
3. Approval takes a few days to a couple weeks.
4. Once live, add `github: 0pen-sourcer` to FUNDING.yml — GitHub shows a heart button on the repo.

### How to ask without it feeling like begging

You're not begging. You built something useful and gave it away for free. Asking for optional support is normal and respected in open source. The tone that works:

- **Lead with the free value.** "Hearth is free and always will be."
- **Make the ask small and optional.** "If it helped, a coffee keeps me shipping. No pressure — a star helps just as much."
- **Be honest, not sob-story.** "I'm a student building this in my spare time" is relatable and true. Don't over-explain your finances; one honest line lands better than a paragraph.
- **Put the star ask FIRST.** Most people won't pay but will happily star — and stars are what actually grow the project.

The README has a one-line `## Support` section pointing here. That's the right amount of visible — present but not nagging.
