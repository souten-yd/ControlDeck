from __future__ import annotations

import json
import re
from typing import Any

from app.application_builder.csharp_generator_runtime import render_workflow_source
from app.application_builder.aspnet_entity_generator import entity_base_path, render_entity_source
from app.application_builder.aspnet_ui_generator import (
    render_app_component, render_page_component, render_ui_css, render_ui_javascript,
    ui_generator_diagnostics,
)
from app.application_builder.compiler import _api_schema_issues
from app.application_builder.diagnostics import Diagnostic, diagnostic
from app.application_builder.source_generator import (
    SourceBundle,
    SourceGenerationError,
    _canonical_json,
    _csharp_identifier,
    _csharp_string,
    _deterministic_zip,
    _sha256,
    _source_checksum,
    _topological_nodes,
    framework_generator_diagnostics,
)

ASPNET_GENERATOR_ID = "controldeck.aspnet-api"
ASPNET_GENERATOR_VERSION = "1.0.0"
MAX_API_ENDPOINTS = 100
MAX_BACKGROUND_JOBS = 100


def aspnet_generator_diagnostics(
    spec: dict[str, Any], workflow_ir: dict[str, Any] | None, *, target_id: str,
) -> list[Diagnostic]:
    issues = framework_generator_diagnostics(
        spec, workflow_ir, target_id=target_id, framework="aspnet-blazor",
        allowed_platforms={"web", "linux", "windows"}, generator_label="ASP.NET Core API",
    )
    app = spec.get("application") if isinstance(spec.get("application"), dict) else {}
    authentication = str(app.get("authentication") or "local")
    if authentication not in {"none", "api-key"}:
        issues.append(diagnostic(
            "GENERATOR_AUTH_ADAPTER_UNAVAILABLE", "error",
            f"authentication '{authentication}' のstandalone adapterは未実装です",
            path="application.authentication", source="source-generator",
            suggested_fix="api-keyまたはnoneを明示してください",
        ))
    issues.extend(ui_generator_diagnostics(spec))
    entities = [item for item in spec.get("entities") or [] if isinstance(item, dict)]
    pages = [item for item in spec.get("pages") or [] if isinstance(item, dict)]
    endpoints = [item for item in spec.get("apiEndpoints") or [] if isinstance(item, dict)]
    background_jobs = [item for item in spec.get("backgroundJobs") or [] if isinstance(item, dict)]
    workflow_side_effects = (workflow_ir or {}).get("sideEffects") or (workflow_ir or {}).get("side_effects") or []
    required_secrets = (workflow_ir or {}).get("requiredSecrets") or (workflow_ir or {}).get("required_secrets") or []
    if (workflow_side_effects or required_secrets) and authentication != "api-key":
        issues.append(diagnostic(
            "GENERATOR_SIDE_EFFECT_AUTH_REQUIRED", "error",
            "Secretまたはside-effectを含むASP.NET生成にはapi-key認証が必要です",
            path="application.authentication", source="source-generator",
        ))
    if workflow_side_effects or required_secrets:
        for index, endpoint in enumerate(endpoints):
            if endpoint.get("authentication") == "anonymous":
                issues.append(diagnostic(
                    "GENERATOR_SIDE_EFFECT_ANONYMOUS_FORBIDDEN", "error",
                    "Secretまたはside-effect Workflowをanonymous endpointへ公開できません",
                    path=f"apiEndpoints.{index}.authentication", source="source-generator",
                ))
    if len(endpoints) > MAX_API_ENDPOINTS:
        issues.append(diagnostic(
            "GENERATOR_API_LIMIT_EXCEEDED", "error", "生成対象API endpointは100件以下にしてください",
            path="apiEndpoints", source="source-generator",
        ))
    if len(background_jobs) > MAX_BACKGROUND_JOBS:
        issues.append(diagnostic(
            "GENERATOR_BACKGROUND_JOB_LIMIT_EXCEEDED", "error", "生成対象background jobは100件以下にしてください",
            path="backgroundJobs", source="source-generator",
        ))
    workflow_id = (workflow_ir or {}).get("workflowId") or (workflow_ir or {}).get("workflow_id")
    has_async = any(str(item.get("mode") or "sync") == "async" for item in endpoints)
    reserved = {"/healthz", "/openapi.json"}
    if has_async:
        reserved |= {"/api/jobs/{jobId}", "/api/jobs/{jobId}/events"}
    if background_jobs:
        reserved |= {"/api/background-jobs", "/api/background-jobs/{definitionId}/run"}
    built_in_reserved = set(reserved)
    for entity in entities:
        crud = entity.get("crud") if isinstance(entity.get("crud"), dict) else {}
        if crud.get("enabled"):
            base_path = entity_base_path(entity)
            if base_path in built_in_reserved or base_path + "/{id}" in built_in_reserved:
                issues.append(diagnostic(
                    "GENERATOR_ENTITY_ROUTE_RESERVED", "error", "generator管理routeとEntity CRUDが重複しています",
                    path=f"entities.{entity.get('id')}.crud.basePath", source="source-generator",
                ))
            reserved |= {base_path, base_path + "/{id}"}
    normalized_reserved = {re.sub(r"\{[A-Za-z][A-Za-z0-9_]*\}", "{}", item) for item in reserved}
    for index, endpoint in enumerate(endpoints):
        normalized_path = re.sub(r"\{[A-Za-z][A-Za-z0-9_]*\}", "{}", str(endpoint.get("path") or ""))
        if normalized_path in normalized_reserved:
            issues.append(diagnostic(
                "GENERATOR_API_ROUTE_RESERVED", "error", "generator管理routeと重複しています",
                path=f"apiEndpoints.{index}.path", source="source-generator",
            ))
        if endpoint.get("workflowId") != workflow_id:
            issues.append(diagnostic(
                "GENERATOR_API_WORKFLOW_UNAVAILABLE", "error",
                f"APIが参照するWorkflow #{endpoint.get('workflowId')} のIR snapshotがありません",
                path=f"apiEndpoints.{index}.workflowId", source="source-generator",
            ))
        for key in ("requestSchema", "responseSchema"):
            issues.extend(_api_schema_issues(endpoint.get(key), f"apiEndpoints.{index}.{key}"))
    for index, job in enumerate(background_jobs):
        if job.get("workflowId") != workflow_id:
            issues.append(diagnostic(
                "GENERATOR_JOB_WORKFLOW_UNAVAILABLE", "error",
                f"background jobが参照するWorkflow #{job.get('workflowId')} のIR snapshotがありません",
                path=f"backgroundJobs.{index}.workflowId", source="source-generator",
            ))
    return issues


