"""
SkillsBench OpenClaw Evaluator.

Evaluates OpenClaw's ability to use skills by running tasks from SkillsBench.

Usage:
    # Prepare benchmark data (clone and filter tasks)
    uv run skill_bench_eval.py prepare

    # List available tasks
    uv run skill_bench_eval.py list

    # Run all tasks
    uv run skill_bench_eval.py run --token YOUR_TOKEN

    # Run specific task
    uv run skill_bench_eval.py run --task 3d-scan-calc --token YOUR_TOKEN
"""

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import requests


SKILLSBENCH_REPO = "https://github.com/benchflow-ai/skillsbench.git"

EXCLUDED_TASKS = {
    "gh-repo-analytics",
    "mhc-layer-impl",
    "pedestrian-traffic-counting",
    "pg-essay-to-audiobook",
    "scheduling-email-assistant",
    "speaker-diarization-subtitles",
    "multilingual-video-dubbing",
    "trend-anomaly-causal-inference",
    "video-filler-word-remover",
    "video-tutorial-indexer",
}

PROJECT_ROOT = Path(__file__).parent.resolve()
BENCH_DATA_DIR = PROJECT_ROOT / "bench_data"
TASKS_DIR = BENCH_DATA_DIR / "tasks"
OPENCLAW_WORKSPACE = Path.home() / ".openclaw" / "workspace"
OPENCLAW_SKILLS_DIR = OPENCLAW_WORKSPACE / "skills"
WORK_DIR = OPENCLAW_WORKSPACE / "bench_work"
OUTPUT_DIR = PROJECT_ROOT / "bench_output"


