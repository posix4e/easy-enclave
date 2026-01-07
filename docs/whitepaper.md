---
layout: default
title: whitepaper
---

# whitepaper

## easyenclave: machine-months as currency

attested compute, accounted by usage.

---

## abstract

clouds say "trust us." blockchains say "replicate it."
easyenclave uses intel tdx to prove what runs, and a control plane ledger to account for verified usage.
machine-months are the unit. credits are issued only after compute is delivered.
credits move by transfer and are spent to schedule compute.
no tokens. no gas. no speculation.

---

## the problem

- waste: 1000 nodes running the same job to agree on state
- speed: consensus adds seconds or minutes
- privacy: data is public
- cloud trust: no proof that code ran as promised
- tokens: price disconnected from utility

---

## construction

```
user/sdk -> control plane (tdx, ledger, routing) -> ws tunnel -> agent (tdx) -> backend
```

the control plane is not special infrastructure. it is just another tdx agent with a special role.
it is verifiable the same way as any workload.

roles:
- user or sdk: discovers apps and routes traffic
- node: a tdx host that provides capacity and stake
- agent: enclave software that serves an app and connects outbound
- control plane: attested agent that verifies nodes, tracks usage and credits, and routes traffic

control plane responsibilities:
- verify node attestation and health
- track capacity, stake, usage, credits, transfers
- route traffic to private agents
- maintain the authoritative ledger

agent responsibilities:
- run workloads in tdx
- register capacity and pricing
- provide health signals
- accept proxied requests over an outbound websocket tunnel

---

## units and credits

- unit of value: machine-month
- definition: 1 vcpu for 30 days (or equivalent compute for other skus)
- pricing: node-defined, market decides
- issuance: credits minted to providers from verified usage (no pre-issuance)
- spend: credits are used to schedule compute
- transfer: credits are transferable via control plane api

credits are a ledger balance, not a token. they represent verified compute delivered by the network.

---

## trust model

hardware proves correctness and confidentiality. stake proves availability.
stake is required to be eligible to earn credits.

rule of thumb:
- provide 1 month of capacity -> stake 1 day of machine time (about 3 percent)

slashing events:
- downtime causing migration -> lose 1 day stake
- attestation fraud -> lose all stake and permanent ban

---

## node lifecycle

1) node registers capacity and pricing
2) control plane attests the node (tdx) and starts health checks
3) node posts stake
4) workloads run
5) usage is reported or metered for a period (ex: monthly)
6) if eligible, control plane issues credits to the node
7) credits can be transferred or spent on compute

eligibility to earn credits requires active stake, valid attestation, and passing health checks.

example: capacity registration (conceptual)

```json
{
  "node_id": "node-abc",
  "capacity_months": 1,
  "price_usd": 50.0
}
```

example: usage report (conceptual)

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

## routing and privacy

agents connect outbound and stay private. no public exposure required.
the control plane proxies requests to the active agent over the websocket tunnel.
the sdk resolves apps and routes through the proxy.

---

## offline verification

tdx quotes and measurements can be verified offline. no network is required to validate
that a node is real. transfers, spending, and credit issuance require the control plane
ledger to be online.

---

## credit flow

providers earn credits from verified usage. clients acquire credits from providers or transfers,
then spend credits to schedule compute.
transfers move credits between accounts.

example: transfer

```
alice has: 2 machine-months
bob wants: compute

alice -> control plane api -> bob
```

---

## governance

easyenclave is designed to be replaced. the control plane is open source and forkable.
eventually, stake-weighted voting can govern parameters and upgrades.

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
- usage-based credits, transfers api, spend flow
- agent proxies (private agents behind control plane)
- abuse system dashboard (stake-weighted trust)
- third-party exchange open source release
- multi-region node support

### later
- stake-weighted governance
- mobile verification sdk
- full decentralization

---

## comparison

### vs blockchain

| metric | blockchain | easyenclave |
|---|---|---|
| trust | many nodes agree | 1 node plus tdx |
| speed | seconds or minutes | milliseconds |
| cost | gas fees | market rate |
| currency | volatile token | stable compute |
| complexity | high | low |

### vs cloud

| metric | cloud | easyenclave |
|---|---|---|
| trust | trust the provider | tdx attestation |
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

### private apis

```python
# reach an agent behind the control plane proxy
client = connect("app-name")
response = client.get("/api/private")
```

---

## summary

- machine-months are the currency
- credits are minted from verified usage
- tdx attestation proves execution
- stake provides availability guarantees
- control plane maintains the ledger and routes traffic
---

compute that trades like money