def generate_aspnet_api(
    spec: dict[str, Any], workflow_ir: dict[str, Any] | None, *, target_id: str,
) -> SourceBundle:
    issues = aspnet_generator_diagnostics(spec, workflow_ir, target_id=target_id)
    if any(item.severity == "error" for item in issues):
        raise SourceGenerationError(issues)
    app = spec.get("application") if isinstance(spec.get("application"), dict) else {}
    project_name = _csharp_identifier(str(app.get("name") or "GeneratedWebApplication"))
    namespace = project_name
    workflow_payload = workflow_ir or {
        "schemaVersion": 1, "name": "", "inputs": [], "outputs": [], "nodes": [], "edges": [],
        "requiredSecrets": [], "capabilities": [], "sideEffects": [], "diagnostics": [],
    }
    required_secrets = workflow_payload.get("requiredSecrets") or workflow_payload.get("required_secrets") or []
    spec_bytes = _canonical_json(spec)
    workflow_bytes = _canonical_json(workflow_payload)
    spec_checksum, workflow_checksum = _sha256(spec_bytes), _sha256(workflow_bytes)
    endpoints = [item for item in spec.get("apiEndpoints") or [] if isinstance(item, dict)]
    background_jobs = [item for item in spec.get("backgroundJobs") or [] if isinstance(item, dict)]
    entities = [item for item in spec.get("entities") or [] if isinstance(item, dict)]
    pages = [item for item in spec.get("pages") or [] if isinstance(item, dict)]
    authentication = str(app.get("authentication") or "none")
    openapi = _openapi_document(app, endpoints, background_jobs, entities, authentication)
    root = project_name
    files: dict[str, tuple[bytes, str]] = {
        f"{root}/README.md": (_readme(project_name, authentication, bool(entities), bool(pages), workflow_payload).encode(), "config"),
        f"{root}/Dockerfile": (_dockerfile(project_name).encode(), "config"),
        f"{root}/src/{project_name}/{project_name}.csproj": (_project_file(namespace, bool(entities)).encode(), "managed"),
        f"{root}/src/{project_name}/Program.cs": (_program(namespace, bool(background_jobs), bool(entities), bool(pages), authentication).encode(), "managed"),
        f"{root}/src/{project_name}/Generated/Application.generated.cs": (
            _application_source(namespace, app, spec_checksum, workflow_checksum).encode(), "managed",
        ),
        f"{root}/src/{project_name}/Generated/Api.generated.cs": (
            _api_source(namespace, endpoints, background_jobs, authentication, bool(pages), json.dumps(openapi, ensure_ascii=False, sort_keys=True, separators=(",", ":"))).encode(), "managed",
        ),
        f"{root}/src/{project_name}/Generated/BackgroundJobs.generated.cs": (
            _background_jobs_source(namespace, background_jobs, authentication).encode(), "managed",
        ),
        f"{root}/src/{project_name}/Generated/JsonSchema.generated.cs": (
            _json_schema_source(namespace).encode(), "managed",
        ),
        f"{root}/src/{project_name}/Generated/Workflow.generated.cs": (
            render_workflow_source(namespace, workflow_payload, _topological_nodes(workflow_payload), _csharp_string).encode(), "managed",
        ),
        f"{root}/src/{project_name}/Extensions/ApiExtensions.cs": (_extensions(namespace).encode(), "extension"),
        f"{root}/src/{project_name}/appsettings.json": (_appsettings(authentication).encode(), "config"),
        f"{root}/openapi.json": (_canonical_json(openapi), "config"),
        f"{root}/tests/{project_name}.GeneratedTests/{project_name}.GeneratedTests.csproj": (
            _test_project(project_name).encode(), "managed",
        ),
        f"{root}/tests/{project_name}.GeneratedTests/Program.cs": (_test_program(namespace).encode(), "managed"),
    }
    if entities:
        files[f"{root}/src/{project_name}/Generated/Entities.generated.cs"] = (
            render_entity_source(namespace, entities, authentication).encode(), "managed",
        )
    if pages:
        files[f"{root}/src/{project_name}/Components/_Imports.razor"] = (
            b"@using Microsoft.AspNetCore.Components\n@using Microsoft.AspNetCore.Components.Web\n@using Microsoft.AspNetCore.Components.Routing\n", "managed",
        )
        files[f"{root}/src/{project_name}/Components/App.razor"] = (
            render_app_component(
                namespace, app, pages, authentication,
                [item for item in spec.get("clientState") or [] if isinstance(item, dict)],
            ).encode(), "managed",
        )
        for index, page in enumerate(pages):
            class_name, page_source = render_page_component(namespace, page, index, entities, spec)
            files[f"{root}/src/{project_name}/Components/Pages/{class_name}.razor"] = (page_source.encode(), "managed")
        files[f"{root}/src/{project_name}/wwwroot/generated-ui.css"] = (render_ui_css().encode(), "managed")
        files[f"{root}/src/{project_name}/wwwroot/generated-ui.js"] = (render_ui_javascript().encode(), "managed")
    rows = tuple(
        {"path": path, "sha256": _sha256(content), "bytes": len(content), "kind": kind}
        for path, (content, kind) in sorted(files.items())
    )
    source_checksum = _source_checksum(files)
    manifest = {
        "schemaVersion": 1, "phase": "E7",
        "generator": {"id": ASPNET_GENERATOR_ID, "version": ASPNET_GENERATOR_VERSION},
        "input": {
            "specChecksum": spec_checksum, "workflowChecksum": workflow_checksum,
            "targetId": target_id, "framework": "aspnet-blazor",
        },
        "sourceChecksum": source_checksum, "files": list(rows),
        "runtime": {
            "jsonSchema": "dependency-free-supported-subset",
            "scheduleState": "atomic-file" if background_jobs else "none",
            "entityDatabase": "sqlite-wal-additive-migration" if entities else "none",
            "entityPackage": "Microsoft.Data.Sqlite/8.0.29" if entities else "none",
            "gui": "blazor-static-ssr" if pages else "none",
            "browserAuth": "ephemeral-http-only-api-key-session" if pages and authentication == "api-key" else "none",
            "workflowForms": "sync-json-schema-typed-result" if pages and any(
                _page_has_workflow_action(page) for page in pages
            ) else "none",
            "clientState": "browser-memory-typed" if pages and spec.get("clientState") else "none",
            "queries": "typed-entity-api-collection-filter-sort-pagination" if pages and spec.get("queries") else "none",
            "secretInjection": "environment-alias-redacted-output" if required_secrets else "none",
            "secretEnvironment": [f"CONTROLDECK_SECRET_{index:03d}" for index in range(1, len(required_secrets) + 1)],
            "workflowSideEffects": sorted(set(workflow_payload.get("sideEffects") or workflow_payload.get("side_effects") or [])),
            "auditRoot": "CONTROLDECK_APP_AUDIT_ROOT" if (workflow_payload.get("sideEffects") or workflow_payload.get("side_effects")) else "none",
            "fileRoot": "CONTROLDECK_APP_WORK_ROOT" if any(
                str(node.get("nodeType") or node.get("node_type") or "").startswith("file.")
                for node in workflow_payload.get("nodes") or [] if isinstance(node, dict)
            ) else "none",
        },
        "managedFiles": [row["path"] for row in rows if row["kind"] == "managed"],
        "extensionFiles": [row["path"] for row in rows if row["kind"] == "extension"],
        "configFiles": [row["path"] for row in rows if row["kind"] == "config"],
    }
    manifest_path = f"{root}/.controldeck/generation-manifest.json"
    manifest_bytes = _canonical_json(manifest)
    files[manifest_path] = (manifest_bytes, "manifest")
    archive = _deterministic_zip({path: content for path, (content, _kind) in files.items()})
    return SourceBundle(
        archive_name=f"{project_name}-aspnet-source.zip", archive_bytes=archive,
        archive_checksum=_sha256(archive), source_checksum=source_checksum, manifest=manifest,
        files=tuple([*rows, {"path": manifest_path, "sha256": _sha256(manifest_bytes), "bytes": len(manifest_bytes), "kind": "manifest"}]),
    )


def _page_has_workflow_action(page: dict[str, Any]) -> bool:
    def visit(component: Any) -> bool:
        return isinstance(component, dict) and (
            component.get("type") == "action.workflow-run"
            or any(visit(child) for child in component.get("children") or [])
        )

    return visit(page.get("root"))


def _project_file(namespace: str, has_entities: bool) -> str:
    package = '\n  <ItemGroup><PackageReference Include="Microsoft.Data.Sqlite" Version="8.0.29" /></ItemGroup>' if has_entities else ""
    return f'''<Project Sdk="Microsoft.NET.Sdk.Web">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework><ImplicitUsings>enable</ImplicitUsings><Nullable>enable</Nullable>
    <RootNamespace>{namespace}</RootNamespace><Deterministic>true</Deterministic>
  </PropertyGroup>{package}
</Project>
'''


def _program(
    namespace: str, has_background_jobs: bool, has_entities: bool, has_pages: bool, authentication: str,
) -> str:
    hosted_service = "builder.Services.AddHostedService<GeneratedScheduleService>();" if has_background_jobs else ""
    components_using = f'''using {namespace}.Components;
using Microsoft.AspNetCore.DataProtection.KeyManagement;
using Microsoft.AspNetCore.DataProtection.Repositories;
using System.Xml.Linq;''' if has_pages else ""
    razor_services = '''builder.Services.AddRazorComponents();
builder.Services.Configure<KeyManagementOptions>(options => options.XmlRepository = new GeneratedEphemeralXmlRepository());''' if has_pages else ""
    razor_map = '''app.UseStaticFiles();
app.UseAntiforgery();
app.MapRazorComponents<App>();''' if has_pages else ""
    gui_security = '''app.Use(async (context, next) =>
{
    context.Response.Headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'";
    context.Response.Headers["X-Content-Type-Options"] = "nosniff";
    context.Response.Headers["Referrer-Policy"] = "no-referrer";
    await next();
});''' if has_pages else ""
    browser_auth_map = "GeneratedBrowserSessions.Map(app);" if has_pages and authentication == "api-key" else ""
    ephemeral_repository = '''

sealed class GeneratedEphemeralXmlRepository : IXmlRepository
{
    private readonly List<XElement> _elements = [];
    public IReadOnlyCollection<XElement> GetAllElements()
    {
        lock (_elements) return _elements.Select(element => new XElement(element)).ToArray();
    }
    public void StoreElement(XElement element, string friendlyName)
    {
        lock (_elements) _elements.Add(new XElement(element));
    }
}''' if has_pages else ""
    return f'''using {namespace}.Generated;
{components_using}

var builder = WebApplication.CreateBuilder(args);
builder.WebHost.ConfigureKestrel(options => options.Limits.MaxRequestBodySize = 2 * 1024 * 1024);
{hosted_service}
{razor_services}
var app = builder.Build();
{gui_security}
{"await GeneratedEntities.InitializeAsync(app.Lifetime.ApplicationStopping);" if has_entities else ""}
GeneratedApi.Map(app);
{browser_auth_map}
{"GeneratedEntities.Map(app);" if has_entities else ""}
{razor_map}
app.Run();

public partial class Program {{ }}{ephemeral_repository}
'''


def _application_source(namespace: str, app: dict[str, Any], spec_checksum: str, workflow_checksum: str) -> str:
    return f'''namespace {namespace}.Generated;

public static class GeneratedApplication
{{
    public const string Name = {_csharp_string(str(app.get("name") or namespace))};
    public const string DisplayName = {_csharp_string(str(app.get("displayName") or app.get("name") or namespace))};
    public const string Generator = "{ASPNET_GENERATOR_ID}/{ASPNET_GENERATOR_VERSION}";
    public const string SpecChecksum = "{spec_checksum}";
    public const string WorkflowChecksum = "{workflow_checksum}";
}}
'''


