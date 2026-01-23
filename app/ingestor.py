"""仓库数据摄取模块

该模块负责从Git仓库获取代码，扫描文件，并将数据持久化到数据库中。
主要功能包括：克隆/更新仓库、扫描文件、提取文件元数据、持久化到数据库。
"""

import os
import shutil
import hashlib
from git import Repo, GitCommandError
from typing import Dict, List
from pathlib import Path
from .db import SessionLocal, Repo as RepoModel, Commit as CommitModel, File as FileModel
import json

# 仓库克隆的基础目录，从环境变量获取，默认值为/tmp/repos
CLONE_BASE_DIR = os.getenv("CLONE_BASE_DIR", "/tmp/repos")

# 文件扩展名到编程语言的映射字典
EXT_LANG = {
    ".py": "python",      # Python文件
    ".java": "java",      # Java文件
    ".js": "javascript",  # JavaScript文件
    ".ts": "typescript",  # TypeScript文件
    ".go": "go",          # Go文件
    ".cpp": "cpp",        # C++文件
    ".c": "c",            # C文件
    ".cs": "csharp",      # C#文件
    ".rb": "ruby",        # Ruby文件
    ".rs": "rust",        # Rust文件
    ".php": "php",        # PHP文件
    ".sh": "shell",       # Shell脚本文件
}


def _safe_repo_dir(repo_url: str) -> str:
    """生成安全的仓库目录名
    
    为了避免URL中的特殊字符导致目录创建失败，使用SHA256哈希值作为目录名。
    
    Args:
        repo_url: Git仓库URL
        
    Returns:
        安全的目录路径字符串
    """
    import hashlib
    # 计算URL的SHA256哈希值，取前12位作为目录名
    h = hashlib.sha256(repo_url.encode("utf-8")).hexdigest()[:12]
    # 拼接基础目录和哈希目录名
    return os.path.join(CLONE_BASE_DIR, h)


def ensure_local_repo(repo_url: str, branch: str = None, depth: int = 1) -> Dict[str, str]:
    """确保本地存在仓库的克隆
    
    如果本地已经存在克隆，则执行fetch和checkout操作；否则执行克隆。
    
    Args:
        repo_url: Git仓库URL
        branch: 要检出的分支，默认为None（使用仓库默认分支）
        depth: 克隆深度，默认为1（只克隆最新提交）
        
    Returns:
        包含本地仓库路径和当前提交SHA的字典
        格式：{"path": str, "commit": str}
    """
    target_dir = _safe_repo_dir(repo_url)  # 获取安全的目标目录
    
    # 检查目录是否存在且是有效的Git仓库
    if os.path.exists(target_dir) and os.path.isdir(os.path.join(target_dir, '.git')):
        repo = Repo(target_dir)  # 打开现有仓库
        try:
            origin = repo.remotes.origin  # 获取origin远程
            origin.fetch()  # 执行fetch操作，获取最新代码
            
            if branch:  # 如果指定了分支，则检出该分支
                repo.git.checkout(branch)
                
            # 获取当前HEAD的提交SHA
            commit = repo.head.commit.hexsha
            return {"path": target_dir, "commit": commit}
        except Exception as e:
            # 如果操作失败（例如仓库损坏），则删除目录并重新克隆
            shutil.rmtree(target_dir)
    
    # 执行新鲜克隆
    try:
        if branch:  # 如果指定了分支，则克隆特定分支
            Repo.clone_from(repo_url, target_dir, branch=branch, depth=depth)
        else:  # 否则克隆默认分支
            Repo.clone_from(repo_url, target_dir, depth=depth)
            
        repo = Repo(target_dir)  # 打开新克隆的仓库
        commit = repo.head.commit.hexsha  # 获取当前提交SHA
        return {"path": target_dir, "commit": commit}
    except GitCommandError as e:
        # 克隆失败时抛出异常
        raise RuntimeError(f"git clone error: {e}")


def list_changed_files(repo_path: str, old_commit: str, new_commit: str) -> List[str]:
    """获取两个提交之间的文件变更列表
    
    Args:
        repo_path: 本地仓库路径
        old_commit: 旧提交SHA
        new_commit: 新提交SHA
        
    Returns:
        变更文件的相对路径列表
    """
    repo = Repo(repo_path)  # 打开本地仓库
    try:
        # 使用git diff命令获取两个提交之间的文件变更列表
        diff = repo.git.diff('--name-only', f'{old_commit}..{new_commit}')
        # 分割结果并过滤空行
        files = [p for p in diff.split('\n') if p]
        return files
    except Exception:
        # 出错时返回空列表
        return []


