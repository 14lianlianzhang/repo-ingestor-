import os
import shutil
import hashlib
from git import Repo, GitCommandError
from typing import Dict, List
from pathlib import Path
from .db import SessionLocal, Repo as RepoModel, Commit as CommitModel, File as FileModel
import json

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
    import hashlib
    h = hashlib.sha256(repo_url.encode("utf-8")).hexdigest()[:12]
    return os.path.join(CLONE_BASE_DIR, h)

def ensure_local_repo(repo_url: str, branch: str = None, depth: int = 1) -> Dict[str, str]:
    """
    If local clone exists, do fetch and checkout branch. Otherwise clone.
    Returns dict with path and current commit sha.
    """
    target_dir = _safe_repo_dir(repo_url)
    if os.path.exists(target_dir) and os.path.isdir(os.path.join(target_dir, '.git')):
        repo = Repo(target_dir)
        try:
            origin = repo.remotes.origin
            origin.fetch()
            if branch:
                repo.git.checkout(branch)
            # get latest commit
            commit = repo.head.commit.hexsha
            return {"path": target_dir, "commit": commit}
        except Exception as e:
            # fallback to reclone if repo is corrupted
            shutil.rmtree(target_dir)
    # fresh clone
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

def list_changed_files(repo_path: str, old_commit: str, new_commit: str) -> List[str]:
    repo = Repo(repo_path)
    try:
        diff = repo.git.diff('--name-only', f'{old_commit}..{new_commit}')
        files = [p for p in diff.split('\n') if p]
        return files
    except Exception:
        return []

def scan_files(repo_path: str, paths: List[str] = None) -> List[Dict]:
    files_meta = []
    if paths is None:
        # full walk
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
    else:
        for rel in paths:
            fpath = os.path.join(repo_path, rel)
            if not os.path.exists(fpath):
                continue
            try:
                size = os.path.getsize(fpath)
                fname = os.path.basename(rel)
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

def upsert_repo_commit_and_files(repo_url: str, commit_sha: str, files: List[Dict]):
    """
    Persist repo, commit and files into Postgres (upsert basic fields).
    """
    db = SessionLocal()
    try:
        # upsert repo
        repo_record = db.query(RepoModel).filter(RepoModel.remote_url == repo_url).first()
        if not repo_record:
            # derive owner/name heuristically
            owner = None
            name = None
            try:
                parts = repo_url.rstrip('.git').split('/')
                name = parts[-1]
                owner = parts[-2]
            except Exception:
                pass
            full_name = f"{owner}/{name}" if owner and name else repo_url
            repo_record = RepoModel(owner=owner, name=name, full_name=full_name, remote_url=repo_url)
            db.add(repo_record)
            db.commit()
            db.refresh(repo_record)
        # insert commit if not exists
        commit_record = db.query(CommitModel).filter(CommitModel.repo_id == repo_record.id, CommitModel.commit_sha == commit_sha).first()
        if not commit_record:
            commit_record = CommitModel(repo_id=repo_record.id, commit_sha=commit_sha)
            db.add(commit_record)
            db.commit()
            db.refresh(commit_record)
        # upsert files based on path+commit
        for f in files:
            file_record = db.query(FileModel).filter(FileModel.repo_id == repo_record.id, FileModel.commit_id == commit_record.id, FileModel.path == f['path']).first()
            if not file_record:
                file_record = FileModel(repo_id=repo_record.id, commit_id=commit_record.id, path=f['path'], size=f.get('size'), language=f.get('language'), content_hash=f.get('content_hash'))
                db.add(file_record)
            else:
                # update content_hash/size
                file_record.content_hash = f.get('content_hash')
                file_record.size = f.get('size')
                file_record.language = f.get('language')
        db.commit()
    finally:
        db.close()

def clone_and_scan(repo_url: str, branch: str = None, depth: int = 1, prev_commit: str = None) -> Dict:
    info = ensure_local_repo(repo_url, branch=branch, depth=depth)
    repo_path = info['path']
    commit = info['commit']
    files = []
    if prev_commit and prev_commit != commit:
        # incremental: list changed files
        changed = list_changed_files(repo_path, prev_commit, commit)
        files = scan_files(repo_path, changed)
    else:
        files = scan_files(repo_path)
    # persist
    upsert_repo_commit_and_files(repo_url, commit, files)
    return {"repo_path": repo_path, "commit": commit, "file_count": len(files), "files": files}