def _browser_auth_source(enabled: bool = True) -> str:
    if not enabled:
        return '''internal static class GeneratedBrowserSessions
{
    internal static bool IsAuthorized(HttpRequest request) => false;
}'''
    return r'''internal static class GeneratedBrowserSessions
{
    private const string CookieName = "ControlDeckGeneratedSession";
    private const int MaxBodyBytes = 16 * 1024;
    private const int MaxSessions = 1_000;
    private const int MaxAttemptSources = 10_000;
    private static readonly TimeSpan SessionLifetime = TimeSpan.FromHours(12);
    private static readonly TimeSpan AttemptWindow = TimeSpan.FromMinutes(5);
    private static readonly ConcurrentDictionary<string, DateTimeOffset> Sessions = new(StringComparer.Ordinal);
    private static readonly ConcurrentDictionary<string, LoginAttempts> Attempts = new(StringComparer.Ordinal);
    private static readonly object SessionGate = new();
    private static readonly object AttemptGate = new();

    internal static void Map(WebApplication app)
    {
        app.MapGet("/auth/session", (HttpRequest request) =>
            IsAuthorized(request) ? Results.Json(new { authenticated = true }) : Results.Unauthorized());
        app.MapPost("/auth/session", LoginAsync);
        app.MapDelete("/auth/session", Logout);
    }

    internal static bool IsAuthorized(HttpRequest request)
    {
        if (!request.Cookies.TryGetValue(CookieName, out var token) || token.Length is < 32 or > 256) return false;
        var hash = Hash(token);
        if (!Sessions.TryGetValue(hash, out var expiresAt)) return false;
        if (expiresAt <= DateTimeOffset.UtcNow) { Sessions.TryRemove(hash, out _); return false; }
        if (HttpMethods.IsGet(request.Method) || HttpMethods.IsHead(request.Method) || HttpMethods.IsOptions(request.Method)) return true;
        return string.Equals(request.Headers["X-Requested-With"].ToString(), "GeneratedApp", StringComparison.Ordinal);
    }

    private static async Task<IResult> LoginAsync(HttpRequest request)
    {
        var remoteAddress = request.HttpContext.Connection.RemoteIpAddress;
        if (!request.IsHttps && (remoteAddress is null || !System.Net.IPAddress.IsLoopback(remoteAddress)))
            return Results.StatusCode(StatusCodes.Status403Forbidden);
        var attemptKey = request.HttpContext.Connection.RemoteIpAddress?.ToString() ?? "unknown";
        if (!TakeAttempt(attemptKey)) return Results.StatusCode(StatusCodes.Status429TooManyRequests);
        if (request.ContentLength is > MaxBodyBytes) return Results.StatusCode(StatusCodes.Status413PayloadTooLarge);
        var body = await ReadBodyAsync(request, request.HttpContext.RequestAborted);
        if (body is null || !body.TryGetPropertyValue("apiKey", out var node) ||
            node is not JsonValue value || !value.TryGetValue<string>(out var apiKey) ||
            !GeneratedApiKey.MatchesExpected(apiKey)) return Results.Unauthorized();
        string token;
        lock (SessionGate)
        {
            PruneExpired();
            if (Sessions.Count >= MaxSessions) return Results.StatusCode(StatusCodes.Status503ServiceUnavailable);
            token = Convert.ToBase64String(RandomNumberGenerator.GetBytes(32)).TrimEnd('=').Replace('+', '-').Replace('/', '_');
            Sessions[Hash(token)] = DateTimeOffset.UtcNow.Add(SessionLifetime);
        }
        Attempts.TryRemove(attemptKey, out _);
        request.HttpContext.Response.Cookies.Append(CookieName, token, new CookieOptions {
            HttpOnly = true, Secure = request.IsHttps, SameSite = SameSiteMode.Strict,
            Path = "/", MaxAge = SessionLifetime, IsEssential = true,
        });
        return Results.Json(new { authenticated = true });
    }

    private static IResult Logout(HttpRequest request, HttpResponse response)
    {
        if (!IsAuthorized(request)) return Results.Unauthorized();
        if (request.Cookies.TryGetValue(CookieName, out var token)) Sessions.TryRemove(Hash(token), out _);
        response.Cookies.Delete(CookieName, new CookieOptions {
            HttpOnly = true, Secure = request.IsHttps, SameSite = SameSiteMode.Strict, Path = "/",
        });
        return Results.NoContent();
    }

    private static async Task<JsonObject?> ReadBodyAsync(HttpRequest request, CancellationToken token)
    {
        if (!request.HasJsonContentType()) return null;
        await using var buffer = new MemoryStream();
        var block = new byte[4096];
        while (true)
        {
            var count = await request.Body.ReadAsync(block.AsMemory(), token);
            if (count == 0) break;
            if (buffer.Length + count > MaxBodyBytes) return null;
            await buffer.WriteAsync(block.AsMemory(0, count), token);
        }
        buffer.Position = 0;
        try { return await JsonNode.ParseAsync(buffer, cancellationToken: token) as JsonObject; }
        catch (JsonException) { return null; }
    }

    private static bool TakeAttempt(string key)
    {
        var now = DateTimeOffset.UtcNow;
        lock (AttemptGate)
        {
            foreach (var item in Attempts.Where(item => now - item.Value.StartedAt >= AttemptWindow)) Attempts.TryRemove(item.Key, out _);
            if (!Attempts.ContainsKey(key) && Attempts.Count >= MaxAttemptSources) return false;
            var state = Attempts.GetOrAdd(key, _ => new LoginAttempts(now));
            if (now - state.StartedAt >= AttemptWindow) { state.StartedAt = now; state.Count = 0; }
            if (state.Count >= 5) return false;
            state.Count++;
            return true;
        }
    }

    private static void PruneExpired()
    {
        var now = DateTimeOffset.UtcNow;
        foreach (var item in Sessions.Where(item => item.Value <= now)) Sessions.TryRemove(item.Key, out _);
    }

    private static string Hash(string token) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(token)));

    private sealed class LoginAttempts(DateTimeOffset startedAt)
    {
        internal DateTimeOffset StartedAt { get; set; } = startedAt;
        internal int Count { get; set; }
    }
}'''