def send_message(
    base_url: str, token: str, user: str, message: str, timeout: int = 2400
) -> tuple[str, dict]:
    """Send a single message to the OpenClaw responses API.

    Returns (reply_text, usage) where usage has input_tokens, output_tokens, total_tokens.
    """
    url = f"{base_url}/v1/responses"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    payload = {
        "model": "openclaw",
        "input": message,
        "stream": False,
    }
    if user:
        payload["user"] = user

    resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    body = resp.json()
    usage = body.get("usage", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
    
    try:
        for item in body.get("output", []):
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        return content.get("text", ""), usage
        for item in body.get("output", []):
            if "text" in item:
                return item["text"], usage
            for content in item.get("content", []):
                if "text" in content:
                    return content["text"], usage
    except (KeyError, TypeError, IndexError):
        pass
    return f"[ERROR: could not extract text from response: {body}]", usage


def get_session_id(user: str) -> str | None:
    """Read the current session ID for the given user from sessions.json."""
    sessions_file = Path.home() / ".openclaw" / "agents" / "main" / "sessions" / "sessions.json"
    try:
        with open(sessions_file, "r") as f:
            data = json.load(f)
        key = f"agent:main:openresponses-user:{user}"
        return data.get(key, {}).get("sessionId")
    except Exception as e:
        print(f"    [warn] could not read session ID: {e}", file=sys.stderr)
        return None


def delete_session(user: str) -> bool:
    """Delete a session from sessions.json and remove its .jsonl file."""
    sessions_dir = Path.home() / ".openclaw" / "agents" / "main" / "sessions"
    sessions_file = sessions_dir / "sessions.json"
    
    try:
        if not sessions_file.exists():
            return False
        
        with open(sessions_file, "r") as f:
            data = json.load(f)
        
        key = f"agent:main:openresponses-user:{user}"
        session_info = data.get(key)
        
        if not session_info:
            return False
        
        session_id = session_info.get("sessionId")
        
        del data[key]
        
        with open(sessions_file, "w") as f:
            json.dump(data, f, indent=2)
        
        if session_id:
            session_jsonl = sessions_dir / f"{session_id}.jsonl"
            if session_jsonl.exists():
                session_jsonl.unlink()
        
        print(f"    [session] deleted session for user: {user}", file=sys.stderr)
        return True
        
    except Exception as e:
        print(f"    [warn] could not delete session: {e}", file=sys.stderr)
        return False


def reset_session(session_id: str) -> None:
    """Archive the session .jsonl file by renaming it with a timestamp suffix."""
    sessions_dir = Path.home() / ".openclaw" / "agents" / "main" / "sessions"
    src = sessions_dir / f"{session_id}.jsonl"
    dst = f"{src}.{int(time.time())}"
    try:
        if src.exists():
            src.rename(dst)
            print(f"    [reset] archived {session_id}.jsonl", file=sys.stderr)
    except Exception as e:
        print(f"    [warn] could not archive session file: {e}", file=sys.stderr)


def backup_skills() -> Optional[Path]:
    """Backup current skills directory. Returns backup path or None if no skills exist."""
    if not OPENCLAW_SKILLS_DIR.exists():
        return None
    backup_path = OPENCLAW_SKILLS_DIR.parent / f"skills_backup_{int(time.time())}"
    try:
        shutil.copytree(OPENCLAW_SKILLS_DIR, backup_path)
        print(f"    [skills] backed up to {backup_path.name}", file=sys.stderr)
        return backup_path
    except Exception as e:
        print(f"    [warn] could not backup skills: {e}", file=sys.stderr)
        return None


def rewrite_skill_paths(src_skills_dir: Path, dest_skills_dir: Path) -> None:
    """Rewrite /root/.claude/skills/ paths in skill files to OpenClaw workspace paths."""
    old_path = "/root/.claude/skills/"
    new_path = "skills/"
    
    for file_path in dest_skills_dir.rglob("*"):
        if file_path.is_file() and file_path.suffix in [".md", ".py", ".txt", ".sh"]:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                
                if old_path in content:
                    content = content.replace(old_path, new_path)
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(content)
                    print(f"    [skills] rewrote paths in {file_path.relative_to(dest_skills_dir)}", file=sys.stderr)
            except Exception as e:
                print(f"    [warn] could not rewrite {file_path}: {e}", file=sys.stderr)


def safe_rmtree(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        def _onerror(func, p, exc_info):
            try:
                if os.path.isdir(p):
                    os.chmod(p, stat.S_IRWXU)
                else:
                    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
            except Exception:
                pass
            try:
                func(p)
            except Exception:
                pass

        shutil.rmtree(path, onerror=_onerror)
        return True
    except Exception:
        return False


def replace_skills(task_skills_dir: Path) -> bool:
    """Replace skills directory with task-specific skills."""
    try:
        if OPENCLAW_SKILLS_DIR.exists():
            if not safe_rmtree(OPENCLAW_SKILLS_DIR):
                fallback = OPENCLAW_SKILLS_DIR.parent / f"skills_stash_{int(time.time())}"
                OPENCLAW_SKILLS_DIR.rename(fallback)
        if task_skills_dir.exists():
            shutil.copytree(task_skills_dir, OPENCLAW_SKILLS_DIR)
            rewrite_skill_paths(task_skills_dir, OPENCLAW_SKILLS_DIR)
            print(f"    [skills] replaced with {task_skills_dir.name}", file=sys.stderr)
        else:
            OPENCLAW_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
            print(f"    [skills] created empty skills dir", file=sys.stderr)
        return True
    except Exception as e:
        print(f"    [error] could not replace skills: {e}", file=sys.stderr)
        return False


def restore_skills(backup_path: Optional[Path]) -> None:
    """Restore skills directory from backup."""
    try:
        if OPENCLAW_SKILLS_DIR.exists():
            safe_rmtree(OPENCLAW_SKILLS_DIR)
        if backup_path and backup_path.exists():
            shutil.copytree(backup_path, OPENCLAW_SKILLS_DIR)
            safe_rmtree(backup_path)
            print(f"    [skills] restored from backup", file=sys.stderr)
        else:
            OPENCLAW_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
            print(f"    [skills] created empty skills dir", file=sys.stderr)
    except Exception as e:
        print(f"    [warn] could not restore skills: {e}", file=sys.stderr)


def rewrite_work_dir_file_paths(task_dir: Path, work_dir: Path) -> None:
    abs_work_dir = str(work_dir)

    def replace_abs_dir(text: str, src: str, dst: str) -> str:
        pattern = re.compile(rf"(^|(?<=[\s'\"`(])){re.escape(src)}", re.MULTILINE)
        return pattern.sub(lambda m: f"{m.group(1)}{dst}", text)

    allowed_suffixes = {
        ".py",
        ".sh",
        ".txt",
        ".md",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
    }

    for file_path in work_dir.rglob("*"):
        if not file_path.is_file():
            continue
        if any(part in {"logs", "__pycache__", "data"} for part in file_path.parts):
            continue
        if file_path.suffix and file_path.suffix not in allowed_suffixes:
            continue
        if not file_path.suffix and file_path.name not in {"solve.sh", "run.sh"}:
            continue

        try:
            head = file_path.read_bytes()[:2048]
            if b"\x00" in head:
                continue
        except Exception:
            continue

        try:
            original = file_path.read_text(encoding="utf-8", errors="strict")
        except Exception:
            try:
                original = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

        updated = original
        updated = replace_abs_dir(updated, "/app/environment/", f"{abs_work_dir}/")
        updated = replace_abs_dir(updated, "/root/", f"{abs_work_dir}/")
        updated = replace_abs_dir(updated, "/app/", f"{abs_work_dir}/")
        updated = replace_abs_dir(updated, "/workspace/", f"{abs_work_dir}/workspace/")
        updated = replace_abs_dir(updated, "/output/", f"{abs_work_dir}/output/")
        updated = replace_abs_dir(updated, "/data/", f"{abs_work_dir}/data/")
        updated = replace_abs_dir(updated, "/logs/", f"{abs_work_dir}/logs/")

        double_prefix = f"{abs_work_dir}{abs_work_dir}"
        while double_prefix in updated:
            updated = updated.replace(double_prefix, abs_work_dir)

        if updated != original:
            try:
                file_path.write_text(updated, encoding="utf-8")
            except Exception:
                continue


def prepare_work_dir(task_dir: Path) -> Path:
    """Prepare working directory for a task. Returns work directory path."""
    task_name = task_dir.name
    work_path = WORK_DIR / task_name
    
    if work_path.exists():
        safe_rmtree(work_path)
    work_path.mkdir(parents=True, exist_ok=True)
    
    env_dir = task_dir / "environment"
    if env_dir.exists():
        for item in env_dir.iterdir():
            if item.name != "skills":
                if item.is_dir():
                    shutil.copytree(item, work_path / item.name)
                else:
                    shutil.copy2(item, work_path / item.name)
        rewrite_work_dir_file_paths(task_dir, work_path)
    
    print(f"    [work] prepared {work_path}", file=sys.stderr)
    return work_path


def rewrite_instruction_paths(instruction: str, task_dir: Path, work_dir: Path) -> str:
    """Rewrite various root paths in instruction to OpenClaw workspace paths.
    
    Handles:
    - /root/ → bench_work/xxx/
    - /app/ → bench_work/xxx/
    - /workspace/ → bench_work/xxx/workspace/
    - /output/ → bench_work/xxx/output/
    - /data/ → bench_work/xxx/data/
    
    Also prepends a working directory notice to ensure output files are created in the correct location.
    """
    env_dir = task_dir / "environment"
    
    work_dir_relative = work_dir.relative_to(OPENCLAW_WORKSPACE)
    work_dir_str = str(work_dir_relative)
    
    result = instruction
    
    def replace_abs_dir(text: str, src: str, dst: str) -> str:
        pattern = re.compile(rf"(^|(?<=[\s'\"`(])){re.escape(src)}", re.MULTILINE)
        return pattern.sub(lambda m: f"{m.group(1)}{dst}", text)
    
    result = replace_abs_dir(result, "/root/", f"{work_dir_str}/")
    result = replace_abs_dir(result, "/app/", f"{work_dir_str}/")
    
    result = replace_abs_dir(result, "/workspace/", f"{work_dir_str}/workspace/")
    result = replace_abs_dir(result, "/output/", f"{work_dir_str}/output/")
    result = replace_abs_dir(result, "/data/", f"{work_dir_str}/data/")
    
    double_prefix = f"{work_dir_str}{work_dir_str}"
    if double_prefix in result:
        result = result.replace(double_prefix, work_dir_str)

    def strip_work_dir_prefix(text: str) -> str:
        prefix = f"{work_dir_str}/"
        pattern = re.compile(rf"(^|(?<=[\s'\"`(])){re.escape(prefix)}", re.MULTILINE)
        return pattern.sub(lambda m: m.group(1), text)

    result = strip_work_dir_prefix(result)
    
    env_files = []
    if env_dir.exists():
        for item in env_dir.iterdir():
            if item.name != "skills":
                env_files.append(item.name)
    
    for filename in env_files:
        result = result.replace(f"/root/{filename}", f"{work_dir_str}/{filename}")
        result = result.replace(f"/app/{filename}", f"{work_dir_str}/{filename}")
    
    work_dir_notice = f"""**IMPORTANT: Working Directory**

All input files are located in: {work_dir_str}/
All output files MUST be created in: {work_dir_str}/

Use paths relative to that directory (do NOT create nested {work_dir_str}/ inside it).

For example:
- Read input: data/...
- Write output: output/...

---

"""
    
    result = work_dir_notice + result
    
    return result


def get_available_tasks() -> list[Path]:
    """Get list of available task directories."""
    if not TASKS_DIR.exists():
        return []
    return sorted([d for d in TASKS_DIR.iterdir() if d.is_dir() and d.name not in EXCLUDED_TASKS])


def run_prepare(args: argparse.Namespace) -> None:
    """Prepare benchmark data by cloning SkillsBench and filtering tasks."""
    print("=== Preparing SkillsBench data ===", file=sys.stderr)
    
    if BENCH_DATA_DIR.exists():
        if args.force:
            print(f"    Removing existing {BENCH_DATA_DIR} (--force)...", file=sys.stderr)
            shutil.rmtree(BENCH_DATA_DIR)
        else:
            print(f"    {BENCH_DATA_DIR} already exists. Use --force to re-download.", file=sys.stderr)
            tasks_dir = BENCH_DATA_DIR / "tasks"
            if tasks_dir.exists():
                excluded_count = 0
                for task_name in EXCLUDED_TASKS:
                    task_path = tasks_dir / task_name
                    if task_path.exists():
                        shutil.rmtree(task_path)
                        print(f"    [exclude] removed {task_name}", file=sys.stderr)
                        excluded_count += 1
                
                remaining = [d.name for d in tasks_dir.iterdir() if d.is_dir()]
                print(f"\n    {len(remaining)} tasks available, {excluded_count} excluded.", file=sys.stderr)
                print(f"    Tasks: {', '.join(sorted(remaining))}", file=sys.stderr)
            return
    
    temp_dir = PROJECT_ROOT / f"temp_skillsbench_{int(time.time())}"
    
    print(f"    Cloning {SKILLSBENCH_REPO}...", file=sys.stderr)
    print(f"    (this may take a moment...)", file=sys.stderr)
    
    process = subprocess.Popen(
        ["git", "clone", "--progress", SKILLSBENCH_REPO, str(temp_dir)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    
    while True:
        line = process.stderr.readline()
        if not line and process.poll() is not None:
            break
        if line:
            line = line.strip()
            if line:
                print(f"    [git] {line}", file=sys.stderr)
    
    if process.returncode != 0:
        print(f"    [error] git clone failed with code {process.returncode}", file=sys.stderr)
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        sys.exit(1)
    
    print(f"    Extracting tasks directory...", file=sys.stderr)
    
    src_tasks = temp_dir / "tasks"
    if not src_tasks.exists():
        print(f"    [error] tasks directory not found in cloned repo", file=sys.stderr)
        shutil.rmtree(temp_dir)
        sys.exit(1)
    
    BENCH_DATA_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_tasks, TASKS_DIR)
    
    print(f"    Cleaning up temp files...", file=sys.stderr)
    shutil.rmtree(temp_dir)
    
    excluded_count = 0
    for task_name in EXCLUDED_TASKS:
        task_path = TASKS_DIR / task_name
        if task_path.exists():
            shutil.rmtree(task_path)
            print(f"    [exclude] removed {task_name}", file=sys.stderr)
            excluded_count += 1
    
    remaining = [d.name for d in TASKS_DIR.iterdir() if d.is_dir()]
    print(f"\n    Done! {len(remaining)} tasks available, {excluded_count} excluded.", file=sys.stderr)
    print(f"    Tasks: {', '.join(sorted(remaining))}", file=sys.stderr)


def run_list(args: argparse.Namespace) -> None:
    """List available tasks."""
    tasks = get_available_tasks()
    
    if not tasks:
        print("No tasks found. Run 'prepare' first.", file=sys.stderr)
        return
    
    print(f"=== Available Tasks ({len(tasks)}) ===", file=sys.stderr)
    for i, task_dir in enumerate(tasks, 1):
        instruction_file = task_dir / "instruction.md"
        has_instruction = instruction_file.exists()
        skills_dir = task_dir / "environment" / "skills"
        has_skills = skills_dir.exists()
        status = f"instruction={'Y' if has_instruction else 'N'} skills={'Y' if has_skills else 'N'}"
        print(f"  {i:3d}. {task_dir.name} [{status}]", file=sys.stderr)


def run_verification(task_dir: Path, work_dir: Path) -> dict:
    """Run task verification tests. Returns verification result."""
    task_name = task_dir.name
    tests_dir = task_dir / "tests"
    
    result = {
        "verified": False,
        "passed": False,
        "test_output": None,
        "error": None,
        "test_score": None,
    }
    
    if not tests_dir.exists():
        result["error"] = "no tests directory"
        result["verified"] = True
        result["passed"] = True
        print(f"    [verify] no tests directory, skipping verification", file=sys.stderr)
        return result
    
    test_sh = tests_dir / "test.sh"
    test_py = tests_dir / "test_outputs.py"
    
    if not test_sh.exists() and not test_py.exists():
        result["error"] = "no test files found"
        result["verified"] = True
        result["passed"] = True
        print(f"    [verify] no test files, skipping verification", file=sys.stderr)
        return result
    
    print(f"    [verify] running tests...", file=sys.stderr)
    
    logs_dir = work_dir / "logs" / "verifier"
    logs_dir.mkdir(parents=True, exist_ok=True)

    if tests_dir.exists():
        for item in tests_dir.rglob("*"):
            if not item.is_file():
                continue
            if item.suffix == ".sh":
                continue
            if item.suffix == ".py" and item.name == "test_outputs.py":
                continue
            rel = item.relative_to(tests_dir)
            dest = work_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                shutil.copy2(item, dest)
    
    work_dir_relative = work_dir.relative_to(OPENCLAW_WORKSPACE)
    work_dir_str = str(work_dir_relative)
    tests_dir_relative = str(task_dir / "tests")
    
    if test_py.exists():
        try:
            with open(test_py, "r", encoding="utf-8") as f:
                test_content = f.read()
            
            expected_paths = set(re.findall(r"""['"](/root/[^'"]+)['"]""", test_content))
            expected_paths.update(re.findall(r"""['"](/app/[^'"]+)['"]""", test_content))
            for full_path in sorted(expected_paths):
                if full_path.endswith("/"):
                    continue
                try:
                    if full_path.startswith("/root/"):
                        rel = Path(full_path).relative_to("/root")
                    else:
                        rel = Path(full_path).relative_to("/app")
                except ValueError:
                    continue
                src = OPENCLAW_WORKSPACE / rel
                dest = work_dir / rel
                if dest.exists():
                    continue
                if src.exists() and src.is_file():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dest))

            def replace_abs_token(text: str, src: str, dst: str) -> str:
                pattern = re.compile(
                    rf"(^|(?<=[\s'\"`(])){re.escape(src)}(?=($|[\s'\"`)\]]))",
                    re.MULTILINE,
                )
                return pattern.sub(lambda m: f"{m.group(1)}{dst}", text)

            def replace_abs_prefix(text: str, src: str, dst: str) -> str:
                pattern = re.compile(
                    rf"(^|(?<=[\s'\"`(])){re.escape(src)}",
                    re.MULTILINE,
                )
                return pattern.sub(lambda m: f"{m.group(1)}{dst}", text)

            def rewrite_test_text(text: str) -> str:
                abs_token_map = {
                    "/root": f"{work_dir_str}",
                    "/app": f"{work_dir_str}",
                    "/workspace": f"{work_dir_str}/workspace",
                    "/output": f"{work_dir_str}/output",
                    "/data": f"{work_dir_str}/data",
                    "/logs": f"{work_dir_str}/logs",
                    "/tests": f"{tests_dir_relative}",
                }
                for src, dst in abs_token_map.items():
                    text = replace_abs_token(text, src, dst)

                abs_prefix_map = {
                    "/root/": f"{work_dir_str}/",
                    "/app/": f"{work_dir_str}/",
                    "/workspace/": f"{work_dir_str}/workspace/",
                    "/output/": f"{work_dir_str}/output/",
                    "/data/": f"{work_dir_str}/data/",
                    "/logs/": f"{work_dir_str}/logs/",
                    "/tests/": f"{tests_dir_relative}/",
                }
                for src, dst in abs_prefix_map.items():
                    text = replace_abs_prefix(text, src, dst)

                text = text.replace('sys.path.insert(0, "/tests/src")', f'sys.path.insert(0, "{tests_dir_relative}/src")')
                text = text.replace("sys.path.insert(0, '/tests/src')", f"sys.path.insert(0, '{tests_dir_relative}/src')")
                text = text.replace('sys.path.insert(0, "/root/workspace")', f'sys.path.insert(0, "{work_dir_str}")')
                text = text.replace("sys.path.insert(0, '/root/workspace')", f"sys.path.insert(0, '{work_dir_str}')")
                text = text.replace('sys.path.insert(0, "/root")', f'sys.path.insert(0, "{work_dir_str}")')
                text = text.replace("sys.path.insert(0, '/root')", f"sys.path.insert(0, '{work_dir_str}')")
                text = text.replace("cwd='/root'", f"cwd='{work_dir_str}'")
                text = text.replace('cwd="/root"', f'cwd="{work_dir_str}"')
                return text

            if tests_dir.exists():
                for helper_py in tests_dir.rglob("*.py"):
                    if helper_py.name == "test_outputs.py":
                        continue
                    rel = helper_py.relative_to(tests_dir)
                    dest = work_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if not dest.exists():
                        shutil.copy2(helper_py, dest)
                    try:
                        helper_text = dest.read_text(encoding="utf-8")
                        rewritten = rewrite_test_text(helper_text)
                        if rewritten != helper_text:
                            dest.write_text(rewritten, encoding="utf-8")
                    except Exception:
                        pass

            test_content = rewrite_test_text(test_content)
            
            local_test_py = work_dir / "test_outputs.py"
            with open(local_test_py, "w", encoding="utf-8") as f:
                f.write(test_content)
            
            env = os.environ.copy()
            env["PYTHONPATH"] = str(work_dir)
            
            test_cmd = [
                "python", "-m", "pytest",
                str(local_test_py),
                "-v", "--tb=short",
                f"--junitxml={logs_dir}/junit.xml",
            ]
            
            print(f"    [verify] running: pytest test_outputs.py", file=sys.stderr)
            
            proc_result = subprocess.run(
                test_cmd,
                capture_output=True,
                text=True,
                cwd=str(OPENCLAW_WORKSPACE),
                env=env,
                timeout=300,
            )
            
            result["test_output"] = proc_result.stdout + proc_result.stderr
            result["verified"] = True
            result["passed"] = proc_result.returncode == 0

            summary_text = result["test_output"] or ""
            collected_match = re.search(r"collected\s+(\d+)\s+items", summary_text)
            passed_count = len(re.findall(r"\bPASSED\s+\[", summary_text))
            failed_count = len(re.findall(r"\bFAILED\s+\[", summary_text))
            skipped_count = len(re.findall(r"\bSKIPPED\s+\[", summary_text))
            total_count = int(collected_match.group(1)) if collected_match else None
            if total_count is None and (passed_count or failed_count or skipped_count):
                total_count = passed_count + failed_count + skipped_count
            if total_count:
                score = passed_count / total_count
                result["test_score"] = round(score, 2)
            
            if result["passed"]:
                print(f"    [verify] PASSED", file=sys.stderr)
            else:
                print(f"    [verify] FAILED", file=sys.stderr)
                if proc_result.stdout:
                    print(f"    [verify stdout] {proc_result.stdout[:500]}", file=sys.stderr)
                if proc_result.stderr:
                    print(f"    [verify stderr] {proc_result.stderr[:500]}", file=sys.stderr)
            
        except subprocess.TimeoutExpired:
            result["error"] = "test timeout"
            result["verified"] = True
            result["passed"] = False
            print(f"    [verify] TIMEOUT", file=sys.stderr)
        except Exception as e:
            result["error"] = str(e)
            result["verified"] = True
            result["passed"] = False
            print(f"    [verify] ERROR: {e}", file=sys.stderr)
    else:
        result["verified"] = True
        result["passed"] = True
        print(f"    [verify] no pytest file, skipping", file=sys.stderr)
    
    return result


def run_task(
    task_dir: Path,
    base_url: str,
    token: str,
    user: str,
    output_base: Path,
) -> dict:
    """Run a single task. Returns result dict."""
    task_name = task_dir.name
    print(f"\n=== Task: {task_name} ===", file=sys.stderr)
    
    task_output_dir = output_base / task_name
    if task_output_dir.exists():
        shutil.rmtree(task_output_dir)
    task_output_dir.mkdir(parents=True, exist_ok=True)
    
    result = {
        "task": task_name,
        "status": "pending",
        "response": None,
        "usage": {},
        "error": None,
        "verification": None,
        "start_time": time.time(),
        "end_time": None,
    }
    
    instruction_file = task_dir / "instruction.md"
    if not instruction_file.exists():
        result["status"] = "error"
        result["error"] = "instruction.md not found"
        print(f"    [error] instruction.md not found", file=sys.stderr)
        return result
    
    task_skills_dir = task_dir / "environment" / "skills"
    
    # backup_path = backup_skills()
    work_dir = None
    
    try:
        if not replace_skills(task_skills_dir):
            result["status"] = "error"
            result["error"] = "failed to replace skills"
            return result
        
        work_dir = prepare_work_dir(task_dir)
        
        with open(instruction_file, "r", encoding="utf-8") as f:
            instruction = f.read()
        
        instruction = rewrite_instruction_paths(instruction, task_dir, work_dir)
        
        with open(task_output_dir / "instruction.md", "w", encoding="utf-8") as f:
            f.write(instruction)
        print(f"    [saved] instruction.md -> {task_output_dir.name}/instruction.md", file=sys.stderr)
        
        print(f"    [sending] instruction to OpenClaw...", file=sys.stderr)
        response, usage = send_message(base_url, token, user, instruction)
        
        result["status"] = "completed"
        result["response"] = response
        result["usage"] = usage
        
        with open(task_output_dir / "response.txt", "w", encoding="utf-8") as f:
            f.write(response)
        print(f"    [saved] response.txt -> {task_output_dir.name}/response.txt", file=sys.stderr)
        
        preview = response.replace("\n", " | ")[:100]
        print(f"    [response] {preview}{'...' if len(response) > 100 else ''}", file=sys.stderr)
        print(f"    [tokens] in={usage.get('input_tokens', 0)} out={usage.get('output_tokens', 0)}", file=sys.stderr)
        
        if work_dir:
            verification_result = run_verification(task_dir, work_dir)
            result["verification"] = verification_result
            
            with open(task_output_dir / "verification.json", "w", encoding="utf-8") as f:
                json.dump(verification_result, f, indent=2, ensure_ascii=False)
            print(f"    [saved] verification.json -> {task_output_dir.name}/verification.json", file=sys.stderr)
        
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        print(f"    [error] {e}", file=sys.stderr)
    finally:
        # restore_skills(backup_path)

        print(f"    [cleanup] deleted session for user {user}")
        time.sleep(15)
        delete_session(user)
        
        result["end_time"] = time.time()
    
    with open(task_output_dir / "result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"    [saved] result.json -> {task_output_dir.name}/result.json", file=sys.stderr)
    
    return result


def run_run(args: argparse.Namespace) -> None:
    """Run benchmark tasks."""
    tasks = get_available_tasks()
    
    if not tasks:
        print("No tasks found. Run 'prepare' first.", file=sys.stderr)
        sys.exit(1)
    
    if args.task and (args.count is not None or args.start is not None or args.end is not None):
        print("Error: --task cannot be combined with --count/--start/--end", file=sys.stderr)
        sys.exit(1)
    if args.count is not None and (args.start is not None or args.end is not None):
        print("Error: --count cannot be combined with --start/--end", file=sys.stderr)
        sys.exit(1)
    
    if args.task:
        task_dir = TASKS_DIR / args.task
        if not task_dir.exists():
            print(f"Task not found: {args.task}", file=sys.stderr)
            sys.exit(1)
        tasks = [task_dir]
    elif args.start is not None or args.end is not None:
        start = args.start or 1
        end = args.end or len(tasks)
        if start < 1 or end < 1 or start > end:
            print(f"Error: invalid range --start {start} --end {end}", file=sys.stderr)
            sys.exit(1)
        if start > len(tasks):
            print(f"Error: --start {start} exceeds available tasks ({len(tasks)})", file=sys.stderr)
            sys.exit(1)
        end = min(end, len(tasks))
        tasks = tasks[start - 1 : end]
    elif args.count:
        tasks = tasks[:args.count]
    
    output_base = PROJECT_ROOT / "output"
    output_base.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    
    results = []
    total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    
    print(f"=== Running {len(tasks)} task(s) ===", file=sys.stderr)
    print(f"    base_url: {args.base_url}", file=sys.stderr)
    print(f"    output: {output_base}", file=sys.stderr)
    
    for task_dir in tasks:
        user = args.user or f"skillsbench-{task_dir.name}-{int(time.time())}"
        print(f"    user: {user}", file=sys.stderr)
        
        result = run_task(
            task_dir=task_dir,
            base_url=args.base_url,
            token=args.token,
            user=user,
            output_base=output_base,
        )
        results.append(result)
        
        if result["usage"]:
            for k in total_usage:
                total_usage[k] += result["usage"].get(k, 0)
    
    summary = {
        "total_tasks": len(tasks),
        "completed": sum(1 for r in results if r["status"] == "completed"),
        "passed": sum(1 for r in results if (r.get("verification") or {}).get("passed", False)),
        "errors": sum(1 for r in results if r["status"] == "error"),
        "total_usage": total_usage,
        "tasks": [r["task"] for r in results],
    }
    summary["pass_rate"] = summary["passed"] / summary["total_tasks"] if summary["total_tasks"] else 0
    summary["score"] = round(
        sum((r.get("verification", {}).get("test_score") or 0) for r in results),
        2,
    )
    
    summary_file = output_base / "summary.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    
    print(f"\n=== Summary ===", file=sys.stderr)
    print(f"    Completed: {summary['completed']}/{summary['total_tasks']}", file=sys.stderr)
    print(f"    Errors: {summary['errors']}", file=sys.stderr)
    print(f"    Total tokens: in={total_usage['input_tokens']} out={total_usage['output_tokens']}", file=sys.stderr)
    print(f"    Results saved to: {OUTPUT_DIR}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="SkillsBench OpenClaw Evaluator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    prepare_parser = subparsers.add_parser("prepare", help="Prepare benchmark data")
    prepare_parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Force re-download even if data already exists",
    )
    
    list_parser = subparsers.add_parser("list", help="List available tasks")
    
    run_parser = subparsers.add_parser("run", help="Run benchmark tasks")
    run_parser.add_argument(
        "--task",
        default=None,
        help="Run specific task only",
    )
    run_parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Run first N tasks only",
    )
    run_parser.add_argument(
        "--start",
        type=int,
        default=None,
        help="Run tasks starting from this index (1-based, same order as list)",
    )
    run_parser.add_argument(
        "--end",
        type=int,
        default=None,
        help="Run tasks ending at this index (inclusive, 1-based, same order as list)",
    )
    run_parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:18789",
        help="OpenClaw gateway base URL",
    )
    run_parser.add_argument(
        "--token",
        default=os.environ.get("OPENCLAW_GATEWAY_TOKEN", "xxx"),
        help="Auth token (or set OPENCLAW_GATEWAY_TOKEN env var)",
    )
    run_parser.add_argument(
        "--user",
        default=None,
        help="User identifier for session",
    )
    
    args = parser.parse_args()
    
    if args.command == "prepare":
        run_prepare(args)
    elif args.command == "list":
        run_list(args)
    elif args.command == "run":
        if not args.token:
            print("Error: --token or OPENCLAW_GATEWAY_TOKEN env var is required", file=sys.stderr)
            sys.exit(1)
        run_run(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
