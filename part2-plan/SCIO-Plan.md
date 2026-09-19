# The SCIO Rundown

*OptiSigns Take-Home · Part 2 of 2 — Planning*

A step-by-step plan for building a copy of SCIO, OptiSigns’ screen-management platform — what to build first, what to build later, and why. Based on using the live trial the way a real customer would, not just reading about it.

| | |
|---|---|
| Prepared by | Khanh Tran |
| Date | 19 Sep 2026 |
| Timeline | 18 weeks |
| Team size | 6 people |
| Pairs with | Part 1 — OptiBot clone |

## What the app actually does

*Walkthrough*

SCIO has six main sections. **Screens** is where you manage your devices and pair new ones with a code. **Files/Assets** is a media library, plus a marketplace of 100+ ready-made apps — Weather, ESPN News, Canva, Google/Microsoft Workspace, social media feeds. **Playlists** let you put content in order and set how many seconds each item shows for. **Schedules** is a full weekly/monthly calendar, not just a start-and-end date. **Templates** is a complete drag-and-drop design tool with its own AI assistant. And **Engage** adds touch-screen kiosks, QR codes, and IoT sensors — but only on a pricier plan than Screens/Playlists need.

Behind those six sections is an account layer most product demos skip: team members and permissions, billing (including billing for sub-accounts), a built-in store to order physical screens, and an advanced settings page with single sign-on, an API, custom branding, and support for multiple business locations. The public pricing page confirms this shape: Standard ($10/screen/month) and Pro Plus ($15) cover everything above; Engage ($30) unlocks the interactive features; Enterprise ($45, 25-screen minimum) unlocks private hosting and a dedicated account manager. That pricing breakdown is basically OptiSigns’ own scope document, and it’s what this plan borrows from the most.

## One thing I didn’t expect — and how it changed the plan

*What surprised me*

**I assumed “Templates” was just a panel inside SCIO. It isn’t — it’s a separate product.**

Opening it loads a different website inside a frame: `canvas.optisigns.com`, with its own homepage, its own saved-designs page, and its own AI design assistant. The preview images come from a third address (`signagecloud-prd-cdn.optisigns.com`), and file downloads back in Files/Assets use a fourth one (`smallapp.optisigns.com`). Proof of Play — the report that shows what actually played on each screen — tells the same story from the other side: it’s off by default. You have to click an explicit “Enable Now”, and the app warns that it “will slightly increase network usage on your devices”, because it’s a real, ongoing stream of data from every screen, not something that comes for free.

So SCIO isn’t one single app with a design tool and an analytics tab bolted on. It’s a lightweight control panel that connects several separate, loosely-linked services, each with its own address and storage.

That changed how I planned the work below. Instead of treating “Designer” and “Apps/Analytics” as features the core team squeezes in partway through, I planned them as separate workstreams with their own people and their own timeline, built after the core — screens, content, schedules, sending content to screens, and the player — is solid. That’s likely the same order the real product grew in, which is why Phase 4 and Phase 5 below can run alongside other work instead of blocking it.

## Build order

*Order of work · 18 weeks · 6 people*

Every phase either removes a risk or adds more capability, and risk comes first. The one part of SCIO with no ready-made solution is keeping a screen and the cloud in sync — pairing, delivery, playback. That’s scheduled early, while the team is small and can still change direction cheaply, instead of being discovered as a design problem three months in.

| Phase | Weeks | Length | What |
|---|---|---|---|
| P0 | 1–2 | 2 weeks | Foundations |
| P1 | 3–5 | 3 weeks | Content & pairing |
| P2 | 6–8 | 3 weeks | Scheduling & delivery |
| P3 | 9–10 | 2 weeks | Reliability & reporting |
| P4 | 11–13 | 3 weeks | Design tool (MVP) |
| P5 | 14–15 | 2 weeks | Add-ons proof |
| P6 | 16–18 | 3 weeks | Hardening & pilot |

### P0: Foundations (weeks 1–2)

Set up accounts, logins and permissions. Set up the build/test pipeline and a staging environment. Design the database for Screens, Playlists and Schedules. Build an empty version of the dashboard.

**Why first —** everything else depends on accounts and organizations working. It’s also predictable, well-understood work, which helps the team find its real working speed before tackling anything riskier.

### P1: Content & pairing (weeks 3–5)

Build file upload and a media library. Build a playlist editor (put items in order, set how many seconds each one plays). Build the 6-digit pairing code flow. Build a simple web-based player that can register itself, check what playlist it’s assigned, and play it in a loop — and if it loses its internet connection, it keeps playing its last playlist instead of going blank, from the very start, not as something added later.

**Why now —** keeping a screen and the cloud in sync is the one part of this project with no off-the-shelf answer. Solving it early, in weeks 3–5 while the team is still small, is much cheaper than finding a design flaw at month three.

### P2: Scheduling & delivery (weeks 6–8)

Build a calendar for assigning playlists to screens or groups, for specific dates and times (each screen keeps its own time zone, not the company’s). Build a way to push updates to screens right away, with regular re-checking as a backup if the instant push fails. Show whether each screen is online or offline.

**Why here —** this builds directly on the pairing work from the last step. It’s the feature that turns “a screen showing one playlist” into the actual product: real control over what plays, where, and when, from anywhere.

