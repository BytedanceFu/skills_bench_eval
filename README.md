# openclaw-eval

Evaluate OpenClaw's ability to use skills with [SkillsBench](https://github.com/benchflow-ai/skillsbench) benchmark.

## Installation

```bash
# Install Python dependencies
uv sync

# Install Playwright browsers (required for some tasks)
uv run playwright install chromium
```

## Quick Start

```bash
# 1. Prepare benchmark data (clone SkillsBench and filter tasks)
uv run skill_bench_eval.py prepare

# 2. List available tasks
uv run skill_bench_eval.py list

# 3. Run a single task
uv run skill_bench_eval.py run --task 3d-scan-calc --token YOUR_TOKEN

# 3b. Run a range of tasks by index (same order as list, 1-based, inclusive)
uv run skill_bench_eval.py run --start 6 --end 10 --token YOUR_TOKEN

# 4. Run all tasks
uv run skill_bench_eval.py run --token YOUR_TOKEN
```

## Commands

| Command | Description |
|---------|-------------|
| `prepare` | Clone SkillsBench repository and filter tasks |
| `list` | List all available tasks |
| `run` | Run benchmark tasks |

## Prepare Options

| Flag | Description |
|------|-------------|
| `--force` | Force re-download even if data already exists |

## Run Options

| Flag | Default | Description |
|------|---------|-------------|
| `--task` | all | Run specific task only |
| `--base-url` | `http://127.0.0.1:18789` | OpenClaw gateway URL |
| `--token` | `xxx` | Auth token (or `OPENCLAW_GATEWAY_TOKEN` env var) |
| `--user` | auto-generated | User identifier for session |
| `--start` | none | Run tasks starting from this index (1-based, same order as `list`) |
| `--end` | none | Run tasks ending at this index (inclusive, 1-based, same order as `list`) |

## Excluded Tasks

The following tasks are excluded (require extra API keys or have issues):

| Task | Reason |
|------|--------|
| `gh-repo-analytics` | Requires `GH_AUTH_TOKEN` |
| `mhc-layer-impl` | Requires `MODAL_TOKEN_ID/SECRET` |
| `pedestrian-traffic-counting` | Requires OpenAI/Gemini/Anthropic API keys |
| `pg-essay-to-audiobook` | Requires `OPENAI_API_KEY` + `ELEVENLABS_API_KEY` |
| `scheduling-email-assistant` | Hardcoded volume mount + `HUGGINGFACE_API_TOKEN` |
| `speaker-diarization-subtitles` | build OOM |
| `multilingual-video-dubbing` | Has issues |
| `trend-anomaly-causal-inference` | Requires Anthropic + OpenAI API keys |
| `video-filler-word-remover` | Requires `OPENAI_API_KEY` |
| `video-tutorial-indexer` | Requires `OPENAI_API_KEY` |
| `fix-build-agentops` | Requires BugSwarm build artifacts (`/home/github/build`) + `REPO_ID` |
| `fix-build-google-auto` | Requires BugSwarm build artifacts (`/home/github/build`) + `REPO_ID` |
| `fix-druid-loophole-cve` | Requires BugSwarm build artifacts (`/home/github/build`) + `REPO_ID` |
| `fix-erlang-ssh-cve` | Requires BugSwarm build artifacts (`/home/github/build`) + `REPO_ID` |
| `fix-visual-stability` | Requires running web app on `localhost:3000` for Playwright tests |

## Output Structure

```
output/
├── 3d-scan-calc/           # Task output directory
│   ├── instruction.md      # Instruction sent to OpenClaw (paths rewritten)
│   ├── response.txt        # OpenClaw's full response
│   ├── verification.json   # Test verification result
│   └── result.json         # Task execution summary
├── another-task/
│   └── ...
└── summary.json            # Overall summary report
```

## How It Works

Each task execution follows this flow:

```
1. Backup ~/.openclaw/workspace/skills/
2. Replace skills with task-specific skills
3. Copy environment files to workspace
4. Rewrite paths in instruction.md (/root/ → bench_work/xxx/)
5. Send instruction to OpenClaw
6. Run pytest verification
7. Restore original skills
```

## Dependencies

The following packages are required for test verification:

| Package | Purpose |
|---------|---------|
| `pytest` | Test framework |
| `openpyxl` | Excel file processing |
| `numpy` | Numerical computing |
| `pandas` | Data processing |
| `scipy` | Scientific computing |
| `polars` | Data processing |
| `PyPDF2` | PDF processing |
| `python-docx` | Word document processing |
| `rapidfuzz` | Fuzzy string matching |
| `qutip` | Quantum simulation |
| `unified-planning` | Planning problems |
| `scapy` | Network packet processing |
| `httpx` | HTTP client |
| `playwright` | Browser automation |

All dependencies are installed via `uv sync`. Playwright browsers require separate installation:

```bash
uv run playwright install chromium
```
