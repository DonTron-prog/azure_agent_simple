# Multi-agent orchestration demo — setup spec

A follow-on to `azure_rag_simple`. Same Azure footprint (Azure OpenAI + local
FAISS, nothing else), but the single chat agent is replaced by an
**orchestrator** that delegates to three specialised **subagents**:

1. **Data-retrieval subagent** — owns the FAISS store and the three retrieval
   tools from `azure_rag_simple` (`semantic_search`, `exhaustive_find`,
   `filter_by_metadata`). Takes a natural-language information request, returns
   a structured bundle of chunks + cited `doc_id`s.
2. **Analysis subagent** — takes the retrieved bundle plus the original
   question, produces a structured analysis (themes, contradictions, gaps,
   quantitative signals) grounded only in the retrieved snippets.
3. **Hypothesis-generation subagent** — takes the analysis, produces a ranked
   list of testable hypotheses, each tagged with the evidence that supports it
   and the evidence that would falsify it.

The **orchestrator** is itself an Azure OpenAI chat agent using function
calling, where each subagent is exposed as a single tool. The orchestrator
decides which subagents to call, in what order, and how many times — the
"agent-as-tool" pattern. A typical flow is
`retrieval → analysis → hypothesis → (optional) retrieval again to verify`.

---

## 1. Azure infrastructure

Identical to `azure_rag_simple`. Do not re-provision.

- **Reuse the existing Azure OpenAI resource** with `gpt-4o` and
  `text-embedding-3-large` deployments.
- **Reuse the same role assignment** (`Cognitive Services OpenAI User` on the
  caller identity).
- **No new Azure resources** — vector index is still local FAISS, documents
  still live on local disk, auth is still `DefaultAzureCredential`.

If the Azure side isn't set up yet, follow
`azure_rag_simple/MANUAL_SETUP.md` (portal path) or
`azure_rag_simple/AZURE_ML_SETUP.md` (no-local-CLI path) first. Stop after
those docs' step 5 (`.env` filled) — the rest of this file takes over.

---

## 2. Project layout

Create the new project as a **sibling directory** of `azure_rag_simple`:

```
~/Projects/
├── azure_rag_simple/          # existing — do not modify
└── azure_rag_multiagent/      # new project
    ├── data/
    │   ├── documents/         # PDFs (same shape as azure_rag_simple)
    │   ├── document_list.json
    │   └── index/             # FAISS index (built by scripts/ingest.py)
    ├── scripts/
    │   ├── ingest.py          # copied verbatim from azure_rag_simple
    │   └── chat.py            # new: drives the orchestrator
    ├── src/
    │   ├── config.py          # copied; may gain ORCHESTRATOR_DEPLOYMENT
    │   ├── faiss_store.py     # copied verbatim
    │   ├── ingest/            # copied verbatim
    │   │   ├── chunk.py
    │   │   ├── download.py
    │   │   ├── extract.py
    │   │   └── pipeline.py
    │   └── agents/
    │       ├── orchestrator.py     # new — top-level agent
    │       ├── retrieval_agent.py  # new — wraps FaissStore + 3 tools
    │       ├── analysis_agent.py   # new — structured analysis
    │       └── hypothesis_agent.py # new — hypothesis generation
    ├── .env.example
    ├── pyproject.toml
    └── README.md
```

**What to copy verbatim from `azure_rag_simple`:**
`src/config.py`, `src/faiss_store.py`, all of `src/ingest/`,
`scripts/ingest.py`, `pyproject.toml`, `.env.example`, and the
`data/document_list.*.json` samples. The ingest pipeline is unchanged — the
multi-agent system reads the same FAISS index.

**What to build new:** only the four files under `src/agents/` and a new
`scripts/chat.py` that instantiates the orchestrator instead of
`SimpleAgent`. The old `src/agent/` (singular) from `azure_rag_simple` is
**not** copied — its role is absorbed by `retrieval_agent.py`.

---

## 3. Component contracts

Keep each subagent's interface narrow and explicit so the orchestrator can
reason about when to call it.

### Retrieval subagent
- **Input:** a natural-language information need, plus optional `doc_type`
  and date constraints.
- **Internal:** uses the same three FAISS-backed tools as `azure_rag_simple`
  (`semantic_search`, `exhaustive_find`, `filter_by_metadata`). May call them
  multiple times per invocation.
