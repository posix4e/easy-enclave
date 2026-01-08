---
layout: default
title: whitepaper
---

# easyenclave

## a network for attested compute

### abstract

easyenclave is a compute network. nodes provide capacity, agents serve workloads, and a control plane
keeps the ledger and routes traffic. users prepay usd credits to run compute, credits are locked while
work runs, and settlement pays providers only after strict verification. the control plane is itself a
tdx agent, so the network can verify its own coordinator.

---

## 1. introduction

we need a network that can prove execution without replication.

- cloud requires trust with no proof of execution
- blockchains require replication, wasting compute
- tokens decouple price from real work

we want a network where compute is verified by hardware and paid for by delivered work.

---

## 2. network roles

node
: a tdx host that provides capacity and stake.

agent
: enclave software that runs workloads and connects outbound.

control plane
: an attested agent that verifies nodes, routes traffic, and maintains the ledger.

user/sdk
: discovers apps, moves credits, and routes traffic.

---

## 3. network flow

```
user/sdk -> control plane (tdx, ledger, routing) -> ws tunnel -> agent (tdx) -> backend
```

nodes register capacity and stake.
agents serve workloads and stay private behind outbound tunnels.
the control plane attests nodes, meters health, and settles credits.

control plane responsibilities
- verify node attestation and health
- track capacity, stake, usage, credits, transfers
- route traffic to private agents
- maintain the authoritative ledger

---

## 4. credits and settlement

usd credit
: ledger balance denominated in dollars. 1 credit = $1.

vcpu-hour
: one vcpu for one hour, used for metering and pricing.

credits are minted to users on prepay. spending locks credits to a period. settlement happens
at the end of the period and pays providers only if all checks pass.

period settlement is zero tolerance:
- any missed health check fails the period
- any missed attestation fails the period
- any abuse report fails the period

health and attestation checks come from the control plane or a trusted attested uptime server.
abuse reports can only be filed by the launcher.
misses are low cost: the period fails and payout is withheld, nothing more.
if the control plane goes down, checks can misfire and settlement halts. this is accepted.

settlement logic
- pass: locked credits transfer to the provider
- fail: locked credits return to the user

---

## 5. attestation and offline verification

intel tdx provides a quote and measurements that can be verified offline. no network access
is required to validate that a node is real. transfers, spending, and settlement require the
control plane ledger to be online.

---

## 6. pricing and routing

nodes publish a usd price per vcpu-hour.
the control plane routes traffic to the lowest effective price among eligible nodes,
weighted by trust (attestation, health, abuse history).
prices are posted; there is no algorithmic price curve.

---

## 7. stake and incentives

hardware proves correctness and confidentiality. stake proves availability.
stake is required to be eligible for settlement.

rule of thumb
- provide 1 month of capacity -> stake 1 day of machine time (about 3 percent)

slashing events
- downtime causing migration -> lose 1 day stake
- attestation fraud -> lose all stake and permanent ban

---

## 8. routing and privacy

agents connect outbound and stay private. no public exposure required.
requests are proxied over the websocket tunnel. the sdk resolves apps and routes through the proxy.

---

## 9. governance

the control plane is open source and forkable. eventually, stake-weighted voting can govern
parameters and upgrades.

```
voting power = stake_amount * reputation_score
```

---

## 10. roadmap

### now
- control plane
- basic staking
- attestation verification

### next
- prepaid credits and settlement flow
- transfers api and spend flow
- agent proxies (private agents behind control plane)
- abuse system dashboard (stake-weighted trust)
- third-party exchange open source release
- multi-region node support

### later
- stake-weighted governance
- mobile verification sdk
- full decentralization

---

## 11. comparison

### vs blockchain

| metric | blockchain | easyenclave |
|---|---|---|
| trust | many nodes agree | 1 node plus tdx |
| speed | seconds or minutes | milliseconds |
| cost | gas fees | market rate |
| currency | volatile token | usd credits |
| complexity | high | low |

### vs cloud

| metric | cloud | easyenclave |
|---|---|---|
| trust | trust the provider | tdx attestation |
| proof | none | cryptographic |
| pricing | complex | market set |
| lock-in | high | portable |

---

## 12. use cases

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
transfer_credits(to="contractor-id", amount="$200")
```

### private apis

```python
# reach an agent behind the control plane proxy
client = connect("app-name")
response = client.get("/api/private")
```

---

## summary

- the network is built from nodes, agents, and an attested control plane
- users prepay usd credits, providers are paid after settlement
- tdx attestation proves execution
- stake provides availability guarantees
- the control plane maintains the ledger and routes traffic

---

compute that trades like money