### P3: Reliability & reporting (weeks 9–10)

Add a remote refresh/restart command. Add optional playback reporting — off by default, matching how the real product does it to save bandwidth. Add a screen-health view showing when each screen was last seen and what it’s currently showing.

**Why not sooner —** reporting and remote fixes only matter once there’s a real group of screens running real schedules from the step before. Building this earlier would leave nothing to actually watch.

### P4: Design tool (MVP) (weeks 11–13)

Build a simple drag-and-drop editor — text, images, shapes, backgrounds — that produces content the player can show, plus 8–10 ready-made templates. Deliberately much simpler than Canva.

**Why here, not earlier —** what I found above shows this is a separate part of the real product. That means it can be built and improved on its own schedule without slowing down the scheduling work, so it runs alongside other steps instead of blocking them.

### P5: Add-ons proof (weeks 14–15)

Build a simple plug-in system for adding new content types, then build three examples on top of it: a weather widget, a news/RSS feed, and a YouTube video — all using free, license-free sources.

**Why three, not a hundred —** the goal is just to prove the plug-in system works. The 100+ integrations in the real app are mostly about getting licensing deals with content providers like ESPN, which is a business task, not an engineering one, and doesn’t belong in this timeline.

### P6: Hardening & pilot (weeks 16–18)

Polish roles and permissions. Add a basic billing setup. Load-test the update system against a simulated fleet of a few hundred screens — updates should roll out gradually rather than all at once, so the servers don’t get overloaded. Run the whole thing with a small internal group.

**Why last —** testing for scale and edge cases is only meaningful once everything from the earlier steps exists to put under load. Anything found here becomes part of next month’s plan, not a last-minute surprise.

## Who’s on it

*Team*

| Role | People | Owns |
|---|---|---|
| Tech lead / PM (hands-on) | 1 | Scope decisions, account/login design, keeping the phases on order |
| Backend — platform | 1 | Accounts, permissions, media storage, the playlists API |
| Backend — delivery | 1 | Pairing, sending updates to screens, online/offline status, playback reports |
| Frontend — dashboard | 2 | Screens/Playlists/Schedules screens, the design tool MVP |
| Player app | 1 | The web-based player, offline playback, remote commands |

## What’s in, and what’s left out on purpose

*Scope decisions*

Which device platform to build first: **web-based, first.** The real onboarding screen itself offers “use your laptop as a screen” right alongside native downloads for Windows, macOS, Android, Linux and Raspberry Pi — proof that native players are numerous and different for each platform. Building all five ourselves is a hardware/operating-system problem, not a signage problem, so this plan ships one browser-based player that covers laptops and most common displays, and leaves native versions as a documented next step rather than something we build now.

### Building this

- **Accounts, teams, permissions**: One company per account — not the reseller/multi-account setup hinted at by Sub-Account Billing
- **Screens, pairing, groups**: The 6-digit code flow seen in onboarding
- **Files/Assets + storage**: Upload and a library for images, video, documents
- **Playlists**: Ordered items, a duration per item, sending to screens
- **Calendar scheduling**: Week/day/month views, a time zone per screen
- **Web-based player**: Registers, checks in, plays content, survives being offline
- **Basic playback reporting**: Off by default, like the real product
- **Design tool (MVP)**: Text/image/shape editor, 8–10 templates
- **3 example add-ons**: Proves the plug-in system, not a full marketplace

### Leaving out, and why

- **The full 100+ app marketplace**: Mostly a content-licensing effort once the plug-in system exists, not more engineering
- **The Engage tier (kiosks, IoT sensors, AI camera)**: A different type of hardware (touch/sensors) and its own research project, not needed to prove the core works
- **Enterprise (private hosting, single sign-on, public API)**: Sales-driven features that only pay off once there are paying customers on the core product
- **Ordering physical screens in-app**: A retail/shipping problem, not a signage-software one
- **AI-powered analytics dashboards**: Needs real playback data from the reporting feature first — there’s nothing to analyze yet
- **Multi-account (reseller) billing**: A big project on its own; one account per company is enough for a pilot

## Risks & assumptions

*What could go wrong*

- **Sending updates to thousands of screens at once**: If every screen checks in at the exact same moment, the servers get overloaded. Phases 2 and 3 plan for staggering these check-ins, instead of assuming one broadcast just works.
- **Staying on when the internet drops**: A screen that loses its connection mid-schedule should keep playing its last playlist, not go blank. This is a requirement from Phase 1, not something patched in later.
- **Content licensing**: The marketplace apps we saw, like ESPN and Weather, are licensing deals as much as they are code. The three example add-ons in Phase 5 deliberately use only free, license-free sources.
- **Time zones across locations**: A schedule means nothing without knowing which time zone the screen is in — not the company’s time zone. “Functional Locations” in the real product is the clue that this matters from day one for any business with more than one site.

## How we’d know it’s working

*Landing it*

The team runs the product on about 10 of its own screens by the end of Phase 3, once scheduling, delivery and the player are all live. The first outside pilot — two or three friendly customers, one location each — happens at the end of Phase 6. Everything marked “leaving out” above becomes next month’s roadmap conversation, backed by real feedback from that pilot, instead of a last-minute surprise.

---

*The SCIO Rundown — Part 2 of 2 · Paired with the OptiBot Mini-Clone build in Part 1*
