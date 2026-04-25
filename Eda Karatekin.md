# Trend Analytics Agent

Proof of Concept

March 2026  | Eda Karatekin

## 1.    Overview

### 1.1.        Problem Statement

Business data exists in SQL databases behind Power BI dashboards, covering sales, performance, margins, and other metrics across multiple dimensions (product, dealer, region, segment). Understanding what’s changing in this data — and why — requires an analyst to manually query, slice, compare periods, and write up findings. This process is slow, only covers what someone already thought to check, and runs on a fixed reporting cycle.

We want to test whether an AI agent with access to SQL tools can perform a bounded agentic investigation of business data, choosing its next analytical step within defined limits based on what it finds, and produce useful trend and performance insights that an analyst would otherwise spend hours assembling.

### 1.2.        Data

**Status: Ready.** A read-only SQL view exists in the database that serves as the source for existing Power BI dashboards. A view about sales data that will be used in this project.

The SQL view contains:

•       Date field (for time-based analysis)

•       1-2 metric values to start (e.g., sales, revenue), expandable to 3-4 if the investigation loop is working well

•       2-3 dimension fields to start (e.g., product, dealer, region), expandable to 4-5 as the POC progresses

•       12+ months of history

The agent accesses data through bounded analysis tools, each backed by validated, tested SQL underneath. The agent decides which tool to call and with what parameters; the tools handle the query execution. Tools are subject to query logging and execution limits (query timeout, row limits, maximum tool calls per run). The analysis tools include:

•       Metric overview: Returns current values and period-over-period changes for selected metrics

•       Period comparison: Compares a metric across two time periods with optional filters

•       Dimension decomposition: Breaks a metric down by a dimension (product, dealer, region) and ranks contributors

•       Time series : Returns a metric over time at a specified grain (daily, weekly, monthly)

•       Top contributors: Identifies which dimensional values contributed most to a change

•       Data sufficiency check: alidates whether enough data exists for a reliable comparison before proceeding

This design keeps the investigation agentic (the agent decides which tool to call next based on what it just found) while ensuring all calculations are deterministic and trustworthy. The analyst only needs to evaluate whether the agent asked the right questions, not whether its SQL was correct.

Selected metric definitions will be confirmed before kick-off.

### 1.3.        LLM Model

**Azure Foundry LLM models will be tested.** Enterprise data residency (data stays within the Azure tenant), integration with existing Azure infrastructure will be used. The agent uses reasoning models for tool selection, and narrative output. The agent determines which analytical step to perform next, while core calculations such as period comparison, ranking, and contribution analysis are executed through the bounded analysis tools using validated SQL and deterministic logic.

### 1.4.        AI Task Type

|   |   |
|---|---|
|**Task Type**|**How It Applies**|
|**Workflow (primary)**|Multi-step analytical process: the agent reasons about the data, decides which analysis tool to call next, examines results, decides what to investigate further, and repeats until it has a complete analysis. The steps are not predetermined — the agent’s path depends on what it finds, within defined scope and stopping rules.|
|**Classification**|The agent classifies detected changes: rising, declining, accelerating, decelerating, stable. These labels structure the briefing output. More nuanced classifications (seasonal, anomaly) may be added if the data and history support them.|
|**Generation**|The agent produces a readable narrative briefing from its investigation findings, with source attribution on every claim.|

This is primarily a workflow with an agentic reasoning loop. The agent decides what to do at each step based on what it just found. Q&A, classification, and generation are embedded within the workflow, not separate tasks.

### 1.5.        Success Criteria

All outputs are evaluated by an analyst who validates the agent’s findings against source data. Criteria 1–3 are tested internally before any briefing is distributed.

|   |   |   |   |
|---|---|---|---|
|**#**|**Criterion**|**Target**|**Validation**|
|**1**|Correct trend direction. The agent correctly identifies whether metrics are rising, declining, accelerating, decelerating, or stable.|≥ 80%|Analyst runs test cases against known data.|
|**2**|Correct top driver. When the agent attributes a change to a dimension (product, region, dealer), the attribution is correct. “Correct” means the agent’s top attributed driver matches the analyst’s review or appears in the analyst’s top 3 contributors.|≥ 70%|Analyst compares to manual decomposition.|
|**3**|No fabricated claims. Every number and claim in the output traces back to a real query result. Zero hallucinated figures.|0|Analyst reviews all figures in each briefing against source data and query logs.|
|**4**|Briefing is useful. The analyst reviewing the briefing considers the output worth distributing and finds at least some findings that were not already visible in existing reports.|Yes|Analyst feedback after each briefing.|