def _api_source(
    namespace: str, endpoints: list[dict[str, Any]], background_jobs: list[dict[str, Any]],
    authentication: str, has_pages: bool, openapi_json: str,
) -> str:
    rows: list[str] = []
    schema_rows: list[str] = []
    for index, endpoint in enumerate(endpoints):
        path = _csharp_string(str(endpoint.get("path") or "/api/run"))
        timeout = float(endpoint.get("timeoutSeconds") or 120)
        anonymous = endpoint.get("authentication") == "anonymous" or authentication == "none"
        auth = "true" if anonymous else "false"
        mode = str(endpoint.get("mode") or "sync")
        request_schema = endpoint.get("requestSchema") if isinstance(endpoint.get("requestSchema"), dict) else {}
        response_schema = endpoint.get("responseSchema") if isinstance(endpoint.get("responseSchema"), dict) else {}
        request_ref = f"RequestSchema{index}" if request_schema else "null"
        response_ref = f"ResponseSchema{index}" if response_schema else "null"
        if request_schema:
            schema_rows.append(f"    private static readonly JsonNode RequestSchema{index} = JsonNode.Parse({_csharp_string(json.dumps(request_schema, ensure_ascii=False, sort_keys=True, separators=(',', ':')))})!;")
        if response_schema:
            schema_rows.append(f"    private static readonly JsonNode ResponseSchema{index} = JsonNode.Parse({_csharp_string(json.dumps(response_schema, ensure_ascii=False, sort_keys=True, separators=(',', ':')))})!;")
        action = (
            f"return GeneratedJobs.Start(input, {timeout!r}, anonymous: {auth}, app.Lifetime.ApplicationStopping, {response_ref});"
            if mode == "async" else
            f"return await RunSyncAsync(input, {timeout!r}, request.HttpContext.RequestAborted, {response_ref});"
        )
        rows.append(f'''        app.MapPost({path}, async (HttpRequest request) =>
        {{
            if (!GeneratedApiKey.IsAuthorized(request, anonymous: {auth})) return Results.Unauthorized();
            var input = await ReadInputAsync(request, request.HttpContext.RequestAborted);
            var requestErrors = GeneratedJsonSchema.Validate(input, {request_ref});
            if (requestErrors.Count > 0) return Results.Json(new {{ error = "Request schema validation failed", diagnostics = requestErrors }}, statusCode: 400);
            AddRouteValues(input, request);
            {action}
        }});''')
    routes = "\n".join(rows)
    schema_fields = "\n".join(schema_rows)
    has_jobs = any(str(item.get("mode") or "sync") == "async" for item in endpoints) or bool(background_jobs)
    job_routes = '''
        app.MapGet("/api/jobs/{jobId}", GeneratedJobs.Status);
        app.MapDelete("/api/jobs/{jobId}", GeneratedJobs.Cancel);
        app.MapGet("/api/jobs/{jobId}/events", GeneratedJobs.EventsAsync);
''' if has_jobs else ""
    background_routes = '''
        app.MapGet("/api/background-jobs", GeneratedScheduleApi.List);
        app.MapPost("/api/background-jobs/{definitionId}/run", GeneratedScheduleApi.Run);
''' if background_jobs else ""
    browser_auth_source = _browser_auth_source(authentication == "api-key" and has_pages)
    template = r'''#nullable enable
using System.Collections.Concurrent;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace __NAMESPACE__.Generated;

public static class GeneratedApi
{
    private const string OpenApiJson = __OPENAPI__;
__SCHEMA_FIELDS__

    public static void Map(WebApplication app)
    {
        app.MapGet("/healthz", () => Results.Json(new { ok = true, application = GeneratedApplication.Name }));
        app.MapGet("/openapi.json", () => Results.Text(OpenApiJson, "application/json", Encoding.UTF8));
__ROUTES__
__JOB_ROUTES__
__BACKGROUND_ROUTES__
    }

    private static async Task<JsonObject> ReadInputAsync(HttpRequest request, CancellationToken token)
    {
        if (request.ContentLength == 0) return new JsonObject();
        if (!request.HasJsonContentType()) throw new BadHttpRequestException("Content-Type must be application/json", StatusCodes.Status415UnsupportedMediaType);
        await using var buffer = new MemoryStream();
        await request.Body.CopyToAsync(buffer, token);
        if (buffer.Length == 0) return new JsonObject();
        buffer.Position = 0;
        try
        {
            return await JsonNode.ParseAsync(buffer, cancellationToken: token) as JsonObject
                ?? throw new BadHttpRequestException("JSON request body must be an object");
        }
        catch (JsonException exception) { throw new BadHttpRequestException("Request body is not valid JSON", exception); }
    }

    private static void AddRouteValues(JsonObject input, HttpRequest request)
    {
        var route = new JsonObject();
        foreach (var pair in request.RouteValues.OrderBy(item => item.Key, StringComparer.Ordinal))
            route[pair.Key] = pair.Value?.ToString() ?? "";
        input["route"] = route;
    }

    private static async Task<IResult> RunSyncAsync(JsonObject input, double timeoutSeconds, CancellationToken requestToken, JsonNode? responseSchema)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(requestToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(Math.Clamp(timeoutSeconds, 0.1, 7200)));
        try
        {
            var output = await GeneratedWorkflow.RunAsync(input, timeout.Token);
            var responseErrors = GeneratedJsonSchema.Validate(output, responseSchema);
            return responseErrors.Count == 0
                ? Results.Json(output)
                : Results.Json(new { error = "Response schema validation failed", diagnostics = responseErrors }, statusCode: 500);
        }
        catch (OperationCanceledException) when (!requestToken.IsCancellationRequested)
        { return Results.Json(new { error = "Workflow timeout" }, statusCode: StatusCodes.Status504GatewayTimeout); }
    }
}

internal static class GeneratedApiKey
{
    internal static bool IsAuthorized(HttpRequest request, bool anonymous)
    {
        if (anonymous) return true;
        var provided = request.Headers["X-API-Key"].ToString();
        if (!string.IsNullOrEmpty(provided) && MatchesExpected(provided)) return true;
        return GeneratedBrowserSessions.IsAuthorized(request);
    }

    internal static bool MatchesExpected(string provided)
    {
        var expected = Environment.GetEnvironmentVariable("CONTROLDECK_APP_API_KEY");
        if (string.IsNullOrEmpty(expected) || string.IsNullOrEmpty(provided)) return false;
        var left = Encoding.UTF8.GetBytes(expected); var right = Encoding.UTF8.GetBytes(provided);
        return left.Length == right.Length && CryptographicOperations.FixedTimeEquals(left, right);
    }
}

__BROWSER_AUTH_SOURCE__

internal sealed class GeneratedJob
{
    internal GeneratedJob(bool anonymous, double timeoutSeconds, CancellationToken shutdown, JsonNode? responseSchema)
    {
        Anonymous = anonymous;
        Cancellation = CancellationTokenSource.CreateLinkedTokenSource(shutdown);
        TimeoutCancellation.CancelAfter(TimeSpan.FromSeconds(Math.Clamp(timeoutSeconds, 0.1, 7200)));
        ExecutionCancellation = CancellationTokenSource.CreateLinkedTokenSource(Cancellation.Token, TimeoutCancellation.Token);
        Shutdown = shutdown;
        ResponseSchema = responseSchema;
    }
    internal string Id { get; } = Guid.NewGuid().ToString("N");
    internal bool Anonymous { get; }
    internal CancellationTokenSource Cancellation { get; }
    internal CancellationTokenSource TimeoutCancellation { get; } = new();
    internal CancellationTokenSource ExecutionCancellation { get; }
    internal CancellationToken Shutdown { get; }
    internal JsonNode? ResponseSchema { get; }
    internal DateTimeOffset CreatedAt { get; } = DateTimeOffset.UtcNow;
    internal string Status { get; set; } = "queued";
    internal JsonObject? Result { get; set; }
    internal string Error { get; set; } = "";
    internal ConcurrentQueue<string> Events { get; } = new();
    internal bool Finished => Status is "completed" or "failed" or "cancelled";
    internal JsonObject Snapshot() => new()
    {
        ["id"] = Id, ["status"] = Status, ["createdAt"] = CreatedAt.ToString("O"),
        ["result"] = Result?.DeepClone(), ["error"] = Error,
    };
}

internal static class GeneratedJobs
{
    private const int MaxJobs = 1000;
    private static readonly ConcurrentDictionary<string, GeneratedJob> Jobs = new(StringComparer.Ordinal);

    internal static IResult Start(JsonObject input, double timeoutSeconds, bool anonymous, CancellationToken shutdown, JsonNode? responseSchema)
    {
        var job = StartCore(input, timeoutSeconds, anonymous, shutdown, responseSchema);
        return job is null
            ? Results.Json(new { error = "Job capacity reached" }, statusCode: 503)
            : Results.Json(new { id = job.Id, status = job.Status, eventsUrl = $"/api/jobs/{job.Id}/events" }, statusCode: 202);
    }

    internal static string? StartScheduled(JsonObject input, double timeoutSeconds, bool anonymous, CancellationToken shutdown) =>
        StartCore(input, timeoutSeconds, anonymous, shutdown, responseSchema: null)?.Id;

    internal static bool IsFinished(string? jobId) =>
        string.IsNullOrEmpty(jobId) || !Jobs.TryGetValue(jobId, out var job) || job.Finished;

    private static GeneratedJob? StartCore(JsonObject input, double timeoutSeconds, bool anonymous, CancellationToken shutdown, JsonNode? responseSchema)
    {
        foreach (var old in Jobs.Values.Where(item => item.Finished).OrderBy(item => item.CreatedAt).Take(Math.Max(0, Jobs.Count - MaxJobs + 1)))
            Jobs.TryRemove(old.Id, out _);
        if (Jobs.Count >= MaxJobs) return null;
        var job = new GeneratedJob(anonymous, timeoutSeconds, shutdown, responseSchema); Jobs[job.Id] = job; Enqueue(job, "queued");
        _ = Task.Run(async () =>
        {
            try
            {
                job.Status = "running"; Enqueue(job, "running");
                job.Result = await GeneratedWorkflow.RunAsync(input, job.ExecutionCancellation.Token);
                var responseErrors = GeneratedJsonSchema.Validate(job.Result, job.ResponseSchema);
                if (responseErrors.Count > 0)
                {
                    job.Status = "failed"; job.Error = "Response schema validation failed"; Enqueue(job, "failed"); return;
                }
                job.Status = "completed"; Enqueue(job, "completed");
            }
            catch (OperationCanceledException)
            {
                job.Status = "cancelled";
                job.Error = job.Shutdown.IsCancellationRequested ? "Application stopping"
                    : job.TimeoutCancellation.IsCancellationRequested ? "Workflow timeout" : "Workflow cancelled";
                Enqueue(job, "cancelled");
            }
            catch (Exception exception)
            {
                Console.Error.WriteLine($"Generated workflow job {job.Id} failed ({exception.GetType().Name})");
                job.Status = "failed"; job.Error = "Workflow failed"; Enqueue(job, "failed");
            }
            finally { job.ExecutionCancellation.Dispose(); job.TimeoutCancellation.Dispose(); job.Cancellation.Dispose(); }
        }, CancellationToken.None);
        return job;
    }

    internal static IResult Status(HttpRequest request, string jobId)
    {
        if (!Jobs.TryGetValue(jobId, out var job)) return Results.NotFound();
        return GeneratedApiKey.IsAuthorized(request, job.Anonymous) ? Results.Json(job.Snapshot()) : Results.Unauthorized();
    }

    internal static IResult Cancel(HttpRequest request, string jobId)
    {
        if (!Jobs.TryGetValue(jobId, out var job)) return Results.NotFound();
        if (!GeneratedApiKey.IsAuthorized(request, job.Anonymous)) return Results.Unauthorized();
        if (job.Finished) return Results.Conflict(new { error = "Job already finished" });
        try { job.Cancellation.Cancel(); }
        catch (ObjectDisposedException) { return Results.Conflict(new { error = "Job already finished" }); }
        return Results.Accepted($"/api/jobs/{job.Id}", new { id = job.Id, status = "cancelling" });
    }

    internal static async Task EventsAsync(HttpContext context, string jobId)
    {
        if (!Jobs.TryGetValue(jobId, out var job)) { context.Response.StatusCode = 404; return; }
        if (!GeneratedApiKey.IsAuthorized(context.Request, job.Anonymous)) { context.Response.StatusCode = 401; return; }
        context.Response.Headers.CacheControl = "no-store"; context.Response.ContentType = "text/event-stream";
        try
        {
            while (!job.Finished || !job.Events.IsEmpty)
            {
                while (job.Events.TryDequeue(out var item))
                    await context.Response.WriteAsync($"event: status\ndata: {item}\n\n", context.RequestAborted);
                await context.Response.Body.FlushAsync(context.RequestAborted);
                if (!job.Finished) await Task.Delay(200, context.RequestAborted);
            }
        }
        catch (OperationCanceledException) when (context.RequestAborted.IsCancellationRequested) { }
    }

    private static void Enqueue(GeneratedJob job, string status) => job.Events.Enqueue(
        JsonSerializer.Serialize(new { id = job.Id, status }));
}
'''
    return (template.replace("__NAMESPACE__", namespace)
            .replace("__OPENAPI__", _csharp_string(openapi_json))
            .replace("__SCHEMA_FIELDS__", schema_fields)
            .replace("__ROUTES__", routes).replace("__JOB_ROUTES__", job_routes)
            .replace("__BACKGROUND_ROUTES__", background_routes)
            .replace("__BROWSER_AUTH_SOURCE__", browser_auth_source))


