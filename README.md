README: Hermes Reflection Hub

This is the autonomous reflection service for all 5 Hermes Trading agents.

Agents monitored:
- MNQ-5M-Candle
- BTC-1H-RSI-Coinbase
- BTC-5M-SupplyDemand
- BTC-5M-EMA-Stochastic
- BTC-5M-BOS-OB

Every 30 minutes:
1. Pull latest code from each agent repo
2. Load trades from state/trades.jsonl
3. Score against goal.yaml
4. Propose ONE variable change if needed
5. Commit and push to agent repo
6. Railway auto-redeploys agent with new strategy

Runs 24/7 on Railway. No local machine required.
