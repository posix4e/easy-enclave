---
layout: default
title: whitepaper
---

# whitepaper

## EasyEnclave: Attestation-Based Compute Network

*A blockchain replacement using hardware trust instead of consensus.*

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   TRUST = TDX ATTESTATION, NOT CRYPTOGRAPHIC CONSENSUS      │
│   CURRENCY = DOLLAR POINTS, NOT TOKENS                      │
│   STAKE = MONTHS OF WORK, NOT GAS                           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## abstract

Blockchains solve trust with redundant computation and cryptographic consensus. This is wasteful. Intel TDX provides hardware-attested trust with zero redundancy.

EasyEnclave replaces blockchain economics with:

- **attestation over consensus** - TDX quotes prove execution, no need for 1000 nodes to agree
- **dollar points over tokens** - no speculation, no volatility, just compute credits
- **work-months over gas** - stake commitment measured in time, not abstract units
- **slashing over proof-of-work** - misbehave and lose your stake

---

## the problem with blockchains

| blockchain | easyenclave |
|------------|-------------|
| 1000 nodes run same code | 1 node runs, hardware attests |
| consensus = slow + expensive | attestation = instant + cheap |
| tokens = speculation | dollar points = stable value |
| gas = unpredictable pricing | machine-hours = predictable |
| trust the math | trust the silicon |

blockchains are an expensive solution to "how do we trust remote code execution?"

TDX answers that question directly: the CPU signs what's running.

---

## architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     CONTROL PLANE                           │
│                                                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │   LEDGER    │  │  REGISTRY   │  │   ROUTER    │         │
│  │             │  │             │  │             │         │
│  │ balances    │  │ node stakes │  │ job routing │         │
│  │ transactions│  │ attestations│  │ load balance│         │
│  │ slashing    │  │ reputation  │  │ failover    │         │
│  └─────────────┘  └─────────────┘  └─────────────┘         │
│                                                             │
└───────────────────────────┬─────────────────────────────────┘
                            │
            ┌───────────────┼───────────────┐
            │               │               │
            ▼               ▼               ▼
     ┌──────────┐    ┌──────────┐    ┌──────────┐
     │  NODE A  │    │  NODE B  │    │  NODE C  │
     │  TDX VM  │    │  TDX VM  │    │  TDX VM  │
     │          │    │          │    │          │
     │ staked:  │    │ staked:  │    │ staked:  │
     │ 6 months │    │ 12 months│    │ 3 months │
     └──────────┘    └──────────┘    └──────────┘
```

---

## economics

### dollar points

no tokens. no speculation. just credits.

```
1 dollar point = $1 USD worth of compute
```

users deposit USD, get dollar points. spend dollar points on compute. simple.

### pricing

machine-hours, not gas.

```
1 vCPU-hour    = $0.05
1 GB-hour      = $0.01
1 GPU-hour     = $0.50
1 TB egress    = $0.10
```

prices set by network governance. adjusted quarterly. predictable.

### revenue split

```
┌────────────────────────────────────────┐
│           USER PAYS $1.00              │
├────────────────────────────────────────┤
│  85% → node operator                   │
│  10% → network treasury                │
│   5% → protocol development            │
└────────────────────────────────────────┘
```

---

## staking

### work-months

nodes stake commitment, not tokens.

```
stake = mass of commitment to serve workloads
unit = work-months (1 node × 1 month of availability)
```

example:
- node A stakes 6 work-months
- node A must serve workloads for 6 months
- if node A disappears, stake is slashed

### stake requirements

| tier | stake | max jobs | priority |
|------|-------|----------|----------|
| bronze | 3 months | 10 | low |
| silver | 6 months | 50 | medium |
| gold | 12 months | unlimited | high |
| platinum | 24 months | unlimited | highest |

higher stake = more jobs routed to you = more revenue.

### collateral

work-months are backed by USD collateral:

```
1 work-month = $500 USD collateral
```

stake 6 months = lock $3,000. this covers potential damages if you fail.

---

## slashing

misbehave and lose your stake.

### slashing conditions

| violation | slash % | evidence |
|-----------|---------|----------|
| downtime > 1 hour | 5% | health check failure |
| downtime > 24 hours | 25% | health check failure |
| abandon job | 50% | no attestation renewal |
| data breach | 100% | audit finding |
| attestation fraud | 100% + ban | quote mismatch |

### slashing process

```
1. violation detected (automated or reported)
2. evidence submitted to control plane
3. 24-hour dispute window
4. if confirmed: slash executed
5. slashed funds → affected users + treasury
```

### slash distribution

```
┌────────────────────────────────────────┐
│         SLASHED: $1,000                │
├────────────────────────────────────────┤
│  70% → affected users (compensation)   │
│  20% → reporter (bounty)               │
│  10% → treasury                        │
└────────────────────────────────────────┘
```

---

## attestation flow

no consensus. just hardware.

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│  CLIENT  │     │  CONTROL │     │   NODE   │
│          │     │  PLANE   │     │  (TDX)   │
└────┬─────┘     └────┬─────┘     └────┬─────┘
     │                │                │
     │ submit job ───>│                │
     │                │ route job ────>│
     │                │                │
     │                │<── attestation │
     │                │    (TDX quote) │
     │                │                │
     │                │ verify quote   │
     │                │ check RTMR     │
     │                │ log to ledger  │
     │                │                │
     │<── job handle ─│                │
     │                │                │
     │ poll status ──>│<── heartbeat ──│
     │                │    (re-attest) │
     │                │                │
     │<── result ─────│<── result ─────│
     │                │                │
     │                │ charge user    │
     │                │ pay node       │
     │                │                │
```