def _json_schema_source(namespace: str) -> str:
    return f'''#nullable enable
using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace {namespace}.Generated;

public sealed record GeneratedSchemaError(string Path, string Keyword, string Message);

public static class GeneratedJsonSchema
{{
    private const int MaxErrors = 100;
    private const int MaxDepth = 64;
    private const int MaxArrayItems = 10_000;

    public static IReadOnlyList<GeneratedSchemaError> Validate(JsonNode? instance, JsonNode? schema)
    {{
        var errors = new List<GeneratedSchemaError>();
        if (schema is not null) ValidateNode(instance, schema, "$", 0, errors);
        return errors;
    }}

    private static void ValidateNode(JsonNode? instance, JsonNode schema, string path, int depth, List<GeneratedSchemaError> errors)
    {{
        if (errors.Count >= MaxErrors) return;
        if (depth > MaxDepth) {{ Add(errors, path, "depth", "Schema validation depth exceeded"); return; }}
        if (schema is JsonValue booleanSchema && booleanSchema.TryGetValue<bool>(out var allowed))
        {{
            if (!allowed) Add(errors, path, "falseSchema", "Value is rejected by the schema");
            return;
        }}
        if (schema is not JsonObject rule) {{ Add(errors, path, "schema", "Schema must be an object or boolean"); return; }}

        if (rule["type"] is JsonNode typeRule && !MatchesType(instance, typeRule))
        {{
            Add(errors, path, "type", "Value has an unexpected JSON type");
            return;
        }}
        if (rule["const"] is JsonNode constant && !JsonNode.DeepEquals(instance, constant))
            Add(errors, path, "const", "Value does not match const");
        if (rule["enum"] is JsonArray choices && !choices.Any(item => JsonNode.DeepEquals(instance, item)))
            Add(errors, path, "enum", "Value is not in enum");

        ValidateCombinators(instance, rule, path, depth, errors);
        if (instance is JsonObject obj) ValidateObject(obj, rule, path, depth, errors);
        if (instance is JsonArray array) ValidateArray(array, rule, path, depth, errors);
        if (TryString(instance, out var text)) ValidateString(text, rule, path, errors);
        if (TryNumber(instance, out var number)) ValidateNumber(number, rule, path, errors);
    }}

    private static void ValidateCombinators(JsonNode? instance, JsonObject rule, string path, int depth, List<GeneratedSchemaError> errors)
    {{
        foreach (var keyword in new[] {{ "allOf", "anyOf", "oneOf" }})
        {{
            if (rule[keyword] is not JsonArray schemas) continue;
            var matches = schemas.Count(schema => schema is not null && Matches(instance, schema, depth + 1));
            if (keyword == "allOf" && matches != schemas.Count) Add(errors, path, keyword, "Value does not match every schema");
            if (keyword == "anyOf" && matches == 0) Add(errors, path, keyword, "Value does not match any schema");
            if (keyword == "oneOf" && matches != 1) Add(errors, path, keyword, "Value must match exactly one schema");
        }}
        if (rule["not"] is JsonNode excluded && Matches(instance, excluded, depth + 1))
            Add(errors, path, "not", "Value matches an excluded schema");
    }}

    private static void ValidateObject(JsonObject obj, JsonObject rule, string path, int depth, List<GeneratedSchemaError> errors)
    {{
        if (TryInteger(rule["minProperties"], out var min) && obj.Count < min) Add(errors, path, "minProperties", "Object has too few properties");
        if (TryInteger(rule["maxProperties"], out var max) && obj.Count > max) Add(errors, path, "maxProperties", "Object has too many properties");
        if (rule["required"] is JsonArray required)
            foreach (var item in required)
                if (TryString(item, out var name) && !obj.ContainsKey(name)) Add(errors, Pointer(path, name), "required", "Required property is missing");

        var properties = rule["properties"] as JsonObject;
        if (properties is not null)
            foreach (var pair in properties)
                if (pair.Value is not null && obj.TryGetPropertyValue(pair.Key, out var value))
                    ValidateNode(value, pair.Value, Pointer(path, pair.Key), depth + 1, errors);

        if (rule["additionalProperties"] is JsonNode additional)
            foreach (var pair in obj)
            {{
                if (properties?.ContainsKey(pair.Key) is true) continue;
                if (additional is JsonValue flag && flag.TryGetValue<bool>(out var enabled) && !enabled)
                    Add(errors, Pointer(path, pair.Key), "additionalProperties", "Additional property is not allowed");
                else if (additional is JsonObject)
                    ValidateNode(pair.Value, additional, Pointer(path, pair.Key), depth + 1, errors);
            }}
    }}

    private static void ValidateArray(JsonArray array, JsonObject rule, string path, int depth, List<GeneratedSchemaError> errors)
    {{
        if (array.Count > MaxArrayItems) {{ Add(errors, path, "maxItems", "Array exceeds generated runtime limit"); return; }}
        if (TryInteger(rule["minItems"], out var min) && array.Count < min) Add(errors, path, "minItems", "Array has too few items");
        if (TryInteger(rule["maxItems"], out var max) && array.Count > max) Add(errors, path, "maxItems", "Array has too many items");
        if (rule["uniqueItems"] is JsonValue unique && unique.TryGetValue<bool>(out var uniqueEnabled) && uniqueEnabled)
        {{
            var values = new HashSet<string>(StringComparer.Ordinal);
            for (var index = 0; index < array.Count; index++)
                if (!values.Add(Canonical(array[index]))) Add(errors, path + "/" + index, "uniqueItems", "Array item is duplicated");
        }}
        if (rule["items"] is JsonNode itemSchema)
            for (var index = 0; index < array.Count && errors.Count < MaxErrors; index++)
                ValidateNode(array[index], itemSchema, path + "/" + index, depth + 1, errors);
    }}

    private static void ValidateString(string text, JsonObject rule, string path, List<GeneratedSchemaError> errors)
    {{
        var length = text.EnumerateRunes().Count();
        if (TryInteger(rule["minLength"], out var min) && length < min) Add(errors, path, "minLength", "String is too short");
        if (TryInteger(rule["maxLength"], out var max) && length > max) Add(errors, path, "maxLength", "String is too long");
    }}

    private static void ValidateNumber(double number, JsonObject rule, string path, List<GeneratedSchemaError> errors)
    {{
        if (TryNumber(rule["minimum"], out var minimum) && number < minimum) Add(errors, path, "minimum", "Number is below minimum");
        if (TryNumber(rule["maximum"], out var maximum) && number > maximum) Add(errors, path, "maximum", "Number is above maximum");
        if (TryNumber(rule["exclusiveMinimum"], out var exclusiveMinimum) && number <= exclusiveMinimum) Add(errors, path, "exclusiveMinimum", "Number is below exclusive minimum");
        if (TryNumber(rule["exclusiveMaximum"], out var exclusiveMaximum) && number >= exclusiveMaximum) Add(errors, path, "exclusiveMaximum", "Number is above exclusive maximum");
        if (TryNumber(rule["multipleOf"], out var divisor) && divisor > 0)
        {{
            var quotient = number / divisor;
            if (Math.Abs(quotient - Math.Round(quotient)) > 1e-9 * Math.Max(1, Math.Abs(quotient)))
                Add(errors, path, "multipleOf", "Number is not a multiple of the required value");
        }}
    }}

    private static bool Matches(JsonNode? instance, JsonNode schema, int depth)
    {{
        var nested = new List<GeneratedSchemaError>(); ValidateNode(instance, schema, "$", depth, nested); return nested.Count == 0;
    }}

    private static bool MatchesType(JsonNode? node, JsonNode typeRule)
    {{
        if (TryString(typeRule, out var single)) return IsType(node, single);
        return typeRule is JsonArray many && many.Any(item => TryString(item, out var name) && IsType(node, name));
    }}

    private static bool IsType(JsonNode? node, string type) => type switch
    {{
        "null" => node is null || node.GetValueKind() == JsonValueKind.Null,
        "object" => node is JsonObject,
        "array" => node is JsonArray,
        "string" => TryString(node, out _),
        "boolean" => node is JsonValue value && value.TryGetValue<bool>(out _),
        "number" => TryNumber(node, out _),
        "integer" => TryNumber(node, out var number) && double.IsFinite(number) && Math.Truncate(number) == number,
        _ => false,
    }};

    private static bool TryString(JsonNode? node, out string value)
    {{
        value = ""; return node is JsonValue json && json.TryGetValue(out value!);
    }}

    private static bool TryInteger(JsonNode? node, out int value)
    {{
        value = 0;
        return TryNumber(node, out var number) && number >= 0 && number <= int.MaxValue && Math.Truncate(number) == number && (value = (int)number) >= 0;
    }}

    private static bool TryNumber(JsonNode? node, out double value)
    {{
        value = 0;
        if (node is not JsonValue json) return false;
        if (json.TryGetValue(out value)) return double.IsFinite(value);
        if (json.TryGetValue<decimal>(out var decimalValue)) {{ value = (double)decimalValue; return double.IsFinite(value); }}
        if (json.TryGetValue<JsonElement>(out var element) && element.ValueKind == JsonValueKind.Number && element.TryGetDouble(out value)) return double.IsFinite(value);
        return false;
    }}

    private static string Canonical(JsonNode? node)
    {{
        if (node is JsonObject obj) return "{{" + string.Join(",", obj.OrderBy(item => item.Key, StringComparer.Ordinal).Select(item => JsonSerializer.Serialize(item.Key) + ":" + Canonical(item.Value))) + "}}";
        if (node is JsonArray array) return "[" + string.Join(",", array.Select(Canonical)) + "]";
        if (node is JsonValue value && value.TryGetValue<decimal>(out var decimalValue)) return "n:" + decimalValue.ToString("G29", CultureInfo.InvariantCulture);
        if (TryNumber(node, out var number)) return "n:" + number.ToString("R", CultureInfo.InvariantCulture);
        return node?.ToJsonString() ?? "null";
    }}

    private static string Pointer(string path, string name) => path + "/" + name.Replace("~", "~0", StringComparison.Ordinal).Replace("/", "~1", StringComparison.Ordinal);
    private static void Add(List<GeneratedSchemaError> errors, string path, string keyword, string message)
    {{
        if (errors.Count < MaxErrors) errors.Add(new(path, keyword, message));
    }}
}}
'''


