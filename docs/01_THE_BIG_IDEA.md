# 1. The Big Idea — explained simply

## The problem

A bank sees millions of card payments. Some are **fraud** (a stolen card), most are **normal**.
A computer model gives each payment a **risk score** from 0 to 1 ("how scary is this?").

But that score is often wrong:
- Sometimes it screams "danger!" on a payment that is totally normal (a person just bought
  something expensive on holiday).
- Sometimes it stays quiet on a payment that is actually fraud.

So a human **fraud analyst** has to open each alert, dig through the customer's history,
check the device, look at past cases, and decide: **block the card, ask the customer, or let it go.**
This is slow and boring, and if you block a real customer by mistake, they get very angry
(and the bank loses money — blocking good customers costs banks *more* than fraud does).

## The boring solution everyone builds

Most teams build a **"fraud detector"**: feed in the payment, it says *fraud* or *not fraud*.

That's it. It only ever tries to *prove fraud*. It never tries to *prove innocence*.
So it blocks too many innocent people. Boring, and not very useful.

## Our idea: a **courtroom** (we call it *Tribunal*)

Instead of one detector, we run a tiny **court case** for every alert. Three roles:

```
                    FRAUD ALERT
                        │
           ┌────────────┴────────────┐
           ▼                         ▼
      PROSECUTOR                 DEFENDER
   "This IS fraud,          "This is NORMAL,
    here's my proof"         here's my proof"
           │                         │
     shared device            same device for 8 months
     matches old fraud        merchant used before
     sudden burst             amount is normal
           │                         │
           └────────────┬────────────┘
                        ▼
                      JUDGE
        weighs both sides, checks the rulebook,
        and decides IF there is enough to act
                        │
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
        BLOCK         VERIFY         ALLOW
                   (ask customer
                    first)
```

- **Prosecutor** = looks in the data for *reasons it IS fraud*.
- **Defender** = looks in the data for *reasons it is NORMAL* (a legitimate explanation).
- **Judge** = weighs both, and only acts if one side clearly wins. If both sides are strong,
  it says *"I'm not sure yet — get me one more piece of evidence"* (ask the customer),
  then decides again.

## Why this is better (and not boring)

1. **It has a Defender.** Almost nobody builds this. The Defender is what stops us from
   blocking innocent customers. It actively hunts for the *innocent* story.
2. **It shows its reasoning like a real case** — two sides, then a verdict. Judges of the
   hackathon can literally read the argument.
3. **It handles "I'm not sure."** Real fraud is full of grey areas. Instead of guessing,
   the Judge asks for one more fact (step-up authentication), then updates its decision.
   *This is exactly what the challenge rewards most.*

## The one-line pitch (our tagline)

> **Don't just ask: "Is this fraud?"**
> **Ask: "Can the fraud hypothesis survive its strongest defense?"**

Meaning: we only block a card if the fraud story is so strong that even the *best possible
innocent explanation* cannot explain it away.

## Two real examples from the 20 exam cases

**Example A — the Defender saves an innocent customer (HHG-002)**
- The bank's model gave this a scary **0.79** risk score. A normal detector would block it.
- **Prosecutor:** "The amount is unusually high for this card."
- **Defender:** "But this customer has paid this same merchant **7 times before**, and when
  we asked, the customer said *yes, I made it*."
- **Judge:** The fraud story did **not** survive the defense → **ALLOW.** No angry customer.

**Example B — the Prosecutor wins (HHG-010)**
- Risk score 0.90. Amount $1000, way above this card's normal. Payment from a brand-new device.
- **Prosecutor:** three strong points. **Defender:** found nothing innocent.
- **Judge:** "This is suspicious but let me be sure" → **asks the customer** → customer says
  *"I did NOT make this."* → confidence jumps 61% → 93% → **BLOCK the card + open a case + file a report.**
- Notice the decision **changed** as new evidence arrived. That's the smart part.

**Example C — a device fraud ring (HHG-014)**
- An analyst says "several cards use the same strange device."
- **Prosecutor:** "This device is shared by **27 different cards** in 30 days, and it appears
  in an old confirmed-fraud case." **Defender:** weak points only.
- **Judge:** Fraud survives → **BLOCK + file report + watch all the connected cards.**
  This is a *ring*, and only a graph can see it (more on that in doc 3).

---

👉 Next: **[02_HOW_WE_BUILT_IT.md](02_HOW_WE_BUILT_IT.md)** — how the code actually does all this, part by part.
