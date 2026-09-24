# Hyperframes Composition Brief: Tribunal

## Objective
A short, polished launch-style brag video for Tribunal — an agentic fraud investigator on TigerGraph.

## Output
- Composition directory: `brag-output/composition/`
- Rendered video: `brag-output/brag.mp4`
- Format: landscape — 1920x1080
- Duration: ~20.5s

## Source Material
- Project root: /Users/macbook/Documents/PROJECTS/hhgoa/Tribunal
- Primary files read: README.md, app/streamlit_app.py (the confidence-meter UI + colours), tribunal/investigator.py
- Product name: Tribunal
- Tagline / strongest claim: "From an uncertain signal to a defensible verdict."
- Key UI to recreate: the fraud-confidence meter (bar + label CLEAR/uncertain/BLOCK, colours from streamlit_app.py)
- Copy that must appear verbatim:
  - Every fraud alert is a question.
  - Most are false alarms.
  - fraud confidence
  - VERIFY before it blocks (R1)
  - Grounded in policy — GraphRAG
  - Files a SAR only when the rules say so

## Creative Direction
- Tone preset: polished
- Creative direction: a courtroom for fraud — weigh the evidence, reach a defensible verdict.
- Angle: the confidence meter moving from uncertain to a decision is the hero; a risk score is a question, not a verdict.
- Hook: "Every fraud alert is a question." → "Most are false alarms."
- Outro: ⚖️ TRIBUNAL — "From an uncertain signal to a defensible verdict."
- Avoid: generic SaaS language, abstract filler, redesigning the product.

## Visual Identity
- Background: #0b0e13
- Text: #f5f7fa (muted #8b93a1)
- Accent: #d97706 (amber); verdict green #16a34a, block red #dc2626
- Display font: Georgia (serif) · Body font: Helvetica/Arial (web-safe, no external fonts for render safety)
- Visual references: the analyst console's dark theme + the confidence meter bar

## Storyboard
See brag-output/brag-plan.md. Scene summary:
1. Hook — 4.0s — two serif lines
2. Name reveal — 3.5s — ⚖️ TRIBUNAL + subtitle
3. Belief meter — 5.5s — bar 25%→93%, colour amber→red, count-up, label uncertain→VERIFY→BLOCK
4. Capabilities — 4.5s — three cards one by one
5. Outro — 3.0s — logo + tagline, music fades

## Audio
- Audio role: confident business bed, restrained.
- Music: assets/music/happy-beats-business-moves-vol-1-by-ende-dot-app.mp3, volume ~0.6, fade out ~2s under the outro.
- Music cue guidance: natural timing chosen over beat-locking to protect readability (allowed).
- Audio-reactive: none. SFX: none required.
- Audio files: copied into composition/assets/music/.

## Implementation notes
- Single self-contained index.html (no sub-compositions, no A-roll video).
- One paused GSAP master timeline registered on window.__timelines["master"]; scenes are full-frame
  `.clip` layers faded in/out at absolute timeline positions; deterministic only.
- Run `npx hyperframes check` before render (the single gate).
