# azure_agent_simple

Trend-analytics PoC from the `Eda Karatekin.md` spec. Two Azure OpenAI agents
over a small public sales dataset:

- **Investigator** — runs a bounded tool loop over a SQLite warehouse,
  choosing which analysis to run next based on what it just found.
- **Writer** — has no tools and no database access; turns the Investigator's
  findings into a Markdown briefing. It physically cannot fabricate numbers.

Architecture mirrors the sibling `azure_rag_simple`: plain `openai` SDK
function-calling, `DefaultAzureCredential`, no frameworks, ~few hundred lines
of Python.

---

## Data

**UCI Online Retail II** (CC BY 4.0). ~800k rows after cleaning, 25 months of
history (2009‑12 → 2011‑12). A single SQLite view `sales_v` stands in for the
"read-only SQL view behind Power BI" the spec describes.

- **Metrics:** `revenue` (`quantity * price`), `units`, `orders`.
- **Dimensions:** `country`, `product` (description), `customer`.

## Investigator tools

Seven bounded tools, each backed by parameterised SQL with a 5-second timeout
and 10k-row cap:

| Tool | What it does |
|---|---|
| `metric_overview` | Current vs prior value for one or more metrics. |
| `period_comparison` | One metric, two periods, with optional filters. |
| `dimension_decomposition` | Rank a metric by one dimension with share of total. |
| `time_series` | Metric over time at day/week/month grain. |
| `top_contributors` | Rank dimension values by signed contribution to a change. |
| `data_sufficiency_check` | Row-count floor before comparisons with tight filters. |
| `list_dimension_values` | Valid values for a dimension — call before filtering. |

Agent hard limit: 15 tool calls per run.

## Layout

```
azure_agent_simple/
├── data/
│   ├── raw/                      # downloaded xlsx (gitignored)
│   ├── warehouse.db              # built by ingest
│   └── runs/<timestamp>/         # per-run outputs
├── scripts/
│   ├── ingest.py                 # download + build warehouse
│   └── run.py                    # trigger one briefing
├── src/
│   ├── config.py
│   ├── db.py                     # sqlite + query helpers
│   ├── tools.py                  # the 7 bounded tools + OpenAI schemas
│   └── agents/
│       ├── investigator.py       # tool loop, emits findings.json
│       └── writer.py             # findings → Markdown briefing
├── .env.example
├── pyproject.toml
└── AZURE_ML_SETUP.md             # full Azure ML Studio walkthrough
```

## Quick start (on an Azure ML compute instance)

See `AZURE_ML_SETUP.md` for the full walkthrough. Short version:

```bash
cd ~/cloudfiles/code/Users/<you>
git clone <your-repo-url> azure_agent_simple
cd azure_agent_simple

conda create -y -n agentsimple python=3.11 && conda activate agentsimple
pip install -e .

az login --use-device-code
cp .env.example .env && nano .env      # fill OPENAI_ENDPOINT + CHAT_DEPLOYMENT

python scripts/ingest.py                # one-off, builds data/warehouse.db
python scripts/run.py                   # produces data/runs/<ts>/briefing.md
```

## Run artefacts

Each `python scripts/run.py` writes into `data/runs/<timestamp>/`:

- `briefing.md` — analyst-facing output, with inline `[tool_call_id]` citations.
- `findings.json` — structured findings from the Investigator.
- `tool_calls.jsonl` — every tool call with SQL, arguments, result. This is
  the audit trail for validating "no fabricated claims" (success criterion 3).
- `run.json` — reference date, deployment, iteration count, timing.

## Running as an Azure ML job (optional)

The happy path above is just `python scripts/run.py`. If you want a tracked
run in Studio → **Jobs**, submit from the compute-instance terminal using the
`azure-ai-ml` SDK (pre-installed on the instance — no local CLI needed):

```python
# submit_job.py
from azure.ai.ml import MLClient, command
from azure.identity import DefaultAzureCredential

ml = MLClient(
    DefaultAzureCredential(),
    subscription_id="<sub>",
    resource_group_name="<rg>",
    workspace_name="<ws>",
)
ml.jobs.create_or_update(
    command(
        code="./",
        command="pip install -e . && python scripts/run.py",
        environment="azureml://registries/azureml/environments/sklearn-1.5/labels/latest",
        compute="ci-agentsimple",
        display_name="trend-briefing",
    )
)
```

Then `python submit_job.py` from the terminal. Logs and the `data/runs/`
folder appear under the job in Studio.

## Out of scope

- No follow-up Q&A mode (§3.2 of the spec is deliberately deferred).
- No Validator agent yet — add once Investigator + Writer are stable.
- No scheduler — runs are triggered manually (`python scripts/run.py`).
- No orchestration framework (LangChain, AutoGen, CrewAI, Semantic Kernel) —
  the entire agent loop is under 200 lines.
