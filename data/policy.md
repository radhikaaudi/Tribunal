# Fraud Action Policy (synthetic, stands in for HHGOA_IEEE policy)

## Confidence thresholds
- **Block / freeze** requires fraud confidence >= 0.85 OR (confidence >= 0.70 AND exposure >= $1000).
- **Clear / allow** when confidence <= 0.20.
- Between 0.20 and the block threshold: gather more evidence, or step-up / monitor.

## Approval routes
- **Auto**: warn customer, add to watchlist, request step-up auth.
- **L1 analyst**: monitor account, request customer validation.
- **L2 analyst (human)**: block card, freeze account, file SAR.

## Suspicious Activity Report (SAR)
- File a SAR when confidence >= 0.85 AND typology in {account_farming, shared_email_ring,
  card_testing, identity_mismatch}. SAR must record the 5W1H and cite graph evidence.

## Evidence friction
- Prefer low-friction graph evidence. Only request customer contact / step-up when it can
  change the decision (value-of-information positive) and cheaper evidence is exhausted.
