# Professional medical-inbox look (Rafi, Oct 30, 3:18 PM)

Rafi: "make this look like a more professional medical inbox platform." The plan is GPT-6 Astra's. It reviewed screenshots of the merged restyle (brief /tmp/astra/brief3.md, answer /tmp/astra/out3.md, the full text). This file is the build version, with Cowork Claude's corrections in [brackets].

## 1. Light theme (for a lit room and a projector)
```css
:root { --canvas:#EDF2F6; --surface:#FFFFFF; --ink:#172B3A; --muted:#465B6B; --border:#B8C5CE;
        --brand:#155E75; --selected:#E1EFF5; --success:#21633B; --warning:#805400; --danger:#B42318; }
```
- Colour only marks selection and status, and status colour always comes with words.
- [Keep the hidden-text reveal clearly visible but restrained: a --danger outline and a light tint, no neon glow.]

## 2. Typography
- `system-ui, -apple-system, "Segoe UI", sans-serif`.
- Sizes at 1920x1080: body 24/1.4, secondary and controls 20/1.35, page heading 32, pane headings 26, patient name 28, data fields 22. Weights 400/600/700, no all-caps paragraphs.
- `tabular-nums` for dates, times and IDs. Monospace only for tool traces and raw JSON.
- Formats: dates `04 Nov 2026`, times `9:00 AM`, DOB `22 Jul 1985`. Show "All times Eastern" once.

## 3. Clinical conventions, ranked
1. **Patient banner** above the message subject. [Values come from the SENDER's record in records/, never invented. Barnaby p-002 has name, MRN HV-100202, DOB 1985-07-22 and next_appointment; there is no sex, PCP or allergies on file. Show only fields that exist; age is computed from the DOB.]
   - Example: "Barnaby Gourdsworth · 41 years" / "DOB: 22 Jul 1985 · MRN: HV-100202 · Next appt: 04 Nov 2026, 9:00 AM".
   - In the Security view this banner sits right above a draft containing MRN GH0ST-1031, and that mismatch is the story.
2. **Routing and work state:** "Pool: Scheduling · Priority: Routine" / "Status: Needs review · Due: 28 Oct, 5:00 PM".
3. **Provenance:** "Patient portal · Received 28 Oct 2026, 10:20 AM".
4. **Staff sign-off** under the reply: "J. Rivera | Patient Services" / "Hollowveil Memorial Hospital".
5. **Persistent footer:** "Demonstration environment · All patient data are synthetic · No messages are sent".
- No editable clinical fields.

## 4. Components (exact copy)
1. **Message row:** a restrained selected background and left border, with no neon avatars or pills. "Barnaby Gourdsworth" / "Reschedule my knee follow-up" / "Scheduling · Needs review · 10:20 AM".
2. **Reading pane:** the patient banner, then the subject, then the provenance. Reply / Forward / Archive in one toolbar.
3. **Composer:** a white bordered panel titled "Review Wisp's draft", with "To: Barnaby Gourdsworth" and "Draft · Not sent". Buttons "Edit draft" and "Discard draft" are decorative and do nothing.
4. **AI disclosure** directly under the draft: "Wisp generated this draft. Review patient details and reply text before use."
5. **Security view header:** "Security review" / "Wisp: Possessed · Guard off" (or Whispering · Guard on, or Still) / "Cross-patient data found in draft". [Build it from the verdict, as in INSIDE-VIEW-SPEC.] The evidence (audit log, raw tabs) goes below.

## 5. Remove (they look like a toy)
1. Giant verdict typography: verdicts stay pane-sized.
2. Neon magenta, glows and the traffic-light bars.
3. Decorative initials circles and the audience progress dots. [The presenter view keeps its step list.]

## Keep
The REPLAY/LIVE badge, "Review Wisp's draft", the Front desk / Security team switch (key I), Possessed / Whispering / Still, Warding Circle mode, the wording rules, 1080p and 720p with no scrollbars, the tests.

## Order
ui builds this together with the role switch (INSIDE-VIEW-SPEC). Both are CSS and copy on the same page.html, so do them in one pass. Target: merged by 3:50. Then re-export Pages; Cowork Claude re-films the backup video.

## Update 3:22 PM: use Astra's full banner (Rafi: "go with what Astra said")
- Banner, in Astra's layout: "Barnaby Gourdsworth · 41 years · Male" / "DOB: 22 Jul 1985 · MRN: HV-100202" / "PCP: Dr. Mara Vale · Allergies: Not reviewed".
- ghost-writer adds synthetic fields to every patient record in records/: `sex`, `pcp` and `allergies`. Barnaby (p-002) gets Male, Dr. Mara Vale and "Not reviewed"; the other patients get their own synthetic values (sober, no jokes). The banner reads them from the record, so it's never hard-coded.
- Barnaby's DOB and MRN stay as in his record (1985-07-22, HV-100202). Astra's sample values (12 Apr 1984, HV-002041) were placeholders, and changing the record would break the saved real runs in runs/demo and runs/pool.
- Tests: the fixtures schema test learns the new optional fields, and the oracles and canaries are unchanged.
