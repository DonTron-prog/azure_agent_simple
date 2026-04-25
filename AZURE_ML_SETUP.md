# Running the demo from Azure Machine Learning Studio

Step-by-step for when you can't install anything on your laptop (no Azure CLI,
no Python). You'll spin up a compute instance inside an Azure ML workspace and
run the demo from the browser terminal.

> Before you start: you need access to an Azure OpenAI resource with a chat
> deployment (e.g. `gpt-4o`) and the **Cognitive Services OpenAI User** role on
> it. If that's not set up, see the sibling project's
> `azure_rag_simple/MANUAL_SETUP.md` sections 2–3 — the same role and endpoint
> work here.

---

## 1. Create an Azure Machine Learning workspace (if you don't have one)

1. Portal → search **Azure Machine Learning** → **+ Create** → **New workspace**.
2. **Basics** tab:
   - Subscription + resource group: your choice.
   - Workspace name: e.g. `mlw-agentsimple`.
   - Region: same region as your Azure OpenAI resource.
3. **Review + create** → **Create**.
4. Open the workspace → **Launch studio** (or go to https://ml.azure.com).

---

## 2. Create a compute instance

1. ML Studio → left sidebar → **Compute** → **Compute instances** tab →
   **+ New**.
2. Compute name: e.g. `ci-agentsimple`.
3. VM type: **CPU**. VM size: `Standard_DS3_v2` is plenty. `Standard_DS2_v2`
   is also fine.
4. Scheduling: set **auto-shutdown** (e.g. 60 min idle) so it doesn't run
   overnight.
5. **Create**. Wait ~5 minutes until status is **Running**.

---

## 3. Open the browser terminal

On the compute instance row, click **Terminal**. All commands below run in
that browser terminal, not on your laptop.

---

## 4. Get the project onto the compute instance

### Option A (recommended) — git clone

Push this project to GitHub or Azure DevOps, then in the ML terminal:

```bash
cd ~/cloudfiles/code/Users/<your-alias>
git clone <your-repo-url> azure_agent_simple
cd azure_agent_simple
```

> Keep the project under `~/cloudfiles/` so it survives compute instance
> stop/start. Anything outside is wiped on restart.

### Option B — upload a zip via JupyterLab

1. Zip the `azure_agent_simple` folder locally.
2. In ML Studio → **Notebooks**, navigate to
   `~/cloudfiles/code/Users/<your-alias>/`.
3. Drag the zip in. In the terminal:
   ```bash
   cd ~/cloudfiles/code/Users/<your-alias>
   unzip azure_agent_simple.zip
   cd azure_agent_simple
   ```

---

## 5. Create a Python env and install

```bash
conda create -y -n agentsimple python=3.11
conda activate agentsimple
pip install -e .
```

Reactivate the env in any new terminal with `conda activate agentsimple`.

---

## 6. Authenticate to Azure

```bash
az login --use-device-code
```

Follow the printed instructions: open the URL, enter the code, sign in. Pick
your subscription:

```bash
az account set --subscription "<your-subscription-id>"
```

`DefaultAzureCredential` in the Python code will use this token to call Azure
OpenAI with the same identity that has the **Cognitive Services OpenAI User**
role.

---

## 7. Create `.env`

```bash
cp .env.example .env
nano .env
```

Fill in:

| Variable             | Value                                                              |
| -------------------- | ------------------------------------------------------------------ |
| `OPENAI_ENDPOINT`    | Your Azure OpenAI endpoint URL (ends in `.openai.azure.com/`).     |
| `CHAT_DEPLOYMENT`    | Deployment name you created in the resource (e.g. `gpt-4o`).       |
| `WAREHOUSE_DB`       | Leave as `data/warehouse.db`.                                      |
| `RUNS_DIR`           | Leave as `data/runs`.                                              |
| `REFERENCE_DATE`     | Optional. Omit to auto-detect from the max date in the warehouse.  |

---

## 8. Build the warehouse

```bash
python scripts/ingest.py
```

This downloads the **UCI Online Retail II** dataset (~45 MB, CC BY 4.0) into
`data/raw/` and builds `data/warehouse.db`. Takes a couple of minutes. Only
needs to run once per compute instance — re-running is idempotent.

If the UCI URL is unreachable from your compute instance, download the file
manually from the UCI page and drop it at
`data/raw/online_retail_II.xlsx`, then re-run ingest.

---

## 9. Run a briefing

```bash
python scripts/run.py
```

The Investigator calls tools (you'll see ~5–15 tool calls), emits findings,
the Writer composes the Markdown. Output lands at
`data/runs/<timestamp>/briefing.md`. A preview prints to the terminal at the
end of the run.

Each run also writes:
- `findings.json` — the structured output of the Investigator.
- `tool_calls.jsonl` — every tool call with its SQL, arguments, and result
  (this is the audit trail for the "no fabricated claims" success criterion).
- `run.json` — metadata (reference date, iteration count, timing).

---

## 10. (Optional) Run it as an Azure ML job instead

If you want tracked runs in the Studio **Jobs** panel, submit from the same
compute-instance terminal with the Python SDK — no local `az ml` CLI needed.
See the notes in `README.md` under "Running as an Azure ML job" for a minimal
`submit_job.py`. The happy path above (running the scripts directly) is
simpler and sufficient for the PoC.
