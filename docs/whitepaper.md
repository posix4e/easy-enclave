---
layout: default
title: whitepaper
---

# whitepaper

## EasyEnclave: Compute Commitments as Currency

*Hardware-attested compute that trades like money.*

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   THE CURRENCY = MACHINE-MONTHS OF COMPUTE                  │
│   TRUST = TDX ATTESTATION                                   │
│   PRICE = WHATEVER THE MARKET DECIDES                       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## abstract

Blockchains are:
- **wasteful** - 1000 nodes run the same code
- **slow** - consensus takes seconds to minutes
- **not confidential** - all data is public
- **not general purpose** - only good for replicating public state
- **speculative** - tokens disconnected from real utility

Cloud providers say "just trust us." No proof. No transparency.

EasyEnclave creates a compute currency:
- **machine-months** are the unit of value
- **TDX attestation** provides hardware trust
- **pre-signed commitments** work offline
- **dynamic pricing** - the market decides

No tokens. No gas. No speculation. Just compute that trades like money.

---

## the problem

### blockchains

1000 nodes run the same code to agree on state. Wasteful.

### tokens

Volatile. Speculative. Price disconnected from utility.

### cloud providers

"Trust us." No proof of execution. No transparency.

---

## the solution

### compute commitments

Nodes issue **signed promises** to provide compute:

```json
{
  "node_id": "node-abc",
  "capacity": "1 vCPU-month",
  "valid_from": "2024-02-01",
  "valid_until": "2024-03-01",
  "price_usd": 50.00,
  "signature": "<TDX-attested signature>"
}
```

These commitments:
- can be verified **offline** (signature check)
- are **tradeable** (user A sells to user B)
- are **redeemable** for actual compute
- are **backed by stake** (slashable)

### why this works

```
┌────────────────┐
│ MACHINE-MONTH  │
│                │
│ 1. Verifiable  │ ← TDX quote proves node is real
│ 2. Tradeable   │ ← transfer to anyone
│ 3. Redeemable  │ ← use for actual compute
│ 4. Backed      │ ← node stakes collateral
└────────────────┘
```

---

## trust model

nodes provide two things to assure different security properties:

```
┌─────────────────────────────────────────────────────────────┐
│                    TRUST DECOMPOSITION                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  SIGNED PROMISES (TDX attestation)                          │
│  ├─ Integrity      → code runs exactly as specified         │
│  └─ Confidentiality → data never leaves the enclave         │
│                                                             │
│  STAKES (slashable collateral)                              │
│  └─ Availability   → economic incentive to stay online      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**signed promises** are cryptographic guarantees. the CPU signs what's running. you can verify offline. no trust required.

**stakes** are economic guarantees. nodes put up collateral. if they go offline and cause problems, they lose it. skin in the game.

together: hardware proves correctness, economics ensures uptime.

### the control plane

the control plane is itself an agent running in TDX - not special infrastructure:

```
┌─────────────────────────────────────────────────────────────┐
│                      CONTROL PLANE                           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Just another TDX agent with a special role:                │
│  ├─ Bootstraps the network                                  │
│  ├─ Tracks who's contributing what                          │
│  └─ Verifiable the same way as any other workload           │
│                                                             │
│  Continuous Verification:                                   │
│  ├─ Re-attests nodes (TDX quotes still valid?)              │
│  ├─ Health checks (node still online?)                      │
│  └─ Updates trust scores based on behavior                  │
│                                                             │
│  Authoritative Ledger:                                      │
│  ├─ Commitments issued (who, when, how much)                │
│  ├─ Redemptions (work performed)                            │
│  ├─ Current balances (points/credits)                       │
│  ├─ Stake amounts per node                                  │
│  └─ Transfer history                                        │
│                                                             │
│  Routing & Proxy:                                           │
│  └─ Routes traffic to apps at appname.app.easyenclave.com   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

contracts are agents. the control plane is a special agent. same trust model for both - TDX attestation proves what's running.

nodes do the compute. the control plane continuously verifies, keeps score, and routes traffic.

---