def _background_jobs_source(
    namespace: str, background_jobs: list[dict[str, Any]], authentication: str,
) -> str:
    rows: list[str] = []
    for job in sorted(background_jobs, key=lambda item: str(item.get("id") or "")):
        payload = job.get("input") if isinstance(job.get("input"), dict) else {}
        rows.append(
            "        new(" + ", ".join([
                _csharp_string(str(job.get("id") or "job")),
                _csharp_string(str(job.get("trigger") or "manual")),
                _csharp_string(str(job.get("schedule") or "")),
                _csharp_string(str(job.get("timeZone") or "UTC")),
                f"JsonNode.Parse({_csharp_string(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')))}) as JsonObject ?? new JsonObject()",
                repr(float(job.get("timeoutSeconds") or 7200)),
                "true" if job.get("enabled", True) else "false",
                _csharp_string(str(job.get("concurrencyPolicy") or "skip")),
                _csharp_string(str(job.get("catchUpPolicy") or "run-once")),
            ]) + "),"
        )
    definitions = "\n".join(rows)
    anonymous = "true" if authentication == "none" else "false"
    template = r'''#nullable enable
using System.Collections.Concurrent;
using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace __NAMESPACE__.Generated;

internal sealed record GeneratedScheduleDefinition(
    string Id, string Trigger, string Schedule, string TimeZone, JsonObject Input,
    double TimeoutSeconds, bool Enabled, string ConcurrencyPolicy, string CatchUpPolicy);

internal sealed class GeneratedScheduleState
{
    public DateTimeOffset? LastStartedAt { get; set; }
    public DateTimeOffset? LastEvaluatedAt { get; set; }
    public bool RunPending { get; set; }
    public bool Queued { get; set; }
}

internal static class GeneratedScheduleRegistry
{
    internal static readonly GeneratedScheduleDefinition[] Definitions =
    [
__DEFINITIONS__
    ];
    internal static readonly ConcurrentDictionary<string, string> ActiveExecutions = new(StringComparer.Ordinal);
    internal static GeneratedScheduleDefinition? Find(string id) =>
        Definitions.FirstOrDefault(item => string.Equals(item.Id, id, StringComparison.Ordinal));
}

internal static class GeneratedScheduleApi
{
    internal static IResult List(HttpRequest request)
    {
        if (!GeneratedApiKey.IsAuthorized(request, anonymous: __ANONYMOUS__)) return Results.Unauthorized();
        return Results.Json(GeneratedScheduleRegistry.Definitions.Select(item => new
        {
            id = item.Id, trigger = item.Trigger, schedule = item.Schedule, timeZone = item.TimeZone,
            enabled = item.Enabled, concurrencyPolicy = item.ConcurrencyPolicy, catchUpPolicy = item.CatchUpPolicy,
            activeExecutionId = GeneratedScheduleRegistry.ActiveExecutions.TryGetValue(item.Id, out var active) ? active : null,
        }));
    }

    internal static IResult Run(HttpRequest request, string definitionId, IHostApplicationLifetime lifetime)
    {
        if (!GeneratedApiKey.IsAuthorized(request, anonymous: __ANONYMOUS__)) return Results.Unauthorized();
        var definition = GeneratedScheduleRegistry.Find(definitionId);
        if (definition is null) return Results.NotFound();
        if (!definition.Enabled) return Results.Conflict(new { error = "Background job is disabled" });
        if (GeneratedScheduleRegistry.ActiveExecutions.TryGetValue(definition.Id, out var activeId))
        {
            if (!GeneratedJobs.IsFinished(activeId)) return Results.Conflict(new { error = "Background job is already running" });
            GeneratedScheduleRegistry.ActiveExecutions.TryRemove(definition.Id, out _);
        }
        var input = ScheduledInput(definition, "manual", DateTimeOffset.UtcNow);
        var executionId = GeneratedJobs.StartScheduled(input, definition.TimeoutSeconds, __ANONYMOUS__, lifetime.ApplicationStopping);
        if (executionId is null) return Results.Json(new { error = "Job capacity reached" }, statusCode: 503);
        GeneratedScheduleRegistry.ActiveExecutions[definition.Id] = executionId;
        return Results.Json(new { id = executionId, definitionId = definition.Id, status = "queued", eventsUrl = $"/api/jobs/{executionId}/events" }, statusCode: 202);
    }

    internal static JsonObject ScheduledInput(GeneratedScheduleDefinition definition, string reason, DateTimeOffset scheduledAt)
    {
        var input = definition.Input.DeepClone() as JsonObject ?? new JsonObject();
        input["schedule"] = new JsonObject
        {
            ["definitionId"] = definition.Id, ["trigger"] = definition.Trigger,
            ["reason"] = reason, ["scheduledAt"] = scheduledAt.ToString("O"),
        };
        return input;
    }
}

internal sealed class GeneratedScheduleService(ILogger<GeneratedScheduleService> logger, IHostApplicationLifetime lifetime) : BackgroundService
{
    private readonly Dictionary<string, GeneratedScheduleState> _states = LoadState(logger);
    private readonly DateTimeOffset _startedAt = DateTimeOffset.UtcNow;

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        InitializeSkipPolicies();
        while (!stoppingToken.IsCancellationRequested)
        {
            try { await EvaluateAsync(stoppingToken); }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
            catch (Exception exception) { logger.LogError(exception, "Generated background schedule evaluation failed"); }
            await Task.Delay(TimeSpan.FromSeconds(1), stoppingToken);
        }
    }

    private void InitializeSkipPolicies()
    {
        var changed = false;
        foreach (var definition in GeneratedScheduleRegistry.Definitions.Where(item => item.Enabled && item.Trigger != "manual"))
        {
            var state = State(definition.Id);
            if (state.LastEvaluatedAt is null) { state.LastEvaluatedAt = _startedAt; changed = true; }
            if (definition.CatchUpPolicy == "skip" && state.LastStartedAt is null) { state.LastStartedAt = _startedAt; changed = true; }
        }
        if (changed) SaveState(_states, logger);
    }

    private Task EvaluateAsync(CancellationToken stoppingToken)
    {
        var now = DateTimeOffset.UtcNow;
        var changed = false;
        foreach (var definition in GeneratedScheduleRegistry.Definitions.Where(item => item.Enabled && item.Trigger != "manual"))
        {
            GeneratedScheduleRegistry.ActiveExecutions.TryGetValue(definition.Id, out var activeId);
            var state = State(definition.Id);
            var previousEvaluation = state.LastEvaluatedAt;
            if (!GeneratedJobs.IsFinished(activeId))
            {
                if (IsDue(definition, state, now))
                {
                    if (definition.ConcurrencyPolicy == "queue-one") state.Queued = true;
                    state.LastStartedAt = now;
                    changed = true;
                }
                if (state.LastEvaluatedAt != previousEvaluation) changed = true;
                continue;
            }
            var recoverPending = activeId is null && state.RunPending;
            if (activeId is not null)
            {
                GeneratedScheduleRegistry.ActiveExecutions.TryRemove(definition.Id, out _);
                state.RunPending = false;
                changed = true;
            }
            var queued = recoverPending || state.Queued;
            if (!queued && !IsDue(definition, state, now))
            {
                if (state.LastEvaluatedAt != previousEvaluation) changed = true;
                continue;
            }
            var executionId = GeneratedJobs.StartScheduled(
                GeneratedScheduleApi.ScheduledInput(definition, queued ? "queued" : "schedule", now),
                definition.TimeoutSeconds, __ANONYMOUS__, lifetime.ApplicationStopping);
            if (executionId is null) continue;
            state.Queued = false;
            state.RunPending = true;
            GeneratedScheduleRegistry.ActiveExecutions[definition.Id] = executionId;
            state.LastStartedAt = now;
            changed = true;
            logger.LogInformation("Generated background job {DefinitionId} started as {ExecutionId}", definition.Id, executionId);
        }
        if (changed) SaveState(_states, logger);
        return Task.CompletedTask;
    }

    private bool IsDue(GeneratedScheduleDefinition definition, GeneratedScheduleState state, DateTimeOffset now)
    {
        if (definition.Trigger == "interval")
            return double.TryParse(definition.Schedule, NumberStyles.Float, CultureInfo.InvariantCulture, out var seconds)
                && (state.LastStartedAt is null || now - state.LastStartedAt >= TimeSpan.FromSeconds(seconds));

        var zone = TimeZoneInfo.FindSystemTimeZoneById(definition.TimeZone);
        var localNow = TimeZoneInfo.ConvertTime(now, zone);
        if (definition.Trigger == "daily")
        {
            if (!TimeOnly.TryParseExact(definition.Schedule, "HH:mm", CultureInfo.InvariantCulture, DateTimeStyles.None, out var at)) return false;
            var scheduledLocal = DateTime.SpecifyKind(new DateTime(localNow.Year, localNow.Month, localNow.Day, at.Hour, at.Minute, 0), DateTimeKind.Unspecified);
            if (zone.IsInvalidTime(scheduledLocal)) return false;
            var scheduledUtc = new DateTimeOffset(TimeZoneInfo.ConvertTimeToUtc(scheduledLocal, zone), TimeSpan.Zero);
            if (now < scheduledUtc) return false;
            return state.LastStartedAt is null || state.LastStartedAt.Value < scheduledUtc;
        }
        if (definition.Trigger == "cron")
        {
            var previousUtc = state.LastEvaluatedAt ?? now;
            state.LastEvaluatedAt = now;
            var currentMinute = new DateTimeOffset(now.UtcDateTime.Year, now.UtcDateTime.Month, now.UtcDateTime.Day, now.UtcDateTime.Hour, now.UtcDateTime.Minute, 0, TimeSpan.Zero);
            var previous = previousUtc.UtcDateTime;
            var cursor = new DateTimeOffset(previous.Year, previous.Month, previous.Day, previous.Hour, previous.Minute, 0, TimeSpan.Zero).AddMinutes(1);
            var limit = 527_100;
            while (cursor <= currentMinute && limit-- > 0)
            {
                if (GeneratedCron.Matches(definition.Schedule, TimeZoneInfo.ConvertTime(cursor, zone).DateTime)) return true;
                cursor = cursor.AddMinutes(1);
            }
        }
        return false;
    }

    private GeneratedScheduleState State(string id)
    {
        if (!_states.TryGetValue(id, out var state)) _states[id] = state = new GeneratedScheduleState();
        return state;
    }

    private static string StatePath()
    {
        var configured = Environment.GetEnvironmentVariable("CONTROLDECK_APP_DATA_DIR");
        var root = string.IsNullOrWhiteSpace(configured)
            ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), GeneratedApplication.Name)
            : configured;
        var resolved = Path.GetFullPath(root);
        Directory.CreateDirectory(resolved);
        var path = Path.GetFullPath(Path.Combine(resolved, "schedule-state.json"));
        var prefix = resolved.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
        var comparison = OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
        if (!path.StartsWith(prefix, comparison)) throw new InvalidOperationException("Schedule state path escaped the configured data root");
        return path;
    }

    private static Dictionary<string, GeneratedScheduleState> LoadState(ILogger logger)
    {
        try
        {
            var path = StatePath();
            if (File.Exists(path) && new FileInfo(path).Length > 1024 * 1024) throw new InvalidDataException("Generated schedule state exceeds 1 MiB");
            return File.Exists(path)
                ? JsonSerializer.Deserialize<Dictionary<string, GeneratedScheduleState>>(File.ReadAllText(path)) ?? new(StringComparer.Ordinal)
                : new(StringComparer.Ordinal);
        }
        catch (Exception exception) { logger.LogError(exception, "Generated schedule state could not be loaded"); return new(StringComparer.Ordinal); }
    }

    private static void SaveState(Dictionary<string, GeneratedScheduleState> states, ILogger logger)
    {
        try
        {
            var path = StatePath(); var temporary = path + ".tmp";
            File.WriteAllText(temporary, JsonSerializer.Serialize(states));
            File.Move(temporary, path, overwrite: true);
        }
        catch (Exception exception) { logger.LogError(exception, "Generated schedule state could not be saved"); }
    }
}

public static class GeneratedCron
{
    public static bool Matches(string expression, DateTime local)
    {
        var fields = expression.Split(' ', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        if (fields.Length != 5) return false;
        var minute = Field(fields[0], 0, 59, local.Minute);
        var hour = Field(fields[1], 0, 23, local.Hour);
        var month = Field(fields[3], 1, 12, local.Month, Months);
        var dayOfMonth = Field(fields[2], 1, 31, local.Day);
        var dayOfWeek = Field(fields[4], 0, 7, (int)local.DayOfWeek, Days, sunday: true);
        var dayMatches = fields[2] == "*" || fields[4] == "*" ? dayOfMonth && dayOfWeek : dayOfMonth || dayOfWeek;
        return minute && hour && month && dayMatches;
    }

    private static readonly Dictionary<string, int> Months = new(StringComparer.OrdinalIgnoreCase)
    { ["JAN"] = 1, ["FEB"] = 2, ["MAR"] = 3, ["APR"] = 4, ["MAY"] = 5, ["JUN"] = 6, ["JUL"] = 7, ["AUG"] = 8, ["SEP"] = 9, ["OCT"] = 10, ["NOV"] = 11, ["DEC"] = 12 };
    private static readonly Dictionary<string, int> Days = new(StringComparer.OrdinalIgnoreCase)
    { ["SUN"] = 0, ["MON"] = 1, ["TUE"] = 2, ["WED"] = 3, ["THU"] = 4, ["FRI"] = 5, ["SAT"] = 6 };

    private static bool Field(string source, int min, int max, int value, Dictionary<string, int>? names = null, bool sunday = false)
    {
        foreach (var item in source.Split(',', StringSplitOptions.RemoveEmptyEntries))
        {
            var stepParts = item.Split('/', 2); if (stepParts.Length == 2 && (!int.TryParse(stepParts[1], out var step) || step < 1)) continue;
            var increment = stepParts.Length == 2 ? int.Parse(stepParts[1], CultureInfo.InvariantCulture) : 1;
            var range = stepParts[0]; var start = min; var end = max;
            if (range != "*")
            {
                var bounds = range.Split('-', 2);
                if (!Number(bounds[0], names, out start)) continue;
                end = stepParts.Length == 2 && bounds.Length == 1 ? max : start;
                if (bounds.Length == 2 && !Number(bounds[1], names, out end)) continue;
            }
            if (start < min || start > max || end < min || end > max) continue;
            foreach (var candidate in sunday && value == 0 ? new[] { 0, 7 } : new[] { value })
            {
                if (start <= end)
                {
                    if (start <= candidate && candidate <= end && (candidate - start) % increment == 0) return true;
                }
                else
                {
                    var offset = candidate >= start ? candidate - start
                        : candidate <= end ? (max - start + 1) + (candidate - min) : -1;
                    if (offset >= 0 && offset % increment == 0) return true;
                }
            }
        }
        return false;
    }

    private static bool Number(string source, Dictionary<string, int>? names, out int value) =>
        int.TryParse(source, NumberStyles.None, CultureInfo.InvariantCulture, out value) || (names?.TryGetValue(source, out value) ?? false);
}
'''
    return (template.replace("__NAMESPACE__", namespace)
            .replace("__DEFINITIONS__", definitions)
            .replace("__ANONYMOUS__", anonymous))