def scan_files(repo_path: str, paths: List[str] = None) -> List[Dict]:
    """扫描仓库文件并提取元数据
    
    Args:
        repo_path: 本地仓库路径
        paths: 要扫描的文件路径列表，None表示扫描所有文件
        
    Returns:
        文件元数据列表，每个元素包含path、size、language、content_hash字段
    """
    files_meta = []  # 存储文件元数据的列表
    
    if paths is None:
        # 扫描所有文件
        for root, _, files in os.walk(repo_path):
            for fname in files:
                fpath = os.path.join(root, fname)  # 完整文件路径
                try:
                    rel = os.path.relpath(fpath, repo_path)  # 相对仓库根目录的路径
                    size = os.path.getsize(fpath)  # 文件大小（字节）
                    ext = Path(fname).suffix.lower()  # 文件扩展名，转为小写
                    lang = EXT_LANG.get(ext, "text")  # 根据扩展名获取编程语言，默认text
                    
                    # 计算文件内容的SHA256哈希值
                    with open(fpath, "rb") as fh:
                        content = fh.read()
                    content_hash = hashlib.sha256(content).hexdigest()
                    
                    # 添加文件元数据到列表
                    files_meta.append({
                        "path": rel,
                        "size": size,
                        "language": lang,
                        "content_hash": content_hash,
                    })
                except Exception:
                    # 处理单个文件出错时，跳过该文件继续扫描
                    continue
    else:
        # 只扫描指定的文件路径
        for rel in paths:
            fpath = os.path.join(repo_path, rel)  # 完整文件路径
            if not os.path.exists(fpath):  # 检查文件是否存在
                continue
            try:
                size = os.path.getsize(fpath)  # 文件大小（字节）
                fname = os.path.basename(rel)  # 文件名
                ext = Path(fname).suffix.lower()  # 文件扩展名，转为小写
                lang = EXT_LANG.get(ext, "text")  # 根据扩展名获取编程语言，默认text
                
                # 计算文件内容的SHA256哈希值
                with open(fpath, "rb") as fh:
                    content = fh.read()
                content_hash = hashlib.sha256(content).hexdigest()
                
                # 添加文件元数据到列表
                files_meta.append({
                    "path": rel,
                    "size": size,
                    "language": lang,
                    "content_hash": content_hash,
                })
            except Exception:
                # 处理单个文件出错时，跳过该文件继续扫描
                continue
    
    return files_meta


def upsert_repo_commit_and_files(repo_url: str, commit_sha: str, files: List[Dict]):
    """将仓库、提交和文件数据持久化到数据库
    
    执行upsert操作：如果记录存在则更新，否则插入。
    
    Args:
        repo_url: Git仓库URL
        commit_sha: 提交SHA
        files: 文件元数据列表
    """
    db = SessionLocal()  # 创建数据库会话
    try:
        # 处理仓库记录（upsert）
        repo_record = db.query(RepoModel).filter(RepoModel.remote_url == repo_url).first()
        if not repo_record:
            # 从URL中提取owner和name
            owner = None
            name = None
            try:
                parts = repo_url.rstrip('.git').split('/')  # 去除.git后缀并分割URL
                name = parts[-1]  # 最后一部分作为仓库名
                owner = parts[-2]  # 倒数第二部分作为所有者
            except Exception:
                pass  # 提取失败时保持None
            
            # 构建完整仓库名
            full_name = f"{owner}/{name}" if owner and name else repo_url
            
            # 创建新仓库记录
            repo_record = RepoModel(
                owner=owner, 
                name=name, 
                full_name=full_name, 
                remote_url=repo_url
            )
            db.add(repo_record)
            db.commit()
            db.refresh(repo_record)  # 刷新获取新生成的ID
        
        # 处理提交记录（插入，如不存在）
        commit_record = db.query(CommitModel).filter(
            CommitModel.repo_id == repo_record.id, 
            CommitModel.commit_sha == commit_sha
        ).first()
        if not commit_record:
            # 创建新提交记录
            commit_record = CommitModel(
                repo_id=repo_record.id, 
                commit_sha=commit_sha
            )
            db.add(commit_record)
            db.commit()
            db.refresh(commit_record)  # 刷新获取新生成的ID
        
        # 处理文件记录（upsert）
        for f in files:
            file_record = db.query(FileModel).filter(
                FileModel.repo_id == repo_record.id, 
                FileModel.commit_id == commit_record.id, 
                FileModel.path == f['path']
            ).first()
            
            if not file_record:
                # 创建新文件记录
                file_record = FileModel(
                    repo_id=repo_record.id, 
                    commit_id=commit_record.id, 
                    path=f['path'], 
                    size=f.get('size'), 
                    language=f.get('language'), 
                    content_hash=f.get('content_hash')
                )
                db.add(file_record)
            else:
                # 更新现有文件记录
                file_record.content_hash = f.get('content_hash')  # 更新内容哈希
                file_record.size = f.get('size')  # 更新文件大小
                file_record.language = f.get('language')  # 更新编程语言
        
        db.commit()  # 提交所有更改
    finally:
        db.close()  # 关闭数据库会话


def clone_and_scan(repo_url: str, branch: str = None, depth: int = 1, prev_commit: str = None) -> Dict:
    """克隆仓库并扫描文件的完整流程
    
    该函数是主要的入口函数，完成从克隆仓库到持久化数据的整个流程。
    
    Args:
        repo_url: Git仓库URL
        branch: 要检出的分支，默认为None
        depth: 克隆深度，默认为1
        prev_commit: 上次提交的SHA，用于增量扫描，None表示全量扫描
        
    Returns:
        包含repo_path、commit、file_count、files字段的字典
    """
    # 确保本地仓库存在（克隆或更新）
    info = ensure_local_repo(repo_url, branch=branch, depth=depth)
    repo_path = info['path']  # 本地仓库路径
    commit = info['commit']  # 当前提交SHA
    
    files = []  # 存储扫描到的文件元数据
    
    if prev_commit and prev_commit != commit:
        # 增量扫描：只处理变更的文件
        changed = list_changed_files(repo_path, prev_commit, commit)  # 获取变更文件列表
        files = scan_files(repo_path, changed)  # 扫描变更文件
    else:
        # 全量扫描：处理所有文件
        files = scan_files(repo_path)  # 扫描所有文件
    
    # 持久化数据到数据库
    upsert_repo_commit_and_files(repo_url, commit, files)
    
    # 返回结果信息
    return {
        "repo_path": repo_path,  # 本地仓库路径
        "commit": commit,  # 当前提交SHA
        "file_count": len(files),  # 扫描到的文件数量
        "files": files  # 文件元数据列表
    }