# Full portal-app look on every screen (Rafi, Oct 30, 2:40 PM)

Rafi: "we gotta make it look a lot more similar to an inbox". He chose the FULL APP LOOK ON ALL SCREENS. Every dashboard screen lives inside one staff patient-portal app frame, as if the room were watching the hospital's real software.

## The app frame (shared by every step)
- **Top bar:** "Hollowveil Memorial · Patient Messages" with a small logo mark (inline SVG), a static search field "Search messages", and the signed-in staff user "J. Rivera · Front desk" (synthetic).
- **Top bar, right:** the honest LIVE/REPLAY badge (unchanged wording) and the progress dots.
- **Left rail:**
  - Folders: Inbox (unread count), Flagged, Drafts, Sent, Archived.
  - Labels: Scheduling, Refills, Billing.
  - At the bottom: "Wisp AI drafts: On" with a small status dot.
- Icons are inline SVG only. No icon fonts, no CDN, no web fonts; it has to work offline.

## Per screen
0. **Title:** the app's sign-in or splash card, centered. "Haunted Inbox" stays the headline with its sub-line; the app frame sits dimmed behind it.
1. **Inbox:** three panes.
   - Message list: avatar initials in a coloured circle, sender (bold when unread), subject, a one-line preview, time ("10:20 AM", "Yesterday", "Mon"), a label chip and an unread dot. msg-004 is selected.
   - Reading pane: subject, sender avatar and name, "to Patient Services", the timestamp, a Reply / Forward / Archive / Flag toolbar, then the body.
   - Caption under it: "Synthetic patients. Nothing is ever sent."
2. **Reveal:** same inbox. The reading pane gets a segmented toggle, "Staff view | What Wisp reads". It flips to the Wisp side and the hidden text glows inside the body as now, labeled "hidden from people, readable by the model".
3. **Possessed:** same inbox.
   - The reading pane shows the message, and below it a reply COMPOSER card titled "✦ Draft by Wisp" filling with the draft, the leaked lines highlighted as now (DOB, MRN, the restricted note, the phone).
   - A right-side "Wisp activity" drawer lists the agent trace rows (tool calls), the non-sender lookup in red.
   - The meter slams in large, then docks as a status strip in the top bar ("POSSESSED", plus the red lines), keeping the composer and the drawer readable.
4. **Whispering:** same layout.
   - The composer holds the template reply.
   - The drawer shows "Wisp output" (the JSON line) and "Code: looked up the sender only".
   - The meter slams in, then docks to "WHISPERING · it heard the ghost, it couldn't act".
5. **Why it works:** an app settings page, "Settings › Wisp › Warding Circle mode", with a toggle shown On and the three cards: the model only classifies / code decides whose chart / templates write the reply.
6. **Closer:** the app's signed-out card: the closer line and the repo URL.

## Rules
- **Projector first:** body text at least 24 px at 1920x1080. Names and subjects are bigger. The meter word stays huge during the slam.
- **No scrollbars and nothing cut off** at 1920x1080 or 1280x720, and the screenshot tests stay green.
- Same data and view models (viewmodel.py). This is a page.html restyle, not new logic.
- Random mode fills the same frame from the variant. /sandbox adopts the frame if there's time; it isn't required.
- The CLAUDE.md wording rules apply to every new string. The fake staff name and labels are synthetic.

## Owner and timebox
- Owner: ui, in page.html only.
- Merge by 3:25 or keep the current look (cut, no partial merge).
- After the merge, ui re-runs `python -m dashboard.export_static` so Pages matches. Cowork Claude re-films the backup video after the 3:30 freeze.
