"""Project Lab API。成果物閲覧とbrowser非依存のdurable runを提供する。"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.database import get_db
from app.models import ProjectRun, User
from app.project_lab import runs, service
from app.schemas.project_lab import ProjectFileRunCreate, ProjectLabSettingsBody, ProjectRunCreate
from app.security.deps import require_permission

router = APIRouter(prefix="/project-lab", tags=["project-lab"])

# 既定は一切実行させないCSP。artifactはControl Deckのoriginから配信されるため、
# 実行を許すHTMLだけCSPの`sandbox`で不透明originへ閉じ込め、上位tabで直接開かれても
# Control Deckのcookie／DOM／APIへ到達できないようにする（connect-srcも遮断）。
INERT_CSP = (
    "default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self'; "
    "media-src 'self'; form-action 'none'; base-uri 'none'"
)
SVG_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
# 明示操作時だけ外部CDN等の読み込みを許可する。sandboxは維持したままなので、
# 読み込まれたコードもControl Deckのcookie／DOM／APIへは到達できない。
HTML_EXTERNAL_CSP = (
    "sandbox allow-scripts allow-modals allow-forms allow-popups allow-downloads; "
    "default-src 'self' https: data: blob:; script-src 'self' https: 'unsafe-inline' 'unsafe-eval' blob:; "
    "style-src 'self' https: 'unsafe-inline' data:; img-src 'self' https: data: blob:; "
    "font-src 'self' https: data:; media-src 'self' https: data: blob:; connect-src https:; "
    "form-action 'none'; base-uri 'none'; frame-ancestors 'self'"
)
HTML_CSP = (
    "sandbox allow-scripts allow-modals allow-forms allow-popups allow-downloads; "
    "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
    "style-src 'self' 'unsafe-inline' data:; img-src 'self' data: blob:; font-src 'self' data:; "
    "media-src 'self' data: blob:; connect-src 'none'; form-action 'none'; base-uri 'none'; "
    "frame-ancestors 'self'"
)


# sandbox iframeは不透明originのため、localStorage/sessionStorage/cookieへ触れると
# SecurityErrorになりscript全体が止まる（描画は残るが操作できない）。allow-same-originを
# 与えるとControl Deckのoriginを渡すことになり危険なので、preview配信時だけ
# メモリ上の互換実装を先に差し込む。ダウンロードや実体のfileは書き換えない。
STORAGE_SHIM = """<script>/* Control Deck preview shim */
(function () {
  function fallback() {
    var store = {};
    return {
      getItem: function (key) { return Object.prototype.hasOwnProperty.call(store, String(key)) ? store[String(key)] : null; },
      setItem: function (key, value) { store[String(key)] = String(value); },
      removeItem: function (key) { delete store[String(key)]; },
      clear: function () { store = {}; },
      key: function (index) { var keys = Object.keys(store); return index < keys.length ? keys[index] : null; },
      get length() { return Object.keys(store).length; }
    };
  }
  ["localStorage", "sessionStorage"].forEach(function (name) {
    try { window[name].getItem("__controldeck_probe__"); return; } catch (error) { /* 遮断されている */ }
    try { Object.defineProperty(window, name, { value: fallback(), configurable: true }); } catch (error) { /* 諦める */ }
  });
  try { document.cookie; } catch (error) {
    var jar = "";
    try {
      Object.defineProperty(document, "cookie", {
        configurable: true,
        get: function () { return jar; },
        set: function (value) { jar = String(value).split(";")[0]; }
      });
    } catch (inner) { /* 諦める */ }
  }
})();
</script>
"""
MAX_SHIM_BYTES = 4 * 1024 * 1024
_HEAD_OPEN = re.compile(r"<head[^>]*>", re.I)
_CHARSET_META = re.compile(r"<meta[^>]*charset[^>]*>", re.I)


def _with_storage_shim(path: Path) -> str | None:
    """HTMLのpreview本文へ互換shimを差し込む（charset宣言の直後、最初のscriptより前）。"""
    try:
        if path.stat().st_size > MAX_SHIM_BYTES:
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    charset = _CHARSET_META.search(text[:4096])
    if charset:
        index = charset.end()
    else:
        head = _HEAD_OPEN.search(text[:4096])
        index = head.end() if head else 0
    return text[:index] + "\n" + STORAGE_SHIM + text[index:]


def _not_found(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


@router.get("/settings")
def project_lab_settings(user: User = Depends(require_permission("project_lab.view"))):
    # 置き場は設定で変わるので、UIの案内文がパスを埋め込まずに済むよう一緒に返す。
    return {**service.get_settings(), "project_root": str(service.project_root())}


@router.put("/settings")
def update_project_lab_settings(
    body: ProjectLabSettingsBody, request: Request,
    user: User = Depends(require_permission("project_lab.run")), db: Session = Depends(get_db),
):
    """外部CDNを常に許可するかを切り替える。sandboxは常に維持する。"""
    settings = service.save_settings(body.model_dump(exclude_none=True))
    audit.record(
        db, "project_lab.settings", user=user, resource_type="project_lab", resource_id="settings",
        request=request, metadata={"allow_external_preview": settings["allow_external_preview"]},
    )
    return settings


@router.get("/projects")
def projects(user: User = Depends(require_permission("project_lab.view"))):
    return service.list_projects()


@router.get("/projects/{project_id}")
def project(project_id: str, user: User = Depends(require_permission("project_lab.view"))):
    try:
        return service.project_detail(project_id)
    except service.ProjectLabError as exc:
        raise _not_found(exc) from exc


# プレビュー用の短命token。sandboxのiframeは不透明originになるため、そこから出る
# サブリソース要求はcross-site扱いになりセッションcookieが送られない（Chromeは送らず、
# WebKitは送るのでブラウザによって動いたり動かなかったりする）。tokenをパスへ入れると
# 相対参照にもそのまま引き継がれるので、cookieに依存せず配下のファイルを配信できる。
PREVIEW_TOKEN_PREFIX = "project-lab-preview:"
# preview の配信 URL。CSP の source に書くので、router の prefix と必ず揃える。
PREVIEW_PATH_PREFIX = "/api/v1/project-lab/preview/"
PREVIEW_TOKEN_TTL_SECONDS = 900


def issue_preview_token(project_id: str) -> str:
    from app.security.crypto import encrypt_text

    return encrypt_text(PREVIEW_TOKEN_PREFIX + project_id)


def resolve_preview_token(token: str) -> str:
    """tokenからproject_idを取り出す。期限切れ・改竄は404で潰す（存在を推測させない）。"""
    from app.security.crypto import decrypt_text

    try:
        plain = decrypt_text(token, ttl_seconds=PREVIEW_TOKEN_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001 - 失敗理由は問わない
        raise HTTPException(status_code=404, detail="previewの有効期限が切れました") from exc
    if not plain.startswith(PREVIEW_TOKEN_PREFIX):
        raise HTTPException(status_code=404, detail="previewの有効期限が切れました")
    return plain[len(PREVIEW_TOKEN_PREFIX):]


def _own_assets_source(request: Request | None, token: str | None) -> str:
    """自分のプロジェクトの配信経路だけを指す CSP source。

    生成されたコードは、自分が作った画像や音声を読めなければ動かない。一方で
    `'self'` を許すと Control Deck の API 全体が同じ origin にあるので、そちらへも
    要求を出せてしまう。sandbox の中でも cookie は宛先 origin のものが付くため、
    利用者として API を叩けることになる。

    CSP の source は path まで書けるので、その project の preview 経路だけに絞る。
    token は project 単位・短命で、配信できるのはその配下の artifact だけである。
    """
    if not token or request is None:
        return ""
    # CSP の source は host を省けないので、いま応答している URL から組み立てる。
    # http でも https でも、Tailscale 越しでも、その経路そのものを指す。
    return f"{request.url.scheme}://{request.url.netloc}{PREVIEW_PATH_PREFIX}{token}/"


def _artifact_response(
    project_id: str, artifact_path: str, *, download: bool, external: bool,
    request: Request | None = None, preview_token: str | None = None,
):
    try:
        project_path = service.resolve_project(project_id)
        path = service.resolve_artifact(project_path, artifact_path)
    except service.ProjectLabError as exc:
        raise _not_found(exc) from exc
    kind = service.ARTIFACT_KINDS.get(path.suffix.lower(), "resource")
    policy = INERT_CSP
    if kind == "html" and not download:
        allow_external = external or service.get_settings()["allow_external_preview"]
        policy = HTML_EXTERNAL_CSP if allow_external else HTML_CSP
        own = _own_assets_source(request, preview_token)
        if own:
            # 自分のアセットを XHR / fetch で読めるようにする。THREE の AudioLoader も
            # canvas へ描くための読み込みも、ここを通らないと何も鳴らず何も映らない。
            policy = policy.replace("connect-src 'none'", f"connect-src {own}")
            policy = policy.replace("connect-src https:", f"connect-src {own} https:")
    elif path.suffix.lower() == ".svg":
        policy = SVG_CSP
    headers = {"Content-Security-Policy": policy, "X-Content-Type-Options": "nosniff"}
    if preview_token is not None:
        # sandbox の中は不透明 origin なので、要求は Origin: null で出る。crossOrigin を
        # 付けて読む loader（THREE.TextureLoader の既定）はこれが無いと弾かれる。
        # 資格情報は許さないので、読めるのは token を持っている者だけである。
        headers["Access-Control-Allow-Origin"] = "*"
        headers["Cross-Origin-Resource-Policy"] = "cross-origin"
    disposition = "attachment" if download else "inline"
    if kind == "html" and not download:
        patched = _with_storage_shim(path)
        if patched is not None:
            return HTMLResponse(patched, headers={**headers, "Cache-Control": "no-store"})
    return FileResponse(
        path, media_type=service.media_type(path),
        filename=path.name if download else None,
        content_disposition_type=disposition, headers=headers,
    )


@router.get("/projects/{project_id}/artifacts/{artifact_path:path}")
def artifact(
    project_id: str, artifact_path: str, download: bool = Query(False),
    external: bool = Query(False, description="外部CDN等の読み込みを明示的に許可する"),
    user: User = Depends(require_permission("project_lab.view")),
):
    return _artifact_response(project_id, artifact_path, download=download, external=external)


@router.post("/projects/{project_id}/preview-token")
def preview_token(project_id: str, user: User = Depends(require_permission("project_lab.view"))):
    """iframeプレビュー用の短命tokenを発行する。"""
    try:
        service.resolve_project(project_id)
    except service.ProjectLabError as exc:
        raise _not_found(exc) from exc
    return {"token": issue_preview_token(project_id), "expires_in": PREVIEW_TOKEN_TTL_SECONDS}


@router.get("/preview/{token}/{artifact_path:path}")
def preview(
    token: str, artifact_path: str, request: Request,
    external: bool = Query(False, description="外部CDN等の読み込みを明示的に許可する"),
):
    """token付きのpreview配信。相対参照のサブリソースもこの経路で解決される。

    cookieを使わないので、sandboxの不透明originからでも読める。tokenはプロジェクト
    単位・短命で、配信できるのはそのプロジェクト配下のartifactだけ（ダウンロードは不可）。
    """
    return _artifact_response(resolve_preview_token(token), artifact_path,
                              download=False, external=external,
                              request=request, preview_token=token)


@router.get("/projects/{project_id}/previews/{artifact_path:path}")
def artifact_preview(
    project_id: str, artifact_path: str,
    user: User = Depends(require_permission("project_lab.view")),
):
    try:
        project_path = service.resolve_project(project_id)
        path = service.resolve_artifact(project_path, artifact_path)
    except service.ProjectLabError as exc:
        raise _not_found(exc) from exc
    metadata = service.artifact_info(project_path, path, include_preview=True)
    if metadata is None:
        raise HTTPException(status_code=404, detail="artifact previewを生成できません")
    return {"path": metadata["path"], "previewText": metadata["previewText"], "structuredPreview": metadata["structuredPreview"]}


def _run_or_404(db: Session, run_id: int) -> ProjectRun:
    row = db.get(ProjectRun, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Project runが見つかりません")
    return row


@router.post("/projects/{project_id}/runs", status_code=201)
def start_project_run(
    project_id: str, body: ProjectRunCreate, request: Request,
    user: User = Depends(require_permission("project_lab.run")), db: Session = Depends(get_db),
):
    try:
        row = runs.start_run(
            db, project_id=project_id, profile_id=body.profile_id,
            timeout_seconds=body.timeout_seconds, created_by=user.id,
        )
    except service.ProjectLabError as exc:
        raise _not_found(exc) from exc
    except runs.ProjectRunError as exc:
        audit.record(
            db, "project_lab.run.start", user=user, resource_type="project",
            resource_id=project_id, request=request, result="failure",
            metadata={"profile_id": body.profile_id},
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit.record(
        db, "project_lab.run.start", user=user, resource_type="project_run",
        resource_id=str(row.id), request=request,
        metadata={"project_id": project_id, "profile_id": body.profile_id},
    )
    return runs.run_out(db, row)


@router.post("/projects/{project_id}/file-runs", status_code=201)
def start_project_file_run(
    project_id: str, body: ProjectFileRunCreate, request: Request,
    user: User = Depends(require_permission("project_lab.run")), db: Session = Depends(get_db),
):
    """成果物のPython／JavaScript fileを、profile定義なしで1本だけ隔離実行する。"""
    try:
        row = runs.start_file_run(
            db, project_id=project_id, artifact_path=body.path,
            timeout_seconds=body.timeout_seconds, created_by=user.id,
        )
    except service.ProjectLabError as exc:
        raise _not_found(exc) from exc
    except runs.ProjectRunError as exc:
        audit.record(
            db, "project_lab.run.file", user=user, resource_type="project",
            resource_id=project_id, request=request, result="failure", metadata={"path": body.path},
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit.record(
        db, "project_lab.run.file", user=user, resource_type="project_run",
        resource_id=str(row.id), request=request, metadata={"project_id": project_id, "path": body.path},
    )
    return runs.run_out(db, row)


@router.get("/runs")
def project_runs(
    project_id: str | None = Query(None, max_length=128), limit: int = Query(30, ge=1, le=100),
    user: User = Depends(require_permission("project_lab.view")), db: Session = Depends(get_db),
):
    query = select(ProjectRun)
    if project_id:
        query = query.where(ProjectRun.project_id == project_id)
    rows = db.execute(query.order_by(ProjectRun.id.desc()).limit(limit)).scalars().all()
    return [runs.run_out(db, row) for row in rows]


@router.get("/runs/{run_id}")
def project_run(
    run_id: int, user: User = Depends(require_permission("project_lab.view")),
    db: Session = Depends(get_db),
):
    return runs.run_out(db, _run_or_404(db, run_id))


@router.get("/runs/{run_id}/logs")
def project_run_logs(
    run_id: int, user: User = Depends(require_permission("project_lab.view")),
    db: Session = Depends(get_db),
):
    row = _run_or_404(db, run_id)
    runs.refresh_run(db, row)
    return {"runId": row.id, "logs": runs.run_logs(row)}


@router.post("/runs/{run_id}/cancel")
def cancel_project_run(
    run_id: int, request: Request, user: User = Depends(require_permission("project_lab.run")),
    db: Session = Depends(get_db),
):
    row = _run_or_404(db, run_id)
    try:
        runs.cancel_run(db, row)
    except runs.ProjectRunError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit.record(
        db, "project_lab.run.cancel", user=user, resource_type="project_run",
        resource_id=str(row.id), request=request,
    )
    return runs.run_out(db, row)
