# Known Fraud Typologies (synthetic)

1. **account_farming** - many distinct cards (card1) transacting from ONE device
   (DeviceInfo + id_33 fingerprint), often with anonymous email. Graph signal:
   high shared-device degree.
2. **shared_email_ring** - many cards under one P_emaildomain. Graph signal: high
   shared-email degree.
3. **card_testing** - one card, burst of tiny-amount transactions in a short
   TransactionDT window (validating stolen card numbers). Signal: velocity spike.
4. **identity_mismatch / ATO** - M1-M9 match flags = F plus dist1/dist2 anomaly;
   new device on an existing card. Signal: mismatch + distance.
5. **(undocumented)** - not every pattern is documented; dense clusters matching
   none of the above are candidate new typologies.