## 2.    What Makes This Agentic

The agent has a set of bounded analysis tools (metric overview, period comparison, dimension decomposition, time series, top contributors, data sufficiency check) and business context provided through structured configuration. Each tool runs validated SQL underneath; the agent never writes raw queries.

What makes this agentic — rather than a fixed pipeline — is that the agent decides which tool to call next based on what the previous tool returned, within bounded rules:

1.     The agent calls the metric overview tool to see what’s changed across the key metrics.

2.     It examines the results and reasons: “Revenue is down 8% overall. Let me decompose by region.”

3.     It calls the dimension decomposition tool on revenue by region. “The decline is concentrated in Region A. What’s different — is it a specific product?”

4.     It calls decomposition again, this time by product within Region A. “Product X accounts for 70% of the decline. Is this a trend or a one-time drop?”

5.     It calls the time series tool for Product X in Region A. “This has been declining for 4 consecutive months. Is this happening in other regions too?”

6.     It calls the period comparison tool for Product X across all regions. “No — only Region A. This is a localised issue.”

7.     It synthesises everything into a finding with evidence and delivers the briefing.

This investigation chain can’t be scripted in advance because the agent doesn’t know what it will find. A different week’s data leads to a completely different chain of tool calls. The agent decides how to proceed within the available tools, query limits, and stopping criteria. That’s the agentic behaviour.

## 3.    What It Looks Like at End of POC

### 3.1.        Weekly Briefing (Primary)

Every week, the orchestrator triggers the agent. The agent runs its investigation loop (typically 5–15 tool calls depending on what it finds) and produces a trend and performance briefing. The briefing is reviewed by an analyst before distribution. It contains:

•       What changed: the significant movements detected, with direction and magnitude

•       Why it changed: the dimensional drivers identified (which product, dealer, region, or segment is responsible)

•       Context: whether the change is a new development, a continuing trend, an acceleration, or a reversal

•       Evidence: the specific numbers from the source data, with references to the analysis tools used and comparison periods

Significance is defined using simple business thresholds agreed at kickoff (e.g., minimum percentage change and/or minimum absolute impact), capped to the top findings per briefing. Each briefing is different because the agent’s investigation path adapts to the data.

### 3.2.        Follow-Up Q&A (Not in this POC – but will develop if I can find time)

After reading the briefing, the analyst can ask follow-up questions in a conversational interface. The agent runs a new investigation grounded in the same data:

•       “Drill into Region A” — the agent runs decomposition and time series tools focused on Region A

•       “Compare this quarter to the same period last year” — the agent uses the period comparison tool with historical parameters

•       “Which dealers are driving the decline?” — the agent calls the top contributors tool decomposed by dealer

## 4.    POC Scope

•       1 read-only SQL view

•       1–2 metrics, 2–3 dimensions to start (expandable if the loop is working well)

•       Agent with bounded analysis tools (metric overview, period comparison, dimension decomposition, time series, top contributors, data sufficiency check) — each backed by validated SQL, logged, with execution limits

•       Business context provided through structured configuration and prompt (metric definitions, dimensions, known relationships, significance thresholds)

•       Agentic investigation loop — agent decides its own analytical path within bounded limits

•       Weekly trend and performance briefing, analyst-reviewed before distribution

## 5.    Architecture

## 6.    Key Risks

|                                                                     |                                                                                                                                                                          |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Risk**                                                            | **Mitigation**                                                                                                                                                           |
| **Analysis tools return unexpected results**                        | Validating all tool implementations against known Unit test each tool independently before connecting to the agent. Log all tool calls and results.                      |
| **Agent investigation goes off track**                              | Providing clear business context in the configuration. Limiting investigation depth (e.g., max 15 tool calls per run). Review early outputs for relevance.               |
| **LLM fabricates numbers not from tool results**                    | Logging all tool results alongside the briefing. Each finding includes supporting tool call references. Analyst reviews every briefing.                                  |
| **Briefings have nothing significant to report**                    | To broaden the metrics and dimensions. If the data genuinely doesn’t move, that’s a valid finding — the approach may suit a different data domain.                       |
| **Metric definitions in SQL don’t match Power BI / business logic** | Confirming metric logic for selected POC measures before development begins. Validate early agent outputs against known reports.                                         |
| **Insufficient history or sparse slices lead to weak comparisons**  | Adding simple data sufficiency checks to the agent’s process. If data is too sparse for a comparison, the agent states that explicitly rather than forcing a conclusion. |