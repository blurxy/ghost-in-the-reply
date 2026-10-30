# Audience screen = a real, fully interactive app. No slides, no timers (Rafi, Oct 30, 3:40 PM)

Rafi, after trying the live dashboard: "the timing as it moves to the next slides is too slow", and the audience screen isn't what he expected. "I need it to be fully interactive. For example, on the page with What Wisp reads vs Staff view, I should be able to go back to Staff view and toggle back and forth, and pretty much use this as a regular messaging system. This is what everyone else is going to see. My section can stay my section. Don't create this whole timer. You don't have to go by a time."

## Rules
1. **No timers anywhere.**
   - No auto-advance, no min_ms holds, no staged reveals that make him wait (trace rows one by one, the slam).
   - Every transition is under 300 ms.
   - Remove the step and demo timers from the presenter view.
   - Autoplay stays only for filming the backup video, behind ?autoplay=1. It's never the default.
2. **The audience screen is the app, not slides.** Everything is clickable with the mouse on omarchy and also works by keyboard:
   - Message list: click any message to open it (all inbox messages, not just msg-004).
   - The **"Staff view | What Wisp reads"** segmented toggle on every open message: click back and forth freely.
   - Patient chat: suggested-message chips (Send #1, Send #2), a textbox with Send, and the chat history stays in place.
   - Role switch **Patient / Front desk ↔ Security team**: clickable in the top bar.
   - **Warding Circle Off/On**: a clickable switch in the Security view.
   - Security view tabs (Model request · Model response · Records touched · Verdict): clickable.
   - Reply / Forward / Archive / Edit draft / Discard can stay decorative; they must not break anything.
   - Back and forward behave the way a person expects (browser history or in-app back).
3. **One source of truth.** Every click on the audience screen POSTs the same action the presenter buttons use (existing /api/action and new actions such as open_message, view_mode, role, guard, chat_send). The server state updates and both screens follow, so clicking on the projector and clicking on the Mac stay in sync.
4. **Rafi's presenter section stays his:** the script line for the current context, every action button (chat sends, role, guard, Show terminal, terminal steps), and the LIVE/REPLAY status. No timer.
5. Live runs still show "Wisp is typing…" while Gemma works, with the LIVE/REPLAY badge on every Wisp reply. The fallback to the saved runs is unchanged.

## Owner and timing
- Owner: ui2 (who is building /chat and the buttons). This replaces the step-driven audience view for Part 1.
- Keep the existing tests passing, and add tests for: the new actions, the message switch, the view toggle round trip, and no auto-advance when autoplay is off.
- Target: merged by 4:15. Rafi presents later. Then Cowork Claude restarts the demo server behind the same Cloudflare link, and Rafi does one full rehearsal.