### verification

every job execution is attested:

```json
{
  "job_id": "abc123",
  "node_id": "node-xyz",
  "quote": "<base64 TDX quote>",
  "rtmrs": {
    "rtmr0": "firmware...",
    "rtmr1": "kernel...",
    "rtmr2": "workload...",
    "rtmr3": "runtime..."
  },
  "sealed": true,
  "timestamp": "2024-01-15T10:30:00Z"
}
```

the CPU signed this. not a committee. not a chain. silicon.

---

## ledger

### no blockchain needed

the control plane maintains a simple database:

```sql
-- accounts
CREATE TABLE accounts (
  id UUID PRIMARY KEY,
  balance_cents BIGINT,  -- dollar points in cents
  created_at TIMESTAMP
);

-- transactions
CREATE TABLE transactions (
  id UUID PRIMARY KEY,
  from_account UUID,
  to_account UUID,
  amount_cents BIGINT,
  type TEXT,  -- deposit, withdraw, job_payment, slash
  job_id UUID,
  created_at TIMESTAMP
);

-- stakes
CREATE TABLE stakes (
  node_id UUID PRIMARY KEY,
  collateral_cents BIGINT,
  work_months INT,
  start_date DATE,
  end_date DATE,
  status TEXT  -- active, slashed, released
);

-- jobs
CREATE TABLE jobs (
  id UUID PRIMARY KEY,
  user_id UUID,
  node_id UUID,
  attestation JSONB,
  started_at TIMESTAMP,
  ended_at TIMESTAMP,
  cost_cents BIGINT
);
```

### why not blockchain?

| blockchain ledger | easyenclave ledger |
|-------------------|-------------------|
| immutable | auditable |
| trustless | attested |
| slow (consensus) | fast (postgres) |
| expensive (gas) | cheap (SQL) |
| pseudonymous | KYC optional |

we don't need trustlessness. we have attestation.

### audit trail

all transactions logged with attestation references:

```
2024-01-15 10:30:00 | DEPOSIT    | user_abc | +$100.00
2024-01-15 10:31:00 | JOB_START  | job_123  | user_abc → escrow
2024-01-15 11:45:00 | JOB_END    | job_123  | escrow → node_xyz ($4.25)
2024-01-15 11:45:00 | JOB_END    | job_123  | escrow → treasury ($0.50)
2024-01-15 11:45:00 | JOB_END    | job_123  | escrow → user_abc ($0.25 refund)
```

auditors can verify any transaction against the attestation record.

---

## governance

### network treasury

funds from:
- 10% of all job payments
- slashing penalties
- node registration fees

spent on:
- protocol development
- security audits
- marketing
- dispute resolution

### decision making

initially: founding org makes decisions
later: stake-weighted voting by nodes

```
voting power = active_stake_months × reputation_score
```

