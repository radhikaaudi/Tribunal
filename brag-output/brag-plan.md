# Brag Plan: Tribunal

## What is this app?
An agentic fraud investigator on TigerGraph: it investigates an alert, weighs evidence for and
against fraud, knows when it's uncertain, grounds every recommendation in policy, and recommends a
defensible next action — then files a SAR only when the rules require it.

## The angle
Tribunal is literally a courtroom for a fraud alert: **prosecution vs. defence → a verdict.** The
hero moment is the confidence meter moving from *uncertain* to a decision as evidence arrives — the
product's actual signature UI. The premise: a risk score is a question, not a verdict.

## Hook (first 2–3 seconds)
Black screen, serif type: **"Every fraud alert is a question."** → **"Most are false alarms."**
An amber rule under the words. Sets up why a verdict engine matters.

## Key moments (the middle)
- The **confidence meter** filling 25% → 93%, colour shifting amber → red, label flipping
  **uncertain → VERIFY → BLOCK** (the real app UI).
- Three capability cards arriving one by one: **VERIFY before it blocks (R1)** · **Grounded in
  policy — GraphRAG** · **Files a SAR only when the rules say so**.

## Outro / punchline
⚖️ **TRIBUNAL** — "From an uncertain signal to a defensible verdict." · small line: Built on TigerGraph · HHGOA 2026.

## User flow worth showing
entry (an alert / risk score) → key action (weigh evidence; the belief meter moves; verify) →
result (a verdict + next-best-action, SAR when required). The centerpiece is the belief meter.

## Tone
- Preset: polished
- Creative direction: a courtroom for fraud — weigh the evidence, reach a defensible verdict.
- Interpretation: confident restraint; serif gravitas; longer holds; the meter is the star, not motion for its own sake.

## Format: landscape — 1920x1080
## Duration: ~20.5s

## Visual identity (from the project)
- Background: #0b0e13 (dark, matches the analyst console)
- Accent: #d97706 (amber — the app's "uncertain" colour, the differentiator)
- Verdict colours: #16a34a clear · #d97706 uncertain · #dc2626 block (the meter's real colours)
- Text: #f5f7fa · muted #8b93a1
- Display font: Georgia (serif, courtroom feel) · Body font: Helvetica/Arial
- Strongest visual element: the fraud-confidence meter (recreated from app/streamlit_app.py)

## Share copy (draft)
Tribunal — an agentic fraud investigator on TigerGraph that turns an uncertain signal into a defensible verdict.

## Audio direction
- Role: confident business bed, restrained.
- Music: happy-beats-business-moves-vol-1 (bundled). Volume ~0.6, fade out under the outro.
- Music treatment: steady bed; fade-out over the last ~2s.
- Music cue guidance: natural timing chosen over beat-locking to protect readability of the hook + meter (allowed by step-3 checklist).
- Audio-reactive treatment: none (keep it clean/polished).
- SFX posture: sparse — none required; the meter and card reveals carry the motion.
- Restraint rule: no strobing, no waveform visuals, no rushed text.

## Storyboard

### Scene 1 — Hook — 4.0s
Black. "Every fraud alert is a question." holds, then "Most are false alarms." with an amber underline.
Sequential/interaction: two lines appear in sequence, each held to read.
Audio intent: bed enters softly.
Transition mood: soft crossfade → Scene 2

### Scene 2 — Name reveal — 3.5s
⚖️ TRIBUNAL in large serif, subtitle "Agentic fraud investigation on TigerGraph".
Audio intent: bed settles.
Transition mood: clean → Scene 3

### Scene 3 — The belief meter — 5.5s
Recreate the confidence meter. Bar fills 25% → 93%; colour amber → red; number counts up; label
flips uncertain → VERIFY → BLOCK. Caption: "It weighs prosecution against defence — and knows when it's uncertain."
Sequential/interaction: yes — the meter fills and the number ticks up.
Transition mood: clean → Scene 4

### Scene 4 — Capabilities — 4.5s
Three cards arrive one by one: VERIFY before it blocks (R1) · Grounded in policy (GraphRAG) · Files a SAR only when the rules say so.
Sequential/interaction: yes — three cards, staggered, each held to read.
Transition mood: soft → Scene 5

### Scene 5 — Outro — 3.0s
⚖️ TRIBUNAL + "From an uncertain signal to a defensible verdict." + small "Built on TigerGraph · HHGOA 2026". Music fades.
Transition mood: hold on logo.

**Music mood for this video:** confident/business, restrained.
**Audio summary:** a steady confident bed that enters on the hook, carries the meter payoff, and fades under the final logo.
