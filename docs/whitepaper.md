---
layout: default
title: whitepaper
---

# easyenclave

## compute credits backed by attested usage

### abstract

this paper proposes a system where compute is proven by hardware attestation and paid for with credits.
credits are issued to users on prepay, locked while compute runs, and settled to providers only after
strict verification. the unit of account is the machine-month. the control plane is itself a tdx agent
and keeps the authoritative ledger for usage, stake, and transfers.

---

## 1. introduction

existing systems fail in different ways.

- cloud requires trust with no proof of execution
- blockchains require replication, wasting compute
- tokens decouple price from real work

we want a currency that is always tied to delivered compute, and a network that proves its own work.

---

## 2. model and terms

machine-month
: one vcpu for 30 days, or an equivalent unit for other skus.

credits
: ledger balance used to schedule compute. credits are transferable.

node
: a tdx host that provides capacity and stake.

agent
: enclave software that runs workloads and connects outbound.

control plane
: an attested agent that verifies nodes, routes traffic, and maintains the ledger.

---

## 3. system overview

```
user/sdk -> control plane (tdx, ledger, routing) -> ws tunnel -> agent (tdx) -> backend
```

roles
- users and sdks discover apps, move credits, and route traffic.
- nodes register capacity and stake.
- agents serve workloads and stay private behind outbound tunnels.
- the control plane attests nodes, meters health, and settles credits.

control plane responsibilities
- verify node attestation and health
- track capacity, stake, usage, credits, transfers
- route traffic to private agents
- maintain the authoritative ledger

---

## 4. credits, spending, settlement

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

## 6. pricing

nodes publish a floor price. the control plane computes a suggested price from utilization
and reliability, and routes traffic by effective price plus trust.

```
suggested_price =
  floor_price * (utilization / target_utilization) ^ alpha * reliability_factor
```

utilization is observed demand vs capacity. target_utilization is a policy constant.
reliability_factor reflects attestation, health, and abuse history.

stake-weighted gauges can shift target_utilization by region or node class.
price follows the gauge, but the floor price is always honored.

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

- machine-months are the unit of account
- users prepay credits, providers are paid after settlement
- tdx attestation proves execution
- stake provides availability guarantees
- the control plane maintains the ledger and routes traffic

---

compute that trades like money
