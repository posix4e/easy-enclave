---
layout: default
title: whitepaper
---

# whitepaper

## easyenclave: machine-months as currency

Hardware-attested compute that trades like money.

---

## abstract

Blockchains replicate the same work across many nodes. Cloud providers ask you to trust them.
EasyEnclave uses Intel TDX to prove what runs, and a control plane ledger to account for usage.
The unit of value is a machine-month. Credits are minted from verified utilization, are transferable,
and are redeemable for compute. No tokens, no gas, no speculation.

---

## the problem

- waste: 1000 nodes running the same job to agree on state
- speed: consensus adds seconds or minutes
- privacy: data is public
- cloud trust: no proof that code ran as promised
- tokens: price disconnected from utility

---

## the solution in one sentence

Use TDX to prove execution, and a control plane ledger to turn real compute usage into transferable credits.

---

## system overview

```
User/SDK -> Control Plane (attested) -> WS Tunnel -> Agent (TDX) -> Backend
```

The control plane is not special infrastructure. It is just another TDX agent with a special role.
It is verifiable the same way as any workload.

Control plane responsibilities:
- verify node attestation and health
- track capacity, stake, usage, credits, transfers
- route traffic to private agents
- maintain the authoritative ledger

Agent responsibilities:
- run workloads in TDX
- register capacity and pricing
- provide health signals
- accept proxied requests over an outbound WebSocket tunnel

---

## units and credits

- unit of value: machine-month
- definition: 1 vCPU for 30 days (or equivalent compute for other SKUs)
- pricing: node-defined, market decides
- issuance: credits minted only from verified usage
- transfer: credits are transferable via control plane API

Credits are a ledger balance, not a token. They represent real compute that already ran.

---

## node lifecycle

1) node registers capacity and pricing
2) control plane attests the node (TDX) and starts health checks
3) node posts stake
4) workloads run
5) usage is reported for a period (ex: monthly)
6) if eligible, control plane issues credits to the node
7) credits can be transferred or redeemed

Example: capacity registration (conceptual)

```json
{
  "node_id": "node-abc",
  "capacity_months": 1,
  "price_usd": 50.0
}
```

Example: usage report (conceptual)

```json
{
  "usage_id": "usage-123",
  "node_id": "node-abc",
  "period_start": "2024-02-01T00:00:00Z",
  "period_end": "2024-03-01T00:00:00Z",
  "units": 1.0
}
```

---

## staking and trust

Staking is the availability guarantee. Hardware proves correctness, stake ensures uptime.

Rule of thumb:
- provide 1 month of capacity -> stake 1 day of machine time (about 3 percent)

Trust behavior:
- low stake: limited capacity, more scrutiny
- high stake: more capacity, less friction

Slashing events:
- downtime causing migration -> lose 1 day stake
- attestation fraud -> lose all stake and permanent ban

---

## routing and privacy

Agents connect outbound and stay private. No public exposure required.
The control plane proxies requests to the active agent over the WebSocket tunnel.
The SDK resolves apps and routes through the proxy.

---

## offline verification

TDX quotes and measurements can be verified offline. No network is required to validate
that a node is real. Transfers, redemption, and credit issuance require the control plane
ledger to be online.

---

## economics

### transfers

Credits move through a simple ledger update:

```
Alice has: 2 machine-months
Bob wants: compute

Alice -> control plane API -> Bob
```

### redeem for compute

Credits are redeemable for real compute at full value:

```
1 machine-month -> 1 month of compute
```

---

## governance

EasyEnclave is designed to be replaced. The control plane is open source and forkable.
Eventually, stake-weighted voting can govern parameters and upgrades.

```
voting power = stake_amount * reputation_score
```

---

## roadmap

### now
- control plane
- basic staking
- attestation verification

### next
- usage-based credits, transfers API, redemption flow
- agent proxies (private agents behind control plane)
- abuse system dashboard (stake-weighted trust)
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
|---|---|---|
| trust | many nodes agree | 1 node plus TDX |
| speed | seconds or minutes | milliseconds |
| cost | gas fees | market rate |
| currency | volatile token | stable compute |
| complexity | high | low |

### vs cloud

| | cloud | easyenclave |
|---|---|---|
| trust | trust the provider | TDX attestation |
| proof | none | cryptographic |
| pricing | complex | market set |
| lock-in | high | portable |

---

## use cases

### confidential compute

```python
# run a private workload
result = run_private_job(
    image="myapp:latest",
    env={"SECRET": "value"}
)
```

### compute as payment

```python
# pay a contractor in compute credits
transfer_credits(to="contractor-id", amount="2 machine-months")
```

### private APIs

```python
# reach an agent behind the control plane proxy
client = connect("app-name")
response = client.get("/api/private")
```

---

## summary

- machine-months are the currency
- credits are minted from verified usage
- TDX attestation proves execution
- stake provides availability guarantees
- control plane maintains the ledger and routes traffic
- goal: make EasyEnclave unnecessary

---

compute that trades like money