## supply architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    COMPUTE SUPPLY                           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  EasyEnclave Direct (Backstop)                              │
│  - Always available                                         │
│  - VERY expensive (priced to rarely be used)                │
│  - Emergency capacity only                                  │
│                                                             │
│  Network Nodes (Dynamic Market)                             │
│  - Third-party TDX hosts                                    │
│  - Nodes set their own prices                               │
│  - Users choose based on price + reputation                 │
│  - Staked + slashable                                       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**No hardcoded prices.** The market decides everything.

### supply constraint

never issue more capacity than real demand:

```
if (network_capacity_issued > real_demand):
    STOP issuing new commitments
```

prevents:
- over-promising compute that doesn't exist
- bank runs
- price collapse from oversupply

---

## economics

### trading machine-months

**peer-to-peer trades have ZERO overhead.**

```
Alice has: 2 machine-months
Bob wants: compute

Alice → 2 machine-months → Bob
Bob → $95 → Alice

EasyEnclave cut: $0
```

trade freely. no fees. no middleman.

### running a node

**providing compute has ZERO overhead.**

```
Node provides: 1 month of compute
User pays: $50 (market rate)
Node receives: $50

EasyEnclave cut: $0
```

you keep everything you earn.

### cashing out to USD

**only the official exchange takes a cut.**

```
Node has: 2 machine-months (worth $100)
Node wants: USD

Exchange rate: 2:1
Node gives: 2 machine-months
Node receives: $50 USD

EasyEnclave receives: $50 (the other half)
```

the 2:1 rate means:
- EasyEnclave gets 50% when you cash out
- discourages frivolous withdrawals
- incentivizes keeping value in the network
- or finding peer-to-peer buyers instead

---

## liquidity: three exit paths

### path 1: use it

redeem for actual compute. full value.

```
1 machine-month → 1 month of compute
```

### path 2: sell to other users

find someone who wants compute. market rate.

```
seller has: 2 machine-months
buyer wants: compute

seller gets: ~$95 (market price)
buyer gets: 2 machine-months
```

no cut. no fees. peer-to-peer.

### path 3: exchange to USD

**EasyEnclave Official Exchange**
```
rate: 2:1 (you get 50% in USD)
KYC: required
availability: may close if low on funds
```

**Third-Party Exchanges**
```
rate: better than 2:1 possible
KYC: optional (operator's choice)
software: open source, anyone can run
```

this creates an exchange ecosystem:
- third parties compete on rates
- EasyEnclave provides backstop (when open)
- users choose based on rate vs KYC needs

**we want to be put out of business.**

if someone builds an exchange at 1.5:1, use them. if someone builds one at 1.1:1, even better. our 2:1 rate is intentionally bad - it's a floor, not a ceiling.

same for compute: if the network nodes are cheaper and better than our expensive backstop, great. that's the goal.

we succeed by becoming unnecessary.

---

## staking & slashing

### stake requirement

to provide 1 month of compute, stake **1 day of machine time**.

```
provide: 1 month
stake: 1 day (~3% collateral)
```

### stake = trust

the more you stake, the more the network trusts you:

```
low stake  → limited capacity, more scrutiny
high stake → more capacity, less friction
```

high-stake nodes get:
- higher job limits
- priority routing
- less aggressive abuse monitoring

the abuse system watches for bad actors. stake is your reputation.

### what happens on downtime

```
1. Node A goes offline
2. Down too long → workload migrates to Node B
3. Node A loses entire 1-day stake
4. Stake covers migration cost
```

### slashing table

| event | consequence |
|-------|-------------|
| downtime causing migration | lose 1 day stake |
| attestation fraud | lose all + permanent ban |

simple: stake 1 day, risk 1 day if you cause problems.

---

## offline operation

pre-signed commitments work without network:

```
┌─────────────────────────────────────────────┐
│           OFFLINE VERIFICATION              │
├─────────────────────────────────────────────┤
│                                             │
│  1. Check signature → valid?                │
│  2. Check expiry → still valid?             │
│  3. Check TDX quote → real hardware?        │
│                                             │
│  All verifiable without internet.           │
│                                             │
└─────────────────────────────────────────────┘
```

only redemption requires live network.