def _openapi_document(
    app: dict[str, Any], endpoints: list[dict[str, Any]], background_jobs: list[dict[str, Any]],
    entities: list[dict[str, Any]], authentication: str,
) -> dict[str, Any]:
    paths: dict[str, Any] = {
        "/healthz": {"get": {"operationId": "health", "responses": {"200": {"description": "Healthy"}}}},
        "/openapi.json": {"get": {"operationId": "openapi", "responses": {"200": {"description": "OpenAPI document"}}}},
    }
    has_async = bool(background_jobs)
    for endpoint in sorted(endpoints, key=lambda item: (str(item.get("path")), str(item.get("id")))):
        is_async = str(endpoint.get("mode") or "sync") == "async"; has_async |= is_async
        request_schema = endpoint.get("requestSchema") if isinstance(endpoint.get("requestSchema"), dict) and endpoint.get("requestSchema") else {"type": "object"}
        response_schema = endpoint.get("responseSchema") if isinstance(endpoint.get("responseSchema"), dict) and endpoint.get("responseSchema") else {"type": "object"}
        operation: dict[str, Any] = {
            "operationId": str(endpoint.get("id")),
            "requestBody": {"required": False, "content": {"application/json": {"schema": request_schema}}},
            "responses": {
                "202" if is_async else "200": {
                    "description": "Accepted" if is_async else "Workflow result",
                    **({} if is_async else {"content": {"application/json": {"schema": response_schema}}}),
                },
                "400": {"description": "Request schema validation failed"},
                "500": {"description": "Workflow or response schema validation failed"},
            },
        }
        if is_async and endpoint.get("responseSchema"):
            operation["x-workflow-response-schema"] = response_schema
        parameters = re.findall(r"\{([A-Za-z][A-Za-z0-9_]*)\}", str(endpoint.get("path") or ""))
        if parameters:
            operation["parameters"] = [
                {"name": name, "in": "path", "required": True, "schema": {"type": "string"}}
                for name in parameters
            ]
        if authentication == "api-key" and endpoint.get("authentication") != "anonymous": operation["security"] = [{"ApiKey": []}]
        else: operation["security"] = []
        paths[str(endpoint.get("path"))] = {"post": operation}
    if has_async:
        async_anonymous = [
            authentication == "none" or item.get("authentication") == "anonymous"
            for item in endpoints if str(item.get("mode") or "sync") == "async"
        ]
        if background_jobs:
            async_anonymous.append(authentication == "none")
        job_security = [] if all(async_anonymous) else ([{"ApiKey": []}] if not any(async_anonymous) else [{}, {"ApiKey": []}])
        job_parameter = [{"name": "jobId", "in": "path", "required": True, "schema": {"type": "string"}}]
        paths["/api/jobs/{jobId}"] = {
            "get": {"operationId": "jobStatus", "security": job_security, "parameters": job_parameter, "responses": {"200": {"description": "Job status"}, "404": {"description": "Not found"}}},
            "delete": {"operationId": "jobCancel", "security": job_security, "parameters": job_parameter, "responses": {"202": {"description": "Cancellation requested"}, "409": {"description": "Already finished"}}},
        }
        paths["/api/jobs/{jobId}/events"] = {"get": {"operationId": "jobEvents", "security": job_security, "parameters": job_parameter, "responses": {"200": {"description": "Server-sent events"}}}}
    if background_jobs:
        background_security = [] if authentication == "none" else [{"ApiKey": []}]
        paths["/api/background-jobs"] = {
            "get": {"operationId": "backgroundJobDefinitions", "security": background_security, "responses": {"200": {"description": "Background job definitions"}}},
        }
        paths["/api/background-jobs/{definitionId}/run"] = {
            "post": {
                "operationId": "backgroundJobRun", "security": background_security,
                "parameters": [{"name": "definitionId", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"202": {"description": "Accepted"}, "404": {"description": "Definition not found"}, "409": {"description": "Definition disabled"}},
            },
        }
    entity_security = [] if authentication == "none" else [{"ApiKey": []}]
    for entity in sorted(entities, key=lambda item: str(item.get("id"))):
        crud = entity.get("crud") if isinstance(entity.get("crud"), dict) else {}
        if not crud.get("enabled"):
            continue
        base_path = entity_base_path(entity)
        operations = set(crud.get("operations") or ["create", "read", "list", "update", "delete"])
        properties: dict[str, Any] = {
            "id": {"type": "string", "format": "uuid"},
            "createdAt": {"type": "string", "format": "date-time"},
            "updatedAt": {"type": "string", "format": "date-time"},
        }
        required = ["id", "createdAt", "updatedAt"]
        writable: dict[str, Any] = {}
        create_required: list[str] = []
        for field in entity.get("fields") or []:
            field_type = str(field.get("type"))
            schema: dict[str, Any] = {
                "type": {"integer": "integer", "number": "number", "boolean": "boolean", "json": ["object", "array", "string", "number", "integer", "boolean", "null"]}.get(field_type, "string"),
            }
            if field_type == "datetime": schema["format"] = "date-time"
            if field.get("maxLength") is not None: schema["maxLength"] = field["maxLength"]
            if field.get("nullable"):
                current_type = schema["type"]
                schema["type"] = [*current_type, "null"] if isinstance(current_type, list) else [current_type, "null"]
            properties[str(field["id"])] = schema
            writable[str(field["id"])] = schema
            if not field.get("nullable") and not field.get("hasDefault"): create_required.append(str(field["id"]))
        response_schema = {"type": "object", "properties": properties, "required": required, "additionalProperties": False}
        create_schema = {"type": "object", "properties": writable, "required": create_required, "additionalProperties": False}
        update_schema = {"type": "object", "properties": writable, "minProperties": 1, "additionalProperties": False}
        collection: dict[str, Any] = {}
        member: dict[str, Any] = {}
        if "list" in operations:
            collection["get"] = {"operationId": f"list{entity['id']}", "security": entity_security, "parameters": [
                {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 100}},
                {"name": "offset", "in": "query", "schema": {"type": "integer", "minimum": 0}},
                {"name": "filter", "in": "query", "description": "JSON array of generated field/operator/value filters (maximum 20)", "schema": {"type": "string", "maxLength": 16384}},
                {"name": "sort", "in": "query", "description": "JSON array of generated field/direction sort entries (maximum 3)", "schema": {"type": "string", "maxLength": 8192}},
            ], "responses": {"200": {"description": "Entity collection", "content": {"application/json": {"schema": {"type": "object", "properties": {"items": {"type": "array", "items": response_schema}, "limit": {"type": "integer"}, "offset": {"type": "integer"}}, "required": ["items", "limit", "offset"]}}}}, "400": {"description": "Invalid query options"}}}
        if "create" in operations:
            collection["post"] = {"operationId": f"create{entity['id']}", "security": entity_security, "requestBody": {"required": True, "content": {"application/json": {"schema": create_schema}}}, "responses": {"201": {"description": "Created", "content": {"application/json": {"schema": response_schema}}}, "400": {"description": "Validation failed"}, "409": {"description": "Constraint failed"}}}
        id_parameter = [{"name": "id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}}]
        if "read" in operations: member["get"] = {"operationId": f"read{entity['id']}", "security": entity_security, "parameters": id_parameter, "responses": {"200": {"description": "Entity", "content": {"application/json": {"schema": response_schema}}}, "404": {"description": "Not found"}}}
        if "update" in operations: member["patch"] = {"operationId": f"update{entity['id']}", "security": entity_security, "parameters": id_parameter, "requestBody": {"required": True, "content": {"application/json": {"schema": update_schema}}}, "responses": {"200": {"description": "Updated"}, "400": {"description": "Validation failed"}, "404": {"description": "Not found"}, "409": {"description": "Constraint failed"}}}
        if "delete" in operations: member["delete"] = {"operationId": f"delete{entity['id']}", "security": entity_security, "parameters": id_parameter, "responses": {"204": {"description": "Deleted"}, "404": {"description": "Not found"}, "409": {"description": "Still referenced"}}}
        if collection: paths[base_path] = collection
        if member: paths[base_path + "/{id}"] = member
    document: dict[str, Any] = {
        "openapi": "3.1.0", "info": {"title": str(app.get("displayName") or app.get("name") or "Generated API"), "version": "1.0.0"},
        "paths": paths,
    }
    if authentication == "api-key": document["components"] = {"securitySchemes": {"ApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}}
    return document


def _extensions(namespace: str) -> str:
    return f'''namespace {namespace}.Generated;

public static class ApiExtensions
{{
    // User-owned extension boundary. Map additional endpoints from a separate startup partial when needed.
}}
'''


def _appsettings(authentication: str) -> str:
    return json.dumps({"schemaVersion": 1, "authentication": authentication, "apiKeyEnvironment": "CONTROLDECK_APP_API_KEY"}, sort_keys=True, separators=(",", ":")) + "\n"


def _dockerfile(project_name: str) -> str:
    return f'''FROM mcr.microsoft.com/dotnet/aspnet:8.0
WORKDIR /app
COPY publish/ .
USER $APP_UID
ENV ASPNETCORE_HTTP_PORTS=8080
EXPOSE 8080
ENTRYPOINT ["dotnet", "{project_name}.dll"]
'''


def _test_project(project_name: str) -> str:
    return f'''<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net8.0</TargetFramework><ImplicitUsings>enable</ImplicitUsings><Nullable>enable</Nullable><Deterministic>true</Deterministic></PropertyGroup>
  <ItemGroup><ProjectReference Include="../../src/{project_name}/{project_name}.csproj" /></ItemGroup>
</Project>
'''


def _test_program(namespace: str) -> str:
    schema_json = _csharp_string(json.dumps({
        "type": "object", "required": ["message"],
        "properties": {"message": {"type": "string", "minLength": 1}},
        "additionalProperties": False,
    }, sort_keys=True, separators=(",", ":")))
    return f'''using System.Text.Json.Nodes;
using {namespace}.Generated;

if (string.IsNullOrWhiteSpace(GeneratedApplication.SpecChecksum)) throw new Exception("Spec checksum is missing");
GeneratedWorkflow.ValidateGeneratedSource();
var schema = JsonNode.Parse({schema_json})!;
if (GeneratedJsonSchema.Validate(new JsonObject {{ ["message"] = "ok" }}, schema).Count != 0) throw new Exception("Generated schema rejected valid input");
if (GeneratedJsonSchema.Validate(new JsonObject(), schema).Count == 0) throw new Exception("Generated schema accepted invalid input");
if (!GeneratedCron.Matches("5/10 22-2 * JAN MON", new DateTime(2026, 1, 5, 22, 5, 0))) throw new Exception("Generated cron rejected a valid wrapped range");
if (GeneratedCron.Matches("5/10 22-2 * JAN MON", new DateTime(2026, 1, 5, 22, 6, 0))) throw new Exception("Generated cron accepted an invalid minute");
Console.WriteLine("Generated ASP.NET source self-test passed");
'''


def _readme(project_name: str, authentication: str, has_entities: bool, has_pages: bool, workflow: dict[str, Any]) -> str:
    auth = "Set CONTROLDECK_APP_API_KEY. API clients send X-API-Key; generated GUI users exchange it for a 12-hour in-memory HttpOnly SameSite session." if authentication == "api-key" else "Endpoints are explicitly generated without authentication."
    entities = "- SQLite Entity: `application.sqlite3` in `CONTROLDECK_APP_DATA_DIR` (WAL, additive startup migration, parameterized CRUD, durable delete audit).\n" if has_entities else ""
    gui = "- Blazor GUI: static SSR with responsive generated CSS and schema-driven Entity list/create/update/delete controls when explicitly enabled.\n" if has_pages else ""
    gui_security_note = "GUI API-key sign-in is accepted over HTTPS, or plain HTTP only from a loopback client. Browser sessions are memory-only and expire on restart; terminate TLS before exposing the application to a network.\n" if has_pages and authentication == "api-key" else ""
    required_secrets = workflow.get("requiredSecrets") or workflow.get("required_secrets") or []
    secret_note = ""
    if required_secrets:
        variables = "\n".join(f"- `CONTROLDECK_SECRET_{index:03d}`: required Secret #{index}" for index in range(1, len(required_secrets) + 1))
        secret_note = f"Required Secret environment aliases (names intentionally omitted):\n{variables}\n"
    file_note = "Set `CONTROLDECK_APP_WORK_ROOT` to an existing application-owned directory for generated file nodes. Paths are contained and symlink traversal is rejected; writes emit a content-free audit record.\n" if any(
        str(node.get("nodeType") or node.get("node_type") or "").startswith("file.")
        for node in workflow.get("nodes") or [] if isinstance(node, dict)
    ) else ""
    side_effect_note = "Set `CONTROLDECK_APP_AUDIT_ROOT` (or the file work root) to an existing application-owned directory when the Workflow has side effects. HTTP audit records omit headers, body, query, response, and Secret values.\n" if (workflow.get("sideEffects") or workflow.get("side_effects")) else ""
    return f'''# {project_name} ASP.NET API

Deterministically generated by `{ASPNET_GENERATOR_ID}/{ASPNET_GENERATOR_VERSION}`.

- Health: `/healthz`
- OpenAPI 3.1: `/openapi.json` and `openapi.json`
- Async jobs: `/api/jobs/{{jobId}}` and SSE `/api/jobs/{{jobId}}/events`
- Authentication: {auth}
{entities}
{gui}
{gui_security_note}

```bash
dotnet run --project src/{project_name}/{project_name}.csproj
dotnet run --project tests/{project_name}.GeneratedTests/{project_name}.GeneratedTests.csproj
dotnet publish src/{project_name}/{project_name}.csproj -o publish
docker build -t {project_name.lower()} .
```

No Secret value is embedded. Set `CONTROLDECK_APP_DATA_DIR` to an application-owned writable directory for schedule or Entity persistence. Existing Entity columns are only migrated additively; incompatible type/nullability/relation changes stop startup instead of silently rewriting data. Generated files are manifest-managed; Extensions are user-owned.
{secret_note}{file_note}{side_effect_note}
'''