### reputation

nodes earn reputation over time:

```
reputation = (uptime% × 0.4) + (jobs_completed × 0.3) + (stake_age × 0.3)
```

high reputation = more job routing = more revenue.

---

## comparison

### vs ethereum

| | ethereum | easyenclave |
|-|----------|-------------|
| trust | consensus | attestation |
| cost | $0.50-50 per tx | $0.001 per tx |
| speed | 12 sec blocks | milliseconds |
| currency | ETH (volatile) | USD points |
| staking | 32 ETH (~$80k) | $500/month |

### vs AWS

| | AWS | easyenclave |
|-|-----|-------------|
| trust | "trust us" | TDX attestation |
| transparency | none | full audit trail |
| vendor lock | high | portable containers |
| pricing | complex | simple $/hour |

### vs other L2s/rollups

| | rollups | easyenclave |
|-|---------|-------------|
| execution | off-chain, verify on-chain | off-chain, verify via TDX |
| finality | depends on L1 | instant |
| data availability | L1 or committee | attested storage |
| complexity | high | low |

---

## use cases

### confidential compute

run sensitive workloads with proof of execution:

```python
from easyenclave import submit_job

result = submit_job(
    image="myapp:latest",
    env={"API_KEY": "secret"},
    max_cost_usd=10.00
)

print(f"executed on: {result.node_id}")
print(f"attestation: {result.quote[:50]}...")
print(f"cost: ${result.cost_usd}")
```

### private AI inference

```python
result = submit_job(
    image="llama:70b",
    input={"prompt": "confidential query..."},
    require_sealed=True
)
```

your prompt never leaves the enclave. not even the node operator sees it.

### financial settlement

```python
# atomic swap without blockchain
result = submit_job(
    image="settlement:v1",
    input={
        "from": "bank_a",
        "to": "bank_b",
        "amount": 1000000
    }
)

# attestation proves settlement executed correctly
verify_settlement(result.attestation)
```

---

## roadmap

### phase 1: foundation
- control plane launch
- basic staking (single tier)
- USD deposits via Stripe
- 10 initial nodes

### phase 2: economics
- multi-tier staking
- slashing automation
- reputation system
- 100+ nodes

### phase 3: governance
- treasury management
- stake-weighted voting
- third-party audits
- 1000+ nodes

### phase 4: ecosystem
- SDK for other languages
- mobile verification
- enterprise features
- global node network

---

## conclusion

blockchains answered: "how do we trust remote execution?"

with: redundancy, consensus, tokens, complexity.

TDX answers the same question with: hardware attestation.

EasyEnclave builds an economic layer on top:

- **stake work-months** to participate
- **earn dollar points** for serving workloads
- **lose stake** if you misbehave
- **no tokens** - just compute credits

simple. auditable. attested.

```
┌─────────────────────────────────────────────────┐
│                                                 │
│   THE BLOCKCHAIN WAS JUST A VERY EXPENSIVE      │
│   WAY TO SAY "I PROMISE I RAN YOUR CODE"        │
│                                                 │
│   TDX SAYS IT WITH SILICON.                     │
│                                                 │
└─────────────────────────────────────────────────┘
```

---

## appendix: technical details

### TDX quote structure

```
offset | size | field
-------|------|------
0      | 4    | version
4      | 2    | attestation key type
6      | 2    | TEE type (TDX = 0x81)
8      | 16   | QE vendor ID
24     | 16   | user data
40     | 48   | RTMR[0-3]
...    | ...  | signature
```

### RTMR measurements

| register | measures |
|----------|----------|
| RTMR0 | TDX module + firmware |
| RTMR1 | OS loader + kernel |
| RTMR2 | application code |
| RTMR3 | runtime configuration |

### API endpoints

```
POST /v1/jobs              # submit job
GET  /v1/jobs/{id}         # job status
GET  /v1/jobs/{id}/attest  # attestation proof

POST /v1/accounts/deposit  # add dollar points
GET  /v1/accounts/balance  # check balance
GET  /v1/accounts/history  # transaction history

POST /v1/nodes/register    # register as node
POST /v1/nodes/stake       # add stake
GET  /v1/nodes/status      # node status
```

---

*built on [easyenclave](/) - hardware trust for the rest of us*