use cases:
- verify commitments on airplane
- trade peer-to-peer in remote areas
- cache attestations locally
- audit without network access

---

## attestation flow

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│   USER   │     │  CONTROL │     │   NODE   │
│          │     │  PLANE   │     │  (TDX)   │
└────┬─────┘     └────┬─────┘     └────┬─────┘
     │                │                │
     │ buy commitment │                │
     │ ──────────────>│                │
     │                │ verify node ──>│
     │                │<── TDX quote ──│
     │                │                │
     │<── commitment ─│                │
     │    (signed)    │                │
     │                │                │
     │ redeem ───────>│ route job ───>│
     │                │                │
     │<── compute ────│<── compute ───│
     │                │                │
```

the CPU signs what's running. not a committee. silicon.

---

## governance

### the goal: make ourselves obsolete

EasyEnclave is designed to be replaced:

1. **our exchange** - 2:1 rate is intentionally bad. build a better one.
2. **our compute** - priced high. network nodes should undercut us.
3. **our control plane** - open source. fork it if you want.

### initially

EasyEnclave controls:
- exchange availability
- protocol upgrades
- dispute resolution

### eventually

stake-weighted voting by nodes:
- governance proposals
- parameter changes
- treasury allocation

```
voting power = stake_amount × reputation_score
```

the endgame: a network that doesn't need us.

---

## roadmap

### now
- control plane
- basic staking
- attestation verification

### next
- **abuse system dashboard** - monitor bad actors, stake-weighted trust
- third-party exchange open source release
- multi-region node support

### later
- stake-weighted governance
- mobile verification SDK
- full decentralization

---

## comparison

### vs blockchain

| | blockchain | easyenclave |
|-|------------|-------------|
| trust | 1000 nodes agree | 1 node + TDX |
| speed | seconds/minutes | milliseconds |
| cost | gas fees | market rate |
| currency | volatile token | stable compute |
| complexity | high | low |

### vs cloud

| | cloud | easyenclave |
|-|-------|-------------|
| trust | "trust us" | TDX attestation |
| proof | none | cryptographic |
| pricing | complex | dynamic market |
| lock-in | high | portable |

### vs tokens

| | tokens | compute commitments |
|-|--------|---------------------|
| value | speculative | backed by real compute |
| volatility | high | market-stable |
| utility | often none | always redeemable |
| inflation | varies | tied to capacity |

---

## use cases

### confidential compute

```python
# buy compute commitment
commitment = buy_commitment("node-abc", months=1)

# verify offline
assert commitment.verify()  # no network needed

# redeem for compute
result = commitment.redeem(
    image="myapp:latest",
    env={"SECRET": "value"}
)
```

your code runs in TDX. attestation proves it.

### compute as payment

```python
# pay contractor in compute
contractor_wallet = "..."
commitment = create_commitment(months=2)
transfer(commitment, contractor_wallet)

# contractor can:
# - use it for compute
# - sell to others
# - exchange to USD (2:1)
```

### private AI

```python
commitment = buy_commitment("gpu-node", hours=10)

result = commitment.redeem(
    image="llama:70b",
    input={"prompt": "confidential..."},
    require_sealed=True
)
```

prompt never leaves the enclave. attestation proves it.

---

## summary

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   MACHINE-MONTHS = THE CURRENCY                             │
│                                                             │
│   ✓ Trade peer-to-peer: 0% overhead                         │
│   ✓ Provide compute: 0% overhead                            │
│   ✓ Cash out to USD: 2:1 (or better via third parties)      │
│                                                             │
│   ✓ Stake 1 day per 1 month commitment                      │
│   ✓ Lose stake if you cause migration                       │
│                                                             │
│   ✓ Dynamic pricing - market decides                        │
│   ✓ Third-party exchanges encouraged                        │
│   ✓ Offline verification via pre-signed commitments         │
│                                                             │
│   ✓ Goal: make EasyEnclave unnecessary                      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

blockchains asked: "how do we trust remote execution?"

and answered with: consensus, redundancy, tokens.

TDX answers with: silicon.

we build the economics on top. then we step aside.

---

*[easyenclave.com](/) - compute that trades like money*
