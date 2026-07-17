"""Deep Research向けGitHub公開リポジトリ構造アダプター。"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import quote

import httpx

GITHUB_REPO_RE = re.compile(
    r"https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.IGNORECASE,
)
TEXT_EXTENSIONS = {
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".rs", ".go", ".java",
    ".kt", ".rb", ".php", ".c", ".h", ".cpp", ".hpp", ".cs", ".swift", ".scala", ".sh",
    ".md", ".rst", ".txt", ".toml", ".yaml", ".yml", ".json", ".xml", ".ini", ".cfg",
}
PRIORITY_NAMES = {
    "readme.md": 100, "pyproject.toml": 95, "package.json": 95, "cargo.toml": 95,
    "go.mod": 95, "pom.xml": 92, "build.gradle": 92, "dockerfile": 90,
    "docker-compose.yml": 88, "compose.yml": 88, "makefile": 85, "justfile": 82,
    "requirements.txt": 85, "architecture.md": 90, "contributing.md": 65,
}


def extract_repositories(texts: list[str], limit: int = 3) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for text in texts:
        for match in GITHUB_REPO_RE.finditer(text or ""):
            owner = match.group(1)
            repo = match.group(2).removesuffix(".git")
            key = (owner.casefold(), repo.casefold())
            if key in seen:
                continue
            seen.add(key)
            result.append((owner, repo))
            if len(result) >= limit:
                return result
    return result


def _query_tokens(query: str) -> set[str]:
    return {token.casefold() for token in re.findall(r"[A-Za-z0-9_./-]{3,}", query)}


def _file_score(path: str, size: int, tokens: set[str]) -> int:
    lower = path.casefold()
    name = lower.rsplit("/", 1)[-1]
    suffix = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    if suffix not in TEXT_EXTENSIONS and name not in PRIORITY_NAMES:
        return -1
    if size <= 0 or size > 120_000:
        return -1
    score = PRIORITY_NAMES.get(name, 0)
    if lower.startswith(("src/", "app/", "lib/", "backend/", "frontend/src/")):
        score += 35
    if lower.startswith(("test/", "tests/", "spec/", "e2e/")) or "/test" in lower:
        score += 30
    if lower.startswith(".github/workflows/"):
        score += 35
    if any(part in name for part in ("main", "index", "server", "app", "router", "engine", "core")):
        score += 20
    score += sum(15 for token in tokens if token in lower)
    score += max(0, 12 - lower.count("/"))
    return score


def _select_files(tree: list[dict], query: str, max_files: int) -> list[dict]:
    tokens = _query_tokens(query)
    candidates: list[tuple[int, dict]] = []
    for entry in tree:
        if entry.get("type") != "blob":
            continue
        path = str(entry.get("path") or "")
        score = _file_score(path, int(entry.get("size") or 0), tokens)
        if score >= 0:
            candidates.append((score, entry))
    candidates.sort(key=lambda item: (-item[0], str(item[1].get("path") or "")))

    # README/manifest/source/test/CIの各観点を落としにくくし、残りをscore順で埋める。
    selected: list[dict] = []
    buckets = (
        lambda p: p.rsplit("/", 1)[-1] in PRIORITY_NAMES,
        lambda p: p.startswith(("src/", "app/", "lib/", "backend/", "frontend/src/")),
        lambda p: p.startswith(("test/", "tests/", "spec/", "e2e/")) or "/test" in p,
        lambda p: p.startswith(".github/workflows/"),
    )
    for predicate in buckets:
        item = next((entry for _, entry in candidates if predicate(str(entry.get("path") or "").casefold())), None)
        if item is not None and item not in selected:
            selected.append(item)
    for _, entry in candidates:
        if entry not in selected:
            selected.append(entry)
        if len(selected) >= max_files:
            break
    return selected[:max_files]


async def inspect_repository(
    owner: str, repo: str, query: str, *, max_files: int = 12,
) -> dict:
    """公開repositoryのmetadata/tree/主要ファイルを有限回のread requestで収集する。"""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ControlDeck-DeepResearch/1.0",
    }
    api = f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}"
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        metadata_response = await client.get(api)
        if metadata_response.status_code >= 400:
            return {"sources": [], "errors": [f"GitHub {owner}/{repo}: metadata HTTP {metadata_response.status_code}"]}
        metadata = metadata_response.json()
        branch = str(metadata.get("default_branch") or "main")
        tree_response = await client.get(f"{api}/git/trees/{quote(branch, safe='')}", params={"recursive": "1"})
        if tree_response.status_code >= 400:
            return {"sources": [], "errors": [f"GitHub {owner}/{repo}: tree HTTP {tree_response.status_code}"]}
        tree_payload = tree_response.json()
        tree = tree_payload.get("tree") if isinstance(tree_payload.get("tree"), list) else []
        truncated = bool(tree_payload.get("truncated"))
        selected = _select_files(tree, query, max_files)

        semaphore = asyncio.Semaphore(4)

        async def fetch_file(entry: dict) -> dict | None:
            path = str(entry.get("path") or "")
            raw_url = (
                f"https://raw.githubusercontent.com/{quote(owner)}/{quote(repo)}/"
                f"{quote(branch, safe='')}/{quote(path, safe='/')}"
            )
            async with semaphore:
                try:
                    response = await client.get(raw_url, headers={"User-Agent": headers["User-Agent"]})
                except httpx.HTTPError as exc:
                    errors.append(f"{path}: {type(exc).__name__}")
                    return None
            if response.status_code >= 400:
                errors.append(f"{path}: HTTP {response.status_code}")
                return None
            content = response.text[:12_000]
            return {
                "title": f"{owner}/{repo}: {path}",
                "url": f"https://github.com/{owner}/{repo}/blob/{branch}/{path}",
                "source": "GitHub code",
                "kind": "document",
                "snippet": content,
                "meta": {"repository": f"{owner}/{repo}", "path": path, "branch": branch},
            }

        fetched = await asyncio.gather(*(fetch_file(entry) for entry in selected))

    directories = sorted({str(entry.get("path") or "").split("/", 1)[0] for entry in tree if entry.get("path")})
    paths = [str(entry.get("path") or "") for entry in tree if entry.get("type") == "blob"]
    repo_url = str(metadata.get("html_url") or f"https://github.com/{owner}/{repo}")
    sources = [
        {
            "title": f"{owner}/{repo}: repository metadata",
            "url": repo_url,
            "source": "GitHub repository",
            "kind": "report",
            "snippet": (
                f"description: {metadata.get('description') or ''}\n"
                f"default_branch: {branch}\nlanguage: {metadata.get('language') or ''}\n"
                f"stars: {metadata.get('stargazers_count', 0)}\nforks: {metadata.get('forks_count', 0)}\n"
                f"open_issues: {metadata.get('open_issues_count', 0)}\nupdated_at: {metadata.get('updated_at') or ''}"
            ),
        },
        {
            "title": f"{owner}/{repo}: repository structure",
            "url": repo_url,
            "source": "GitHub tree",
            "kind": "report",
            "snippet": (
                f"entries: {len(tree)}; files: {len(paths)}; truncated: {str(truncated).lower()}\n"
                f"top-level: {', '.join(directories[:80])}\nselected files:\n" + "\n".join(paths[:400])
            )[:12_000],
        },
    ]
    fetched_sources = [item for item in fetched if item is not None]
    sources.extend(fetched_sources)
    if fetched_sources:
        from app.workflows.code_structure import repository_structure_summary

        sources.append({
            "title": f"{owner}/{repo}: static symbol and integration index",
            "url": repo_url,
            "source": "GitHub static analysis",
            "kind": "report",
            "snippet": repository_structure_summary(fetched_sources),
        })
    if truncated:
        errors.append("recursive treeがGitHub上限でtruncatedになりました")
    return {
        "sources": sources,
        "errors": errors,
        "repository": f"{owner}/{repo}",
        "tree_entries": len(tree),
        "files_selected": len(fetched_sources),
        "truncated": truncated,
    }
