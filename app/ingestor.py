import os
import shutil
import hashlib
from git import Repo, GitCommandError
from typing import Dict, List
from pathlib import Path

CLONE_BASE_DIR = os.getenv("CLONE_BASE_DIR", "/tmp/repos")

EXT_LANG = {
    ".py": "python",
    ".java": "java",
    ".js": "javascript",
    ".ts": "typescript",
    ".go": "go",
    ".cpp": "cpp",
    ".c": "c",
    ".cs": "csharp",
    ".rb": "ruby",
    ".rs": "rust",
    ".php": "php",
    ".sh": "shell",
}

def _safe_repo_dir(repo_url: str) -> str:
    h = hashlib.sha256(repo_url.encode("utf-8")).hexdigest()[:12]
    return os.path.join(CLONE_BASE_DIR, h)

def clone_repo(repo_url: str, branch: str = None, depth: int = 1) -> Dict[str, str]:
    target_dir = _safe_repo_dir(repo_url)
    if os.path.exists(target_dir):
        shutil.rmtree(target_dir)
    try:
        if branch:
            Repo.clone_from(repo_url, target_dir, branch=branch, depth=depth)
        else:
            Repo.clone_from(repo_url, target_dir, depth=depth)
        repo = Repo(target_dir)
        commit = repo.head.commit.hexsha
        return {"path": target_dir, "commit": commit}
    except GitCommandError as e:
        raise RuntimeError(f"git clone error: {e}")

def scan_files(repo_path: str) -> List[Dict]:
    files_meta = []
    for root, _, files in os.walk(repo_path):
        for fname in files:
            fpath = os.path.join(root, fname)
            try:
                rel = os.path.relpath(fpath, repo_path)
                size = os.path.getsize(fpath)
                ext = Path(fname).suffix.lower()
                lang = EXT_LANG.get(ext, "text")
                with open(fpath, "rb") as fh:
                    content = fh.read()
                content_hash = hashlib.sha256(content).hexdigest()
                files_meta.append({
                    "path": rel,
                    "size": size,
                    "language": lang,
                    "content_hash": content_hash,
                })
            except Exception:
                continue
    return files_meta

def clone_and_scan(repo_url: str, branch: str = None, depth: int = 1) -> Dict:
    info = clone_repo(repo_url, branch=branch, depth=depth)
    files = scan_files(info["path"])
    return {"repo_path": info["path"], "commit": info["commit"], "file_count": len(files), "files": files}