- **Output:** JSON with `query`, `tools_used`, `chunks` (list of
  `{doc_id, title, section, page, content, score}`), and `doc_ids_seen`.

### Analysis subagent
- **Input:** the original user question + the retrieval bundle.
- **Internal:** single LLM call, prompt pinned to "analyse only what is in
  the provided chunks; cite `doc_id` for every claim; mark gaps explicitly."
- **Output:** JSON with `themes[]`, `contradictions[]`, `gaps[]`,
  `quantitative_signals[]`, each item carrying supporting `doc_id`s.

### Hypothesis-generation subagent
- **Input:** the analysis bundle (not the raw chunks — forces the agent to
  reason over the structured analysis, not re-retrieve).
- **Internal:** single LLM call, prompt pinned to "produce 3–5 testable
  hypotheses, ranked by strength of supporting evidence; for each, list both
  supporting and falsifying evidence with `doc_id` citations."
- **Output:** JSON with `hypotheses[]`, each
  `{statement, rank, supporting_evidence[], falsifying_evidence[], confidence}`.

### Orchestrator
- Exposes the three subagents as **three tools** to `gpt-4o`:
  `call_retrieval_agent`, `call_analysis_agent`, `call_hypothesis_agent`.
- System prompt names the canonical flow
  (retrieval → analysis → hypothesis) but allows loops (e.g. re-retrieve when
  the analysis flags a gap).
- Returns a final Markdown answer with a "Sources" section, identical
  citation style to `azure_rag_simple`.

---

## 4. Environment and dependencies

`.env` is the same as `azure_rag_simple` with one optional addition:

```bash
OPENAI_ENDPOINT=              # same as azure_rag_simple
CHAT_DEPLOYMENT=gpt-4o
EMBEDDING_DEPLOYMENT=text-embedding-3-large
EMBEDDING_DIM=3072
INDEX_DIR=data/index

# Optional: use a different deployment for the orchestrator only (e.g. a
# higher-quota gpt-4o deployment). Defaults to CHAT_DEPLOYMENT if unset.
ORCHESTRATOR_DEPLOYMENT=
```

`pyproject.toml` starts as a byte-for-byte copy of `azure_rag_simple`'s — no
new runtime dependencies are needed. Subagent orchestration is plain Python
plus the existing `openai` SDK function-calling. Do not introduce LangChain,
LlamaIndex, AutoGen, CrewAI, or Semantic Kernel; the point of the demo is
that the orchestration pattern is ~200 lines of straightforward Python.

---

## 5. Running

```bash
cd azure_rag_multiagent
python -m venv .venv && source .venv/bin/activate
pip install -e .

# Reuse Azure auth — no new login needed if DefaultAzureCredential already works
python scripts/ingest.py    # same ingest as azure_rag_simple
python scripts/chat.py      # orchestrator-driven multi-turn session
```

Chat-loop output should show, per turn: which subagents the orchestrator
called, the intermediate JSON bundles (truncated), and the final Markdown
answer with citations.

---

## 6. Evaluation

Port `evaluation.ipynb` from `azure_rag_simple` with three additions:

- **Per-subagent routing accuracy** — did the orchestrator call retrieval,
  analysis, hypothesis in the expected order for each test question?
- **Hypothesis quality** — gpt-4o-judged on a rubric
  (testability, grounding in cited evidence, non-triviality).
- **End-to-end answer quality** — same six metrics as the single-agent
  version (tool accuracy, retrieval/citation recall, groundedness, relevance,
  similarity).

Reuse `data/testset.jsonl` as the base; add a small extension set of
hypothesis-style questions ("what might explain X?", "given these docs, what
would you predict about Y?") so the hypothesis subagent actually gets
exercised.

---

## 7. Out of scope (explicitly)

- No Azure AI Search, Cognitive Search, or any managed vector service.
- No Azure Document Intelligence — keep `pdfplumber` + `pypdf` + `tiktoken`.
- No multi-agent framework libraries (LangChain, AutoGen, CrewAI, Semantic
  Kernel). Plain function-calling only.
- No persistent subagent memory between turns — each orchestrator turn is a
  fresh invocation; subagents are stateless.
- No streaming. Return the final answer in one shot to keep evaluation
  deterministic.
