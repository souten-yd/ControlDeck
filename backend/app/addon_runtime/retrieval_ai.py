"""Bounded Host-managed local embedding/reranking for ai.inference principals."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import math
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.addon_runtime.ai import AIAuth
from app.addon_runtime.service import audit_runtime

router = APIRouter(prefix="/{addon_id}/ai/retrieval")
Text = Annotated[str, Field(strict=True, min_length=1, max_length=8192)]


class RetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability: Literal["text.embed", "text.rerank"]
    inputs: list[Text] = Field(min_length=1, max_length=32)
    query: Text | None = None

    @model_validator(mode="after")
    def bounds(self) -> "RetrievalRequest":
        if (self.capability == "text.rerank") != (self.query is not None):
            raise ValueError("query is required only for reranking")
        if sum(len(s.encode("utf-8")) for s in self.inputs) + len((self.query or "").encode("utf-8")) > 65536:
            raise ValueError("retrieval input exceeds 64KiB")
        if any(not s.strip() for s in self.inputs) or (self.query is not None and not self.query.strip()):
            raise ValueError("empty retrieval text")
        return self


def resolve_target(capability: str, alias: str | None = None) -> dict[str, str]:
    from app.models_mgmt import llama

    role = "embedding" if capability == "text.embed" else "reranker"
    candidates = [i for i in llama.list_instances() if i.get("role") == role and (alias is None or i.get("alias") == alias)]
    candidates.sort(key=lambda i: (not bool(i.get("loaded")), str(i.get("alias") or "")))
    if not candidates:
        raise ValueError("local retrieval model unavailable")
    item = candidates[0]
    base = str(item.get("base_url") or "").rstrip("/")
    url = urlsplit(base)
    if (url.scheme != "http" or not ipaddress.ip_address(url.hostname or "").is_loopback
            or not url.port or url.username or url.password or url.query or url.fragment or url.path not in ("", "/v1")):
        raise ValueError("retrieval endpoint must be loopback")
    model = Path(str(item.get("model_path") or "")).resolve()
    if model.suffix.lower() != ".gguf" or not model.is_file():
        raise ValueError("local model file unavailable")
    stat = model.stat()
    identity = [str(model), stat.st_size, stat.st_mtime_ns, stat.st_ino, role,
                item.get("pooling"), item.get("ctx_size"), item.get("backend")]
    ref = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return {"alias": str(item["alias"]), "base": base.removesuffix('/v1'), "model_ref": ref}


async def availability(capability: str) -> dict[str, Any]:
    try:
        target = await asyncio.to_thread(resolve_target, capability)
    except (ValueError, OSError):
        return {"available": False, "execution_policy": "local_only"}
    return {"available": True, "execution_policy": "local_only", "model_ref": target["model_ref"],
            "revision_kind": "host_file_metadata_v1"}


@router.get("/capabilities")
async def capabilities(principal: AIAuth) -> dict[str, Any]:
    del principal
    values = await asyncio.gather(availability("text.embed"), availability("text.rerank"))
    return dict(zip(("text.embed", "text.rerank"), values))


def validate_response(value: Any, capability: str, count: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("invalid retrieval response")
    if capability == "text.embed":
        rows = value.get("data")
        if not isinstance(rows, list) or len(rows) != count:
            raise ValueError("embedding count mismatch")
        vectors: dict[int, list[float]] = {}
        dimensions = 0
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("invalid embedding row")
            index, vector = row.get("index"), row.get("embedding")
            if type(index) is not int or not 0 <= index < count or index in vectors:
                raise ValueError("invalid embedding index")
            if not isinstance(vector, list) or not 1 <= len(vector) <= 8192:
                raise ValueError("invalid embedding dimension")
            if any(type(x) not in (int, float) or not math.isfinite(x) for x in vector):
                raise ValueError("invalid embedding values")
            if dimensions and dimensions != len(vector):
                raise ValueError("mixed embedding dimensions")
            if not any(vector):
                raise ValueError("zero embedding")
            dimensions = len(vector)
            vectors[index] = vector
        return {"vectors": [vectors[i] for i in range(count)], "dimensions": dimensions}
    rows = value.get("results")
    if not isinstance(rows, list) or len(rows) != count:
        raise ValueError("rerank count mismatch")
    results, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid rerank row")
        index, score = row.get("index"), row.get("relevance_score")
        if type(index) is not int or not 0 <= index < count or index in seen:
            raise ValueError("invalid rerank index")
        if type(score) not in (int, float) or not math.isfinite(score):
            raise ValueError("invalid rerank score")
        seen.add(index); results.append({"index": index, "score": score})
    return {"rankings": sorted(results, key=lambda r: (-r["score"], r["index"]))}


async def fetch_result(target: dict[str, str], body: RetrievalRequest) -> dict[str, Any]:
    if body.capability == "text.embed":
        path = "/v1/embeddings"
        payload = {"model": target["alias"], "input": body.inputs, "encoding_format": "float"}
    else:
        path = "/rerank"
        payload = {"model": target["alias"], "query": body.query, "documents": body.inputs, "top_n": len(body.inputs)}
    async with httpx.AsyncClient(timeout=120, trust_env=False, follow_redirects=False) as client:
        async with client.stream("POST", target["base"] + path, json=payload) as response:
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 4 * 1024 * 1024:
                    raise ValueError("retrieval response exceeds limit")
    return validate_response(json.loads(content), body.capability, len(body.inputs))


async def execute(body: RetrievalRequest, request: Request) -> dict[str, Any]:
    from app.models_mgmt import gateway, llama

    target = await asyncio.to_thread(resolve_target, body.capability)
    adapter, lease_id, renew = None, "", None
    try:
        adapter, lease_id, renew = await gateway._acquire_gateway_lease(target["alias"], request)
        if not await llama.ensure_ready(target["alias"], timeout_seconds=180):
            raise ValueError("retrieval model not ready")
        current = await asyncio.to_thread(resolve_target, body.capability, target["alias"])
        if current != target:
            raise ValueError("model configuration changed during admission")
        result = await fetch_result(target, body)
        current = await asyncio.to_thread(resolve_target, body.capability, target["alias"])
        if current != target:
            raise ValueError("model configuration changed during inference")
        return dict(result, execution_policy="local_only", model_ref=target["model_ref"],
                    revision_kind="host_file_metadata_v1")
    finally:
        if adapter is not None and lease_id and renew is not None:
            await gateway._release_gateway_lease(adapter, lease_id, renew)


@router.post("/execute")
async def run(body: RetrievalRequest, request: Request, principal: AIAuth) -> dict[str, Any]:
    task = asyncio.create_task(execute(body, request))
    outcome = "error"
    try:
        async with asyncio.timeout(310):
            while not task.done():
                await asyncio.wait({task}, timeout=0.25)
                if await request.is_disconnected():
                    outcome = "client_disconnected"
                    raise HTTPException(status_code=499, detail="request disconnected")
            value = await task
            outcome = "success"
            return value
    except (ValueError, OSError, httpx.HTTPError, TimeoutError) as exc:
        raise HTTPException(status_code=503, detail="ローカル検索AIを利用できません。モデルと入力を確認してください") from exc
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        audit_runtime(request, principal, "addon.runtime.ai.retrieval", "ai_gateway", body.capability,
                      {"capability": body.capability, "count": len(body.inputs), "result": outcome})
