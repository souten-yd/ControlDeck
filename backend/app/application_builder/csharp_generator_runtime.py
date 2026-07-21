from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any


def render_workflow_source(
    namespace: str, workflow: dict[str, Any], nodes: list[dict[str, Any]],
    csharp_string: Callable[[str], str],
) -> str:
    node_rows: list[str] = []
    for node in nodes:
        node_id = str(node.get("id") or "")
        node_type = str(node.get("nodeType") or node.get("node_type") or "")
        config = json.dumps(node.get("config") or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        execution = node.get("execution") if isinstance(node.get("execution"), dict) else {}
        retry_count = int(execution.get("retryCount", execution.get("retry_count", 0)) or 0)
        retry_wait = float(execution.get("retryWaitSeconds", execution.get("retry_wait_seconds", 0)) or 0)
        timeout = execution.get("timeoutSeconds", execution.get("timeout_seconds"))
        timeout_literal = "null" if timeout is None else _number(float(timeout))
        on_error = str(execution.get("onError", execution.get("on_error", "stop")) or "stop")
        join_mode = str(execution.get("joinMode", execution.get("join_mode", "first")) or "first")
        node_rows.append(
            f'            new GeneratedNode({csharp_string(node_id)}, {csharp_string(node_type)}, '
            f'JsonNode.Parse({csharp_string(config)}) as JsonObject ?? new JsonObject(), '
            f'{retry_count}, {_number(retry_wait)}, {timeout_literal}, '
            f'{csharp_string(on_error)}, {csharp_string(join_mode)})'
        )
    edge_rows: list[str] = []
    for edge in workflow.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("sourceNode") or edge.get("source_node") or "")
        target = str(edge.get("targetNode") or edge.get("target_node") or "")
        branch = edge.get("branch")
        edge_rows.append(
            f'            new GeneratedEdge({csharp_string(source)}, {csharp_string(target)}, '
            f'{"null" if branch is None else csharp_string(str(branch))})'
        )
    replacements = {
        "__CD_NAMESPACE__": namespace,
        "__CD_NODES__": ",\n".join(node_rows),
        "__CD_EDGES__": ",\n".join(edge_rows),
        "__CD_SECRET_COUNT__": str(len(workflow.get("requiredSecrets") or workflow.get("required_secrets") or [])),
    }
    return re.sub(r"__CD_(?:NAMESPACE|NODES|EDGES|SECRET_COUNT)__", lambda match: replacements[match.group(0)], _TEMPLATE)


def _number(value: float) -> str:
    if value != value or value in {float("inf"), float("-inf")}:
        return "0.0"
    text = json.dumps(value, ensure_ascii=True, allow_nan=False)
    return text if "." in text or "e" in text.lower() else f"{text}.0"


_TEMPLATE = r'''#nullable enable
using System.Collections.Concurrent;
using System.Globalization;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace __CD_NAMESPACE__.Generated;

public static class GeneratedWorkflow
{
    public static void ValidateGeneratedSource()
    {
        var nodes = Nodes(); var edges = Edges();
        if (nodes.Length == 0 || nodes.Count(item => item.Type == "trigger") != 1)
            throw new InvalidOperationException("Generated workflow requires exactly one trigger");
        if (nodes.Select(item => item.Id).Distinct(StringComparer.Ordinal).Count() != nodes.Length)
            throw new InvalidOperationException("Generated workflow node IDs must be unique");
        var ids = nodes.Select(item => item.Id).ToHashSet(StringComparer.Ordinal);
        if (edges.Any(edge => !ids.Contains(edge.Source) || !ids.Contains(edge.Target)))
            throw new InvalidOperationException("Generated workflow edge endpoint is missing");
    }

    public static Task<JsonObject> RunAsync(JsonObject input, CancellationToken cancellationToken = default)
    {
        ValidateGeneratedSource();
        return GeneratedRunner.RunAsync(Nodes(), Edges(), input, cancellationToken);
    }

    private static GeneratedNode[] Nodes() => new GeneratedNode[]
        {
__CD_NODES__
        };
    private static GeneratedEdge[] Edges() => new GeneratedEdge[]
        {
__CD_EDGES__
        };
}

internal enum GeneratedStatus { Succeeded, Failed, TimedOut, Skipped }
internal sealed record GeneratedNode(string Id, string Type, JsonObject Config, int RetryCount, double RetryWaitSeconds, double? TimeoutSeconds, string OnError, string JoinMode);
internal sealed record GeneratedEdge(string Source, string Target, string? Branch);
internal sealed record GeneratedOutcome(GeneratedStatus Status, JsonObject Output, string Error = "", int Attempts = 1);

internal static class GeneratedSecrets
{
    private const int SecretCount = __CD_SECRET_COUNT__;
    private const int MaxSecretBytes = 64 * 1024;

    internal static string Resolve(string alias)
    {
        if (!Regex.IsMatch(alias, @"^SECRET_[0-9]{3}$", RegexOptions.CultureInvariant))
            throw new InvalidOperationException("Generated Secret alias is invalid");
        if (!int.TryParse(alias.AsSpan(7), NumberStyles.None, CultureInfo.InvariantCulture, out var index) || index < 1 || index > SecretCount)
            throw new InvalidOperationException("Generated Secret alias is not declared");
        var value = Environment.GetEnvironmentVariable("CONTROLDECK_" + alias);
        if (string.IsNullOrEmpty(value)) throw new InvalidOperationException($"Required environment variable CONTROLDECK_{alias} is missing");
        if (Encoding.UTF8.GetByteCount(value) > MaxSecretBytes) throw new InvalidOperationException("Generated Secret exceeds the 64 KiB limit");
        return value;
    }

    internal static JsonObject Redact(JsonObject source)
    {
        var values = Values();
        return RedactNode(source, values) as JsonObject ?? new JsonObject();
    }

    internal static string RedactText(string text)
    {
        foreach (var secret in Values()) text = text.Replace(secret, "***", StringComparison.Ordinal);
        return text;
    }

    private static string[] Values() => Enumerable.Range(1, SecretCount)
        .Select(index => Environment.GetEnvironmentVariable($"CONTROLDECK_SECRET_{index:000}"))
        .Where(value => !string.IsNullOrEmpty(value)).Cast<string>().Distinct(StringComparer.Ordinal)
        .OrderByDescending(value => value.Length).ToArray();

    private static JsonNode? RedactNode(JsonNode? value, IReadOnlyList<string> secrets)
    {
        if (value is JsonObject objectValue)
        {
            var result = new JsonObject();
            foreach (var pair in objectValue) result[pair.Key] = RedactNode(pair.Value, secrets);
            return result;
        }
        if (value is JsonArray arrayValue) return new JsonArray(arrayValue.Select(item => RedactNode(item, secrets)).ToArray());
        if (value is JsonValue scalar && scalar.TryGetValue<string>(out var text))
        {
            foreach (var secret in secrets) text = text.Replace(secret, "***", StringComparison.Ordinal);
            return JsonValue.Create(text);
        }
        return value?.DeepClone();
    }
}

internal static class GeneratedSideEffectAudit
{
    private static readonly object Gate = new();

    internal static void Record(string action, string resource, long bytes, string result)
    {
        var rootRaw = Environment.GetEnvironmentVariable("CONTROLDECK_APP_AUDIT_ROOT") ?? Environment.GetEnvironmentVariable("CONTROLDECK_APP_WORK_ROOT");
        if (string.IsNullOrWhiteSpace(rootRaw)) throw new InvalidOperationException("CONTROLDECK_APP_AUDIT_ROOT or CONTROLDECK_APP_WORK_ROOT is required for generated side-effect audit");
        var rootInfo = new DirectoryInfo(Path.GetFullPath(rootRaw));
        var root = Path.GetFullPath((rootInfo.LinkTarget is null ? rootInfo : rootInfo.ResolveLinkTarget(true) as DirectoryInfo ?? rootInfo).FullName);
        var path = Path.Combine(root, ".controldeck-side-effects.audit.jsonl");
        var previous = path + ".1";
        if ((File.Exists(path) && new FileInfo(path).LinkTarget is not null) || (File.Exists(previous) && new FileInfo(previous).LinkTarget is not null))
            throw new InvalidOperationException("Generated audit path cannot be a symbolic link");
        var line = JsonSerializer.Serialize(new { timestamp = DateTimeOffset.UtcNow, action, resource, bytes, result });
        lock (Gate)
        {
            if (File.Exists(path) && new FileInfo(path).Length > 2 * 1024 * 1024)
            {
                if (File.Exists(previous)) File.Delete(previous);
                File.Move(path, previous);
            }
            File.AppendAllText(path, line + "\n", new UTF8Encoding(false));
        }
    }
}

internal static class GeneratedRunner
{
    internal static async Task<JsonObject> RunAsync(
        IReadOnlyList<GeneratedNode> nodes, IReadOnlyList<GeneratedEdge> edges,
        JsonObject input, CancellationToken cancellationToken)
    {
        var outcomes = new ConcurrentDictionary<string, GeneratedOutcome>(StringComparer.Ordinal);
        var variables = new ConcurrentDictionary<string, JsonObject>(StringComparer.Ordinal);
        using var cancellation = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        using var parallel = new SemaphoreSlim(4, 4);
        await RunGraphAsync(
            nodes, edges, input, outcomes, variables,
            nodes.Where(item => item.Type == "trigger").Select(item => item.Id).ToArray(),
            parallel, cancellation);
        return GeneratedSecrets.Redact(GeneratedNodes.Collect(nodes, outcomes));
    }

    private static async Task RunGraphAsync(
        IReadOnlyList<GeneratedNode> nodes, IReadOnlyList<GeneratedEdge> edges, JsonObject input,
        ConcurrentDictionary<string, GeneratedOutcome> outcomes,
        ConcurrentDictionary<string, JsonObject> variables, IReadOnlyList<string> roots,
        SemaphoreSlim parallel, CancellationTokenSource cancellation)
    {
        var byId = nodes.ToDictionary(item => item.Id, StringComparer.Ordinal);
        var order = nodes.Select((item, index) => (item.Id, index)).ToDictionary(item => item.Id, item => item.index, StringComparer.Ordinal);
        var incoming = nodes.ToDictionary(item => item.Id, item => edges.Where(edge => edge.Target == item.Id).ToArray(), StringComparer.Ordinal);
        var outgoing = nodes.ToDictionary(item => item.Id, item => edges.Where(edge => edge.Source == item.Id).ToArray(), StringComparer.Ordinal);
        var received = nodes.ToDictionary(item => item.Id, _ => 0, StringComparer.Ordinal);
        var liveReceived = nodes.ToDictionary(item => item.Id, _ => 0, StringComparer.Ordinal);
        var arrivals = nodes.ToDictionary(item => item.Id, _ => new List<string>(), StringComparer.Ordinal);
        var successfulArrivals = nodes.ToDictionary(item => item.Id, _ => new List<string>(), StringComparer.Ordinal);
        var ran = new HashSet<string>(StringComparer.Ordinal);
        var running = new Dictionary<string, Task<GeneratedOutcome>>(StringComparer.Ordinal);

        void Schedule(GeneratedNode node, IReadOnlyList<string> sourceIds)
        {
            if (!ran.Add(node.Id)) return;
            var config = node.Config.DeepClone() as JsonObject ?? new JsonObject();
            if (node.Type == "control.merge")
                config["__merge_source_ids"] = new JsonArray(sourceIds.Select(id => (JsonNode?)JsonValue.Create(id)).ToArray());
            var scheduled = node with { Config = config };
            running[node.Id] = node.Type == "control.loop"
                ? ExecuteLoopAsync(scheduled, nodes, edges, outgoing[node.Id], input, outcomes, variables, parallel, cancellation)
                : GeneratedNodes.ExecuteWithPolicyAsync(scheduled, outcomes, variables, input, parallel, cancellation.Token);
        }

        void MarkSkipped(string nodeId)
        {
            if (!ran.Add(nodeId)) return;
            outcomes[nodeId] = new GeneratedOutcome(GeneratedStatus.Skipped, new JsonObject());
            foreach (var edge in outgoing[nodeId]) Receive(edge, false);
        }

        void Receive(GeneratedEdge edge, bool live)
        {
            var target = byId[edge.Target];
            received[target.Id] += 1;
            if (live)
            {
                liveReceived[target.Id] += 1;
                if (!arrivals[target.Id].Contains(edge.Source, StringComparer.Ordinal)) arrivals[target.Id].Add(edge.Source);
                if (outcomes.TryGetValue(edge.Source, out var source) && source.Status == GeneratedStatus.Succeeded &&
                    !successfulArrivals[target.Id].Contains(edge.Source, StringComparer.Ordinal)) successfulArrivals[target.Id].Add(edge.Source);
            }
            if (ran.Contains(target.Id)) return;
            var resolved = received[target.Id] >= incoming[target.Id].Length;
            var lives = liveReceived[target.Id];
            var successes = successfulArrivals[target.Id].Count;
            var mergeMode = target.Type == "control.merge" ? (target.Config["mode"]?.GetValue<string>() ?? "wait_all") : "";
            var joinAll = target.JoinMode == "all" || mergeMode is "wait_all" or "collect";
            bool? shouldRun = null;
            if (mergeMode == "first_success")
            {
                if (successes >= 1 || resolved) shouldRun = true;
            }
            else if (mergeMode == "quorum")
            {
                var quorum = Math.Clamp(target.Config["quorum"]?.GetValue<int>() ?? 1, 1, Math.Max(1, incoming[target.Id].Length));
                if (successes >= quorum || resolved) shouldRun = true;
            }
            else if (joinAll)
            {
                if (resolved) shouldRun = lives > 0;
            }
            else if (live) shouldRun = true;
            else if (resolved && lives == 0) shouldRun = false;
            if (shouldRun is null) return;
            if (shouldRun == false) { MarkSkipped(target.Id); return; }
            IReadOnlyList<string> sources = mergeMode switch
            {
                "wait_all" or "collect" => incoming[target.Id].Select(item => item.Source).ToArray(),
                "first_success" or "quorum" => successfulArrivals[target.Id].ToArray(),
                _ => arrivals[target.Id].ToArray(),
            };
            Schedule(target, sources);
        }

        foreach (var root in roots)
            if (byId.TryGetValue(root, out var node)) Schedule(node, Array.Empty<string>());
        while (running.Count > 0)
        {
            await Task.WhenAny(running.Values);
            var completed = running.Where(item => item.Value.IsCompleted).OrderBy(item => order[item.Key]).ToArray();
            foreach (var pair in completed)
            {
                running.Remove(pair.Key);
                GeneratedOutcome outcome;
                try { outcome = await pair.Value; }
                catch
                {
                    cancellation.Cancel();
                    try { await Task.WhenAll(running.Values); } catch { }
                    throw;
                }
                outcomes[pair.Key] = outcome;
                var node = byId[pair.Key];
                var outputVariable = node.Config["output_var"]?.GetValue<string>()?.Trim() ?? "";
                if (outcome.Status == GeneratedStatus.Succeeded && outputVariable.Length > 0)
                    variables[outputVariable] = outcome.Output.DeepClone() as JsonObject ?? new JsonObject();
                var failed = outcome.Status is GeneratedStatus.Failed or GeneratedStatus.TimedOut;
                var timeoutRoute = outgoing[pair.Key].Any(edge => edge.Branch == "timeout");
                foreach (var edge in outgoing[pair.Key])
                {
                    if (node.Type == "control.loop" && edge.Branch == "body") continue;
                    bool live;
                    if (node.Type == "condition.if" && !failed)
                    {
                        var branch = outcome.Output["result"]?.GetValue<bool>() == true ? "true" : "false";
                        live = (edge.Branch ?? "true") == branch;
                    }
                    else if (failed && node.OnError == "branch")
                    {
                        var failure = outcome.Status == GeneratedStatus.TimedOut ? "timeout" : "error";
                        live = edge.Branch == failure || (failure == "timeout" && !timeoutRoute && edge.Branch == "error");
                    }
                    else if (node.Type == "control.loop") live = edge.Branch is not "error" and not "timeout";
                    else live = edge.Branch is not "error" and not "timeout";
                    Receive(edge, live);
                }
            }
        }
    }

    private sealed record LoopIteration(
        JsonObject Result, ConcurrentDictionary<string, GeneratedOutcome> Outcomes,
        ConcurrentDictionary<string, JsonObject> Variables);

    private static async Task<GeneratedOutcome> ExecuteLoopAsync(
        GeneratedNode loop, IReadOnlyList<GeneratedNode> nodes, IReadOnlyList<GeneratedEdge> edges,
        IReadOnlyList<GeneratedEdge> loopEdges, JsonObject input,
        ConcurrentDictionary<string, GeneratedOutcome> parentOutcomes,
        ConcurrentDictionary<string, JsonObject> parentVariables,
        SemaphoreSlim parallel, CancellationTokenSource cancellation)
    {
        var mode = loop.Config["mode"]?.GetValue<string>() ?? "count";
        var items = new List<JsonNode?>();
        if (mode == "foreach")
        {
            var raw = GeneratedNodes.Render(GeneratedNodes.NodeText(loop.Config["items"]), parentOutcomes, parentVariables).Trim();
            try
            {
                var parsed = JsonNode.Parse(raw);
                if (parsed is JsonArray array) items.AddRange(array.Select(item => item?.DeepClone()));
                else items.Add(parsed);
            }
            catch (JsonException)
            {
                items.AddRange(raw.Replace("\r\n", "\n", StringComparison.Ordinal).Split('\n')
                    .Where(line => line.Trim().Length > 0).Select(line => (JsonNode?)JsonValue.Create(line)));
            }
        }
        else if (mode == "count")
        {
            var count = Math.Clamp(GeneratedNodes.Integer(loop.Config["count"], 1), 1, 100);
            for (var index = 0; index < count; index++) items.Add(JsonValue.Create(index));
        }
        else throw new NotSupportedException($"Generated loop mode is not supported: {mode}");
        if (items.Count > 100) items = items.Take(100).ToList();

        var bodyRoots = loopEdges.Where(edge => edge.Branch == "body").Select(edge => edge.Target).ToArray();
        var loopParallel = Math.Clamp(GeneratedNodes.Integer(loop.Config["parallel"], 1), 1, 5);

        async Task<LoopIteration> RunIterationAsync(int index, JsonNode? item)
        {
            var iterationOutcomes = new ConcurrentDictionary<string, GeneratedOutcome>(parentOutcomes, StringComparer.Ordinal);
            var iterationVariables = new ConcurrentDictionary<string, JsonObject>(
                parentVariables.ToDictionary(pair => pair.Key, pair => pair.Value.DeepClone() as JsonObject ?? new JsonObject(), StringComparer.Ordinal),
                StringComparer.Ordinal);
            iterationOutcomes[loop.Id] = new GeneratedOutcome(GeneratedStatus.Succeeded, new JsonObject
            {
                ["index"] = index, ["item"] = item?.DeepClone(), ["total"] = items.Count,
            });
            await RunGraphAsync(nodes, edges, input, iterationOutcomes, iterationVariables, bodyRoots, parallel, cancellation);
            var outputs = new JsonObject();
            foreach (var pair in iterationOutcomes.OrderBy(pair => pair.Key, StringComparer.Ordinal))
            {
                if (pair.Key == loop.Id || !parentOutcomes.TryGetValue(pair.Key, out var original) || !ReferenceEquals(pair.Value, original))
                    outputs[pair.Key] = pair.Value.Output.DeepClone();
            }
            return new LoopIteration(new JsonObject
            {
                ["index"] = index, ["item"] = item?.DeepClone(), ["outputs"] = outputs,
            }, iterationOutcomes, iterationVariables);
        }

        var completed = new List<LoopIteration>();
        for (var start = 0; start < items.Count; start += loopParallel)
        {
            var batch = Enumerable.Range(start, Math.Min(loopParallel, items.Count - start))
                .Select(index => RunIterationAsync(index, items[index]));
            completed.AddRange(await Task.WhenAll(batch));
        }
        if (completed.Count > 0)
        {
            var last = completed[^1];
            foreach (var pair in last.Outcomes)
                if (pair.Key != loop.Id) parentOutcomes[pair.Key] = pair.Value;
            parentVariables.Clear();
            foreach (var pair in last.Variables) parentVariables[pair.Key] = pair.Value.DeepClone() as JsonObject ?? new JsonObject();
        }
        var results = new JsonArray(completed.Select(item => (JsonNode?)item.Result.DeepClone()).ToArray());
        return new GeneratedOutcome(GeneratedStatus.Succeeded, new JsonObject
        {
            ["index"] = items.Count - 1, ["item"] = items.Count > 0 ? items[^1]?.DeepClone() : null,
            ["total"] = items.Count, ["done"] = true, ["results"] = results,
        });
    }
}

internal static partial class GeneratedNodes
{
    private static readonly Regex Reference = new(@"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}", RegexOptions.CultureInvariant);

    internal static async Task<GeneratedOutcome> ExecuteWithPolicyAsync(
        GeneratedNode node, IReadOnlyDictionary<string, GeneratedOutcome> context,
        IReadOnlyDictionary<string, JsonObject> variables, JsonObject input,
        SemaphoreSlim parallel, CancellationToken cancellationToken)
    {
        var retries = Math.Clamp(node.RetryCount, 0, 5);
        var retryWait = Math.Clamp(node.RetryWaitSeconds, 0, 300);
        var timeout = Math.Clamp(node.TimeoutSeconds ?? (node.Type == "util.wait" ? 3700 : 120), 0.1, 7200);
        for (var attempt = 1; ; attempt++)
        {
            using var attemptCancellation = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            attemptCancellation.CancelAfter(TimeSpan.FromSeconds(timeout));
            try
            {
                Task<JsonObject> execution;
                if (node.Type is "trigger" or "util.wait") execution = ExecuteAsync(node.Type, node.Config, context, variables, input, attemptCancellation.Token);
                else
                {
                    await parallel.WaitAsync(attemptCancellation.Token);
                    execution = ExecuteMeteredAsync(node, context, variables, input, parallel, attemptCancellation.Token);
                }
                var output = await execution;
                return new GeneratedOutcome(GeneratedStatus.Succeeded, output, Attempts: attempt);
            }
            catch (OperationCanceledException exception) when (!cancellationToken.IsCancellationRequested)
            {
                if (attempt <= retries) { await Task.Delay(TimeSpan.FromSeconds(retryWait), cancellationToken); continue; }
                var failed = new GeneratedOutcome(GeneratedStatus.TimedOut, new JsonObject { ["error"] = "Node timeout" }, exception.Message, attempt);
                if (node.OnError == "stop") throw new InvalidOperationException($"Generated node '{node.Id}' timed out", exception);
                return failed;
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested) { throw; }
            catch (Exception exception)
            {
                if (attempt <= retries) { await Task.Delay(TimeSpan.FromSeconds(retryWait), cancellationToken); continue; }
                var failed = new GeneratedOutcome(GeneratedStatus.Failed, new JsonObject { ["error"] = exception.Message }, exception.Message, attempt);
                if (node.OnError == "stop") throw new InvalidOperationException($"Generated node '{node.Id}' failed", exception);
                return failed;
            }
        }
    }

    private static async Task<JsonObject> ExecuteMeteredAsync(
        GeneratedNode node, IReadOnlyDictionary<string, GeneratedOutcome> context,
        IReadOnlyDictionary<string, JsonObject> variables, JsonObject input,
        SemaphoreSlim parallel, CancellationToken cancellationToken)
    {
        try { return await ExecuteAsync(node.Type, node.Config, context, variables, input, cancellationToken); }
        finally { parallel.Release(); }
    }

    private static async Task<JsonObject> ExecuteAsync(
        string type, JsonObject config, IReadOnlyDictionary<string, GeneratedOutcome> context,
        IReadOnlyDictionary<string, JsonObject> variables,
        JsonObject input, CancellationToken cancellationToken)
    {
        switch (type)
        {
            case "trigger":
                var trigger = input.DeepClone() as JsonObject ?? new JsonObject();
                trigger["ok"] = true;
                trigger["message"] ??= "";
                return trigger;
            case "condition.if": return Condition(config, context, variables);
            case "control.merge": return Merge(config, context);
            case "util.wait":
                var seconds = Math.Clamp(config["seconds"]?.GetValue<double>() ?? 1, 0, 3600);
                await Task.Delay(TimeSpan.FromSeconds(seconds), cancellationToken);
                return new JsonObject { ["waited_seconds"] = seconds };
            case "util.now":
                var now = DateTimeOffset.Now;
                var format = config["format"]?.GetValue<string>() ?? "%Y-%m-%d %H:%M:%S";
                format = format.Replace("%Y", "yyyy").Replace("%m", "MM").Replace("%d", "dd").Replace("%H", "HH").Replace("%M", "mm").Replace("%S", "ss");
                return new JsonObject { ["text"] = now.ToString(format, CultureInfo.InvariantCulture), ["iso"] = now.ToString("O", CultureInfo.InvariantCulture), ["date"] = now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture), ["time"] = now.ToString("HH:mm:ss", CultureInfo.InvariantCulture), ["timestamp"] = now.ToUnixTimeSeconds() };
            case "var.set": return new JsonObject { ["name"] = config["name"]?.GetValue<string>() ?? "value", ["value"] = Render(NodeText(config["value"]), context, variables) };
            case "string.op": return StringOperation(config, context, variables);
            case "data.transform": return DataTransform(config, context, variables);
            case "data.template": return DataTemplate(config, context, variables);
            case "data.filter": return DataFilter(config, context, variables);
            case "data.aggregate": return DataAggregate(config, context, variables);
            case "file.read": return await FileReadAsync(config, context, variables, cancellationToken);
            case "file.write": return await FileWriteAsync(config, context, variables, cancellationToken);
            case "file.exists": return FileExists(config, context, variables);
            case "file.glob": return FileGlob(config, context, variables);
            case "http.request": return await HttpRequestAsync(config, context, variables, cancellationToken);
            case "output.render": return Output(config, context, variables);
            case "signal.display":
                var name = config["name"]?.GetValue<string>() ?? config["signal"]?.GetValue<string>() ?? "output";
                return new JsonObject { ["display"] = true, ["name"] = name, ["signal"] = name, ["renderer"] = config["renderer"]?.GetValue<string>() ?? "auto", ["value"] = Render(NodeText(config["value"]), context, variables), ["title"] = Render(NodeText(config["title"]), context, variables) };
            default: throw new NotSupportedException($"Generated node is not supported: {type}");
        }
    }

    private static readonly HttpClient GeneratedHttp = new(new HttpClientHandler
    {
        AllowAutoRedirect = false,
        AutomaticDecompression = System.Net.DecompressionMethods.GZip | System.Net.DecompressionMethods.Deflate,
        UseCookies = false,
    }) { Timeout = Timeout.InfiniteTimeSpan };
    private static readonly HashSet<string> RestrictedHeaders = new(StringComparer.OrdinalIgnoreCase)
    {
        "Host", "Content-Length", "Transfer-Encoding", "Connection", "Proxy-Connection", "Keep-Alive", "Upgrade",
    };
    private const int MaxHttpBodyBytes = 2 * 1024 * 1024;
    private const int MaxHttpResponseBytes = 4 * 1024 * 1024;

    private static async Task<JsonObject> HttpRequestAsync(
        JsonObject config, IReadOnlyDictionary<string, GeneratedOutcome> context,
        IReadOnlyDictionary<string, JsonObject> variables, CancellationToken cancellationToken)
    {
        var rawUrl = NodeText(config["url"]);
        if (rawUrl.Length is < 1 or > 2048 || rawUrl.Contains("{{", StringComparison.Ordinal) ||
            !Uri.TryCreate(rawUrl, UriKind.Absolute, out var uri) || uri.UserInfo.Length > 0)
            throw new InvalidOperationException("Generated HTTP URL is invalid");
        var loopback = uri.IsLoopback && uri.Scheme == Uri.UriSchemeHttp;
        if (uri.Scheme != Uri.UriSchemeHttps && !loopback)
            throw new InvalidOperationException("Generated HTTP requests require HTTPS or loopback HTTP");
        var method = (config["method"]?.GetValue<string>() ?? "GET").ToUpperInvariant();
        if (method is not ("GET" or "POST" or "PUT" or "PATCH" or "DELETE" or "HEAD"))
            throw new InvalidOperationException("Generated HTTP method is not supported");
        using var request = new HttpRequestMessage(new HttpMethod(method), uri);
        var body = Render(NodeText(config["body"]), context, variables);
        if (Encoding.UTF8.GetByteCount(body) > MaxHttpBodyBytes) throw new InvalidOperationException("Generated HTTP body exceeds 2 MiB");
        if (body.Length > 0) request.Content = new StringContent(body, Encoding.UTF8, "application/json");
        foreach (var (name, value) in HttpHeaders(config["headers"], context, variables))
        {
            if (RestrictedHeaders.Contains(name) || name.Length is < 1 or > 128 || name.Any(character => character <= 32 || character >= 127) ||
                value.Contains('\r') || value.Contains('\n') || Encoding.UTF8.GetByteCount(value) > 8192)
                throw new InvalidOperationException("Generated HTTP header is invalid");
            if (name.Equals("Content-Type", StringComparison.OrdinalIgnoreCase))
            {
                if (request.Content is null) request.Content = new ByteArrayContent(Array.Empty<byte>());
                request.Content.Headers.ContentType = MediaTypeHeaderValue.Parse(value);
            }
            else if (!request.Headers.TryAddWithoutValidation(name, value) &&
                     (request.Content is null || !request.Content.Headers.TryAddWithoutValidation(name, value)))
                throw new InvalidOperationException("Generated HTTP header is unsupported");
        }
        GeneratedSideEffectAudit.Record("http.request", $"{method} {uri.GetLeftPart(UriPartial.Authority)}", Encoding.UTF8.GetByteCount(body), "attempt");
        using var response = await GeneratedHttp.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
        var responseBody = await ReadLimitedAsync(response.Content, MaxHttpResponseBytes, cancellationToken);
        GeneratedSideEffectAudit.Record("http.request", $"{method} {uri.GetLeftPart(UriPartial.Authority)}", Encoding.UTF8.GetByteCount(body), $"http-{(int)response.StatusCode}");
        var expectedRaw = config["expected_status"] ?? config["expect_status"];
        var expected = expectedRaw is null ? 0 : Integer(expectedRaw, 0);
        var ok = expected > 0 ? (int)response.StatusCode == expected : (int)response.StatusCode < 400;
        if (expected > 0 && !ok) throw new InvalidOperationException($"Generated HTTP response status did not match {expected}");
        return new JsonObject { ["status_code"] = (int)response.StatusCode, ["ok"] = ok, ["body"] = responseBody };
    }

    private static IEnumerable<(string Name, string Value)> HttpHeaders(
        JsonNode? raw, IReadOnlyDictionary<string, GeneratedOutcome> context,
        IReadOnlyDictionary<string, JsonObject> variables)
    {
        if (raw is JsonObject objectValue)
        {
            foreach (var pair in objectValue)
                yield return (pair.Key, Render(NodeText(pair.Value), context, variables));
            yield break;
        }
        foreach (var line in NodeText(raw).Replace("\r\n", "\n", StringComparison.Ordinal).Split('\n'))
        {
            if (line.Trim().Length == 0) continue;
            var separator = line.IndexOf(':');
            if (separator <= 0) throw new InvalidOperationException("Generated HTTP header line is invalid");
            yield return (line[..separator].Trim(), Render(line[(separator + 1)..].Trim(), context, variables));
        }
    }

    private static async Task<string> ReadLimitedAsync(HttpContent content, int limit, CancellationToken cancellationToken)
    {
        await using var source = await content.ReadAsStreamAsync(cancellationToken);
        using var target = new MemoryStream();
        var buffer = new byte[16 * 1024];
        while (true)
        {
            var read = await source.ReadAsync(buffer.AsMemory(), cancellationToken);
            if (read == 0) break;
            if (target.Length + read > limit) throw new InvalidOperationException("Generated HTTP response exceeds 4 MiB");
            await target.WriteAsync(buffer.AsMemory(0, read), cancellationToken);
        }
        return Encoding.UTF8.GetString(target.ToArray());
    }

    private static async Task<JsonObject> FileReadAsync(
        JsonObject config, IReadOnlyDictionary<string, GeneratedOutcome> context,
        IReadOnlyDictionary<string, JsonObject> variables, CancellationToken cancellationToken)
    {
        var resolved = ResolveWorkPath(Render(NodeText(config["path"]), context, variables), allowRoot: false);
        if (!File.Exists(resolved.FullPath)) throw new FileNotFoundException("Generated file was not found");
        var info = new FileInfo(resolved.FullPath);
        if (info.Length > MaxDataBytes) throw new InvalidOperationException("Generated file exceeds 2 MiB");
        var content = await File.ReadAllTextAsync(resolved.FullPath, Encoding.UTF8, cancellationToken);
        return new JsonObject { ["content"] = content, ["path"] = resolved.RelativePath };
    }

    private static async Task<JsonObject> FileWriteAsync(
        JsonObject config, IReadOnlyDictionary<string, GeneratedOutcome> context,
        IReadOnlyDictionary<string, JsonObject> variables, CancellationToken cancellationToken)
    {
        var resolved = ResolveWorkPath(Render(NodeText(config["path"]), context, variables), allowRoot: false);
        var parent = Path.GetDirectoryName(resolved.FullPath) ?? throw new InvalidOperationException("Generated file parent is missing");
        if (!Directory.Exists(parent)) throw new DirectoryNotFoundException("Generated file parent does not exist");
        var content = GeneratedSecrets.RedactText(Render(NodeText(config["content"]), context, variables));
        var bytes = Encoding.UTF8.GetBytes(content);
        if (bytes.Length > MaxDataBytes) throw new InvalidOperationException("Generated file content exceeds 2 MiB");
        var append = config["append"]?.GetValue<bool>() ?? false;
        try
        {
            if (append)
            {
                var existing = File.Exists(resolved.FullPath) ? new FileInfo(resolved.FullPath).Length : 0;
                if (existing + bytes.Length > 4 * 1024 * 1024) throw new InvalidOperationException("Generated appended file exceeds 4 MiB");
                await using var stream = new FileStream(resolved.FullPath, FileMode.Append, FileAccess.Write, FileShare.None, 16 * 1024, true);
                await stream.WriteAsync(bytes.AsMemory(), cancellationToken);
                await stream.FlushAsync(cancellationToken);
            }
            else
            {
                var temporary = Path.Combine(parent, $".controldeck-write-{Guid.NewGuid():N}.tmp");
                try
                {
                    await File.WriteAllBytesAsync(temporary, bytes, cancellationToken);
                    File.Move(temporary, resolved.FullPath, overwrite: true);
                }
                finally { if (File.Exists(temporary)) File.Delete(temporary); }
            }
            GeneratedSideEffectAudit.Record("file.write", resolved.RelativePath, bytes.Length, "success");
        }
        catch
        {
            GeneratedSideEffectAudit.Record("file.write", resolved.RelativePath, bytes.Length, "failed");
            throw;
        }
        return new JsonObject { ["path"] = resolved.RelativePath, ["bytes"] = bytes.Length };
    }

    private static JsonObject FileExists(
        JsonObject config, IReadOnlyDictionary<string, GeneratedOutcome> context,
        IReadOnlyDictionary<string, JsonObject> variables)
    {
        var resolved = ResolveWorkPath(Render(NodeText(config["path"]), context, variables), allowRoot: false);
        if (File.Exists(resolved.FullPath)) return new JsonObject { ["exists"] = true, ["is_dir"] = false, ["size"] = new FileInfo(resolved.FullPath).Length };
        if (Directory.Exists(resolved.FullPath)) return new JsonObject { ["exists"] = true, ["is_dir"] = true, ["size"] = 0 };
        return new JsonObject { ["exists"] = false };
    }

    private static JsonObject FileGlob(
        JsonObject config, IReadOnlyDictionary<string, GeneratedOutcome> context,
        IReadOnlyDictionary<string, JsonObject> variables)
    {
        var resolved = ResolveWorkPath(Render(NodeText(config["base_path"]), context, variables), allowRoot: true);
        if (!Directory.Exists(resolved.FullPath)) throw new DirectoryNotFoundException("Generated glob base does not exist");
        var pattern = NodeText(config["pattern"] ?? JsonValue.Create("*"));
        ValidateGlobPattern(pattern);
        var recursive = config["recursive"]?.GetValue<bool>() ?? false;
        var kind = config["kind"]?.GetValue<string>() ?? "all";
        if (kind is not ("all" or "files" or "directories")) throw new InvalidOperationException("Generated glob kind is invalid");
        var limit = Math.Clamp(Integer(config["limit"], 100), 1, 1000);
        var pending = new Stack<string>(); pending.Push(resolved.FullPath);
        var matches = new List<JsonObject>(); var scanned = 0;
        while (pending.Count > 0 && matches.Count < limit)
        {
            var directory = pending.Pop();
            foreach (var candidate in Directory.EnumerateFileSystemEntries(directory).OrderBy(item => item, StringComparer.Ordinal))
            {
                if (++scanned > 100_000) throw new InvalidOperationException("Generated glob scan exceeds 100000 entries");
                var safe = ResolveExistingWorkPath(candidate);
                var isDirectory = Directory.Exists(safe.FullPath);
                var relativeToBase = Path.GetRelativePath(resolved.FullPath, safe.FullPath).Replace('\\', '/');
                if (recursive && isDirectory) pending.Push(safe.FullPath);
                if ((kind == "files" && isDirectory) || (kind == "directories" && !isDirectory)) continue;
                if (!System.IO.Enumeration.FileSystemName.MatchesSimpleExpression(pattern, relativeToBase, ignoreCase: OperatingSystem.IsWindows())) continue;
                var size = isDirectory ? 0 : new FileInfo(safe.FullPath).Length;
                matches.Add(new JsonObject { ["path"] = safe.RelativePath, ["relative_path"] = relativeToBase, ["name"] = Path.GetFileName(safe.FullPath), ["size"] = size, ["is_dir"] = isDirectory });
                if (matches.Count >= limit) break;
            }
            if (!recursive) break;
        }
        matches.Sort((left, right) => StringComparer.Ordinal.Compare(NodeText(left["path"]), NodeText(right["path"])));
        var rows = new JsonArray(matches.Select(item => (JsonNode?)item).ToArray());
        return new JsonObject { ["matches"] = rows, ["paths"] = new JsonArray(matches.Select(item => (JsonNode?)JsonValue.Create(NodeText(item["path"]))).ToArray()), ["count"] = matches.Count };
    }

    private sealed record GeneratedWorkPath(string FullPath, string RelativePath);

    private static GeneratedWorkPath ResolveWorkPath(string relative, bool allowRoot)
    {
        if (string.IsNullOrWhiteSpace(relative) || relative.Length > 1024 || Path.IsPathRooted(relative))
            throw new InvalidOperationException("Generated file path must be relative to CONTROLDECK_APP_WORK_ROOT");
        var rootRaw = Environment.GetEnvironmentVariable("CONTROLDECK_APP_WORK_ROOT");
        if (string.IsNullOrWhiteSpace(rootRaw)) throw new InvalidOperationException("CONTROLDECK_APP_WORK_ROOT is required for generated file nodes");
        var rootInfo = new DirectoryInfo(Path.GetFullPath(rootRaw));
        if (!rootInfo.Exists) throw new DirectoryNotFoundException("Generated work root does not exist");
        var rootTarget = rootInfo.LinkTarget is null ? rootInfo : rootInfo.ResolveLinkTarget(true) as DirectoryInfo ?? throw new InvalidOperationException("Generated work root link is invalid");
        var root = Path.GetFullPath(rootTarget.FullName);
        var full = Path.GetFullPath(Path.Combine(root, relative));
        var comparison = OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
        var prefix = root.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
        if ((!full.StartsWith(prefix, comparison) && !full.Equals(root, comparison)) || (!allowRoot && full.Equals(root, comparison)))
            throw new InvalidOperationException("Generated file path escaped its work root");
        EnsureNoLinks(root, full);
        return new GeneratedWorkPath(full, Path.GetRelativePath(root, full).Replace('\\', '/'));
    }

    private static GeneratedWorkPath ResolveExistingWorkPath(string fullPath)
    {
        var rootRaw = Environment.GetEnvironmentVariable("CONTROLDECK_APP_WORK_ROOT") ?? throw new InvalidOperationException("CONTROLDECK_APP_WORK_ROOT is required");
        var rootInfo = new DirectoryInfo(Path.GetFullPath(rootRaw));
        var root = Path.GetFullPath((rootInfo.LinkTarget is null ? rootInfo : rootInfo.ResolveLinkTarget(true) as DirectoryInfo ?? rootInfo).FullName);
        var relative = Path.GetRelativePath(root, Path.GetFullPath(fullPath));
        return ResolveWorkPath(relative, allowRoot: false);
    }

    private static void EnsureNoLinks(string root, string fullPath)
    {
        var relative = Path.GetRelativePath(root, fullPath);
        var current = root;
        foreach (var part in relative.Split(new[] { Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar }, StringSplitOptions.RemoveEmptyEntries))
        {
            current = Path.Combine(current, part);
            FileSystemInfo? info = Directory.Exists(current) ? new DirectoryInfo(current) : File.Exists(current) ? new FileInfo(current) : null;
            if (info?.LinkTarget is not null) throw new InvalidOperationException("Generated file path contains a symbolic link");
        }
    }

    private static void ValidateGlobPattern(string pattern)
    {
        var normalized = pattern.Replace('\\', '/');
        if (normalized.Length is < 1 or > 256 || normalized.StartsWith('/') || normalized.Split('/').Contains("..", StringComparer.Ordinal) || normalized.Contains("{{", StringComparison.Ordinal))
            throw new InvalidOperationException("Generated glob pattern is invalid");
    }

    private static JsonObject Condition(JsonObject config, IReadOnlyDictionary<string, GeneratedOutcome> context, IReadOnlyDictionary<string, JsonObject> variables)
    {
        var left = Render(NodeText(config["left"]), context, variables);
        var right = Render(NodeText(config["right"]), context, variables);
        var op = config["op"]?.GetValue<string>() ?? "eq";
        bool result = op switch
        {
            "eq" => left == right, "ne" => left != right, "contains" => left.Contains(right, StringComparison.Ordinal),
            "gt" => Number(left) > Number(right), "gte" => Number(left) >= Number(right),
            "lt" => Number(left) < Number(right), "lte" => Number(left) <= Number(right),
            _ => throw new NotSupportedException($"Generated condition operation is not supported: {op}"),
        };
        return new JsonObject { ["result"] = result, ["left"] = left, ["right"] = right };
    }

    private static double Number(string value) => double.Parse(value, NumberStyles.Float, CultureInfo.InvariantCulture);

    private static JsonObject Merge(JsonObject config, IReadOnlyDictionary<string, GeneratedOutcome> context)
    {
        var mode = config["mode"]?.GetValue<string>() ?? "wait_all";
        var ids = (config["__merge_source_ids"] as JsonArray)?.Select(item => item?.GetValue<string>() ?? "").Where(item => item.Length > 0).ToArray() ?? Array.Empty<string>();
        var items = new JsonArray();
        foreach (var id in ids)
            if (context.TryGetValue(id, out var outcome))
                items.Add(new JsonObject { ["node_id"] = id, ["status"] = StatusText(outcome.Status), ["output"] = outcome.Output.DeepClone() });
        var successful = items.Select(item => item as JsonObject).Where(item => item?["status"]?.GetValue<string>() == "SUCCEEDED").Cast<JsonObject>().ToArray();
        JsonObject[] selected = mode switch
        {
            "first_success" when successful.Length == 0 => throw new InvalidOperationException("No successful merge input"),
            "first_success" => successful.Take(1).ToArray(),
            "first_complete" => items.Select(item => (JsonObject)item!).Take(1).ToArray(),
            "quorum" when successful.Length < Math.Clamp(config["quorum"]?.GetValue<int>() ?? 1, 1, 100) => throw new InvalidOperationException("Merge quorum was not reached"),
            "quorum" => successful.Take(Math.Clamp(config["quorum"]?.GetValue<int>() ?? 1, 1, 100)).ToArray(),
            "wait_all" or "collect" => items.Select(item => (JsonObject)item!).ToArray(),
            _ => throw new NotSupportedException($"Generated merge mode is not supported: {mode}"),
        };
        var values = new JsonArray(selected.Select(item => item["output"]?.DeepClone()).ToArray());
        JsonNode? value = selected.Length == 1 ? selected[0]["output"]?.DeepClone() : values.DeepClone();
        return new JsonObject { ["mode"] = mode, ["items"] = new JsonArray(selected.Select(item => item.DeepClone()).ToArray()), ["values"] = values, ["count"] = selected.Length, ["succeeded"] = selected.Count(item => item["status"]?.GetValue<string>() == "SUCCEEDED"), ["value"] = value };
    }

    private static string StatusText(GeneratedStatus status) => status switch
    {
        GeneratedStatus.Succeeded => "SUCCEEDED", GeneratedStatus.Failed => "FAILED",
        GeneratedStatus.TimedOut => "TIMED_OUT", _ => "SKIPPED",
    };

    private static JsonObject StringOperation(JsonObject config, IReadOnlyDictionary<string, GeneratedOutcome> context, IReadOnlyDictionary<string, JsonObject> variables)
    {
        var text = Render(NodeText(config["text"]), context, variables);
        var op = config["op"]?.GetValue<string>() ?? "template";
        object result = op switch
        {
            "upper" => text.ToUpperInvariant(), "lower" => text.ToLowerInvariant(), "trim" => text.Trim(),
            "replace" => text.Replace(config["find"]?.GetValue<string>() ?? "", Render(NodeText(config["replace"]), context, variables), StringComparison.Ordinal),
            "length" => text.Length, "split" => text.Split(config["sep"]?.GetValue<string>() ?? "\n"),
            "template" => text, _ => throw new NotSupportedException($"Generated string operation is not supported: {op}"),
        };
        return new JsonObject { ["result"] = JsonSerializer.SerializeToNode(result), ["text"] = text };
    }

    private const int MaxDataBytes = 2 * 1024 * 1024;
    private const int MaxDataItems = 10_000;

    private static JsonObject DataTransform(JsonObject config, IReadOnlyDictionary<string, GeneratedOutcome> context, IReadOnlyDictionary<string, JsonObject> variables)
    {
        var operation = config["operation"]?.GetValue<string>() ?? "json_parse";
        var value = JsonConfigValue(config["input"], context, variables, "JSON");
        if (operation == "json_parse") return ValueResult(value);
        var path = RequiredPath(config["path"]);
        if (operation == "json_get")
        {
            if (!TryPath(value, path, out var selected)) throw new InvalidOperationException("JSON path was not found");
            return ValueResult(selected);
        }
        if (operation == "json_set")
        {
            var result = value?.DeepClone();
            var parts = path.Split('.', StringSplitOptions.RemoveEmptyEntries);
            if (!TryPath(result, string.Join('.', parts.Take(parts.Length - 1)), out var parent))
                throw new InvalidOperationException("JSON update path was not found");
            var raw = Render(NodeText(config["value"] ?? JsonValue.Create("null")), context, variables);
            EnsureSize(raw, "JSON setting");
            JsonNode? replacement;
            try { replacement = JsonNode.Parse(raw); } catch (JsonException) { replacement = JsonValue.Create(raw); }
            if (parent is JsonObject parentObject) parentObject[parts[^1]] = replacement;
            else if (parent is JsonArray parentArray && int.TryParse(parts[^1], out var index) && index >= 0 && index < parentArray.Count) parentArray[index] = replacement;
            else throw new InvalidOperationException("JSON update target is not an object or array");
            return ValueResult(result);
        }
        throw new NotSupportedException($"Generated data transform is not supported: {operation}");
    }

    private static JsonObject DataTemplate(JsonObject config, IReadOnlyDictionary<string, GeneratedOutcome> context, IReadOnlyDictionary<string, JsonObject> variables)
    {
        var template = config["template"]?.GetValue<string>() ?? "";
        EnsureSize(template, "Template");
        var data = config["data"] is null || NodeText(config["data"]).Length == 0
            ? new JsonObject() : JsonConfigValue(config["data"], context, variables, "Template data");
        var text = Render(template, context, variables, data);
        EnsureSize(text, "Template output");
        var format = config["output_format"]?.GetValue<string>() ?? "text";
        JsonNode? value = format switch
        {
            "text" => JsonValue.Create(text),
            "json" => JsonNode.Parse(text),
            _ => throw new NotSupportedException($"Generated template format is not supported: {format}"),
        };
        return new JsonObject { ["text"] = text, ["value"] = value, ["format"] = format };
    }

    private static JsonObject DataFilter(JsonObject config, IReadOnlyDictionary<string, GeneratedOutcome> context, IReadOnlyDictionary<string, JsonObject> variables)
    {
        var input = JsonConfigValue(config["input"], context, variables, "Filter input") as JsonArray
            ?? throw new InvalidOperationException("Filter input must be an array");
        if (input.Count > MaxDataItems) throw new InvalidOperationException("Filter input exceeds 10000 items");
        var field = config["field"]?.GetValue<string>() ?? "";
        var operation = config["operator"]?.GetValue<string>() ?? "truthy";
        var expected = TemplateLiteral(config["value"], context, variables);
        var items = input.Where(item => FilterMatch(DataPath(item, field), operation, expected)).Select(item => item?.DeepClone()).ToList();
        var uniqueBy = config["unique_by"]?.GetValue<string>()?.Trim() ?? "";
        if (uniqueBy.Length > 0)
        {
            var seen = new HashSet<string>(StringComparer.Ordinal);
            items = items.Where(item => seen.Add(Canonical(DataPath(item, uniqueBy)))).ToList();
        }
        var sortBy = config["sort_by"]?.GetValue<string>()?.Trim() ?? "";
        if (sortBy.Length > 0)
        {
            items.Sort((left, right) => CompareSortValues(DataPath(left, sortBy), DataPath(right, sortBy)));
            if ((config["sort_order"]?.GetValue<string>() ?? "asc") == "desc") items.Reverse();
        }
        var limit = Math.Clamp(config["limit"]?.GetValue<int>() ?? 0, 0, MaxDataItems);
        if (limit > 0) items = items.Take(limit).ToList();
        return new JsonObject
        {
            ["items"] = new JsonArray(items.ToArray()), ["count"] = items.Count, ["original_count"] = input.Count,
        };
    }

    private static JsonObject DataAggregate(JsonObject config, IReadOnlyDictionary<string, GeneratedOutcome> context, IReadOnlyDictionary<string, JsonObject> variables)
    {
        var input = JsonConfigValue(config["input"], context, variables, "Aggregate input") as JsonArray
            ?? throw new InvalidOperationException("Aggregate input must be an array");
        if (input.Count > MaxDataItems) throw new InvalidOperationException("Aggregate input exceeds 10000 items");
        var operation = config["operation"]?.GetValue<string>() ?? "count";
        var field = config["field"]?.GetValue<string>()?.Trim() ?? "";
        if (operation != "count" && field.Length == 0) throw new InvalidOperationException($"{operation} requires a field");
        var groupBy = config["group_by"]?.GetValue<string>()?.Trim() ?? "";
        var groups = new List<(JsonNode? Key, List<JsonNode?> Items)>();
        var positions = new Dictionary<string, int>(StringComparer.Ordinal);
        foreach (var item in input)
        {
            var key = groupBy.Length == 0 ? null : DataPath(item, groupBy);
            var canonical = Canonical(key);
            if (!positions.TryGetValue(canonical, out var position))
            {
                position = groups.Count;
                positions[canonical] = position;
                groups.Add((key?.DeepClone(), new List<JsonNode?>()));
            }
            groups[position].Items.Add(item);
        }
        var rows = new JsonArray();
        foreach (var group in groups)
            rows.Add(new JsonObject { ["group"] = group.Key?.DeepClone(), ["value"] = Aggregate(group.Items, operation, field), ["count"] = group.Items.Count });
        JsonNode? result = groupBy.Length > 0 ? rows.DeepClone() : rows.Count > 0 ? rows[0]?["value"]?.DeepClone() : operation == "count" ? JsonValue.Create(0) : null;
        return new JsonObject { ["result"] = result, ["groups"] = groupBy.Length > 0 ? rows : new JsonArray(), ["count"] = input.Count, ["operation"] = operation };
    }

    private static JsonNode? Aggregate(IReadOnlyList<JsonNode?> items, string operation, string field)
    {
        if (operation == "count") return JsonValue.Create(items.Count);
        var numbers = new List<double>();
        foreach (var item in items)
        {
            var raw = DataPath(item, field);
            if (raw is null) continue;
            if (raw is not JsonValue scalar || !scalar.TryGetValue<double>(out var number) || scalar.TryGetValue<bool>(out _))
                throw new InvalidOperationException($"Field '{field}' contains a non-number");
            numbers.Add(number);
        }
        if (numbers.Count == 0) return null;
        return operation switch
        {
            "sum" => JsonValue.Create(numbers.Sum()), "avg" => JsonValue.Create(numbers.Average()),
            "min" => JsonValue.Create(numbers.Min()), "max" => JsonValue.Create(numbers.Max()),
            _ => throw new NotSupportedException($"Generated aggregation is not supported: {operation}"),
        };
    }

    private static JsonObject ValueResult(JsonNode? value) => new() { ["value"] = value?.DeepClone(), ["valid"] = true };

    private static JsonNode? JsonConfigValue(JsonNode? raw, IReadOnlyDictionary<string, GeneratedOutcome> context, IReadOnlyDictionary<string, JsonObject> variables, string label)
    {
        if (raw is not JsonValue scalar || !scalar.TryGetValue<string>(out var source)) return raw?.DeepClone();
        var text = Render(source, context, variables);
        EnsureSize(text, label);
        try { return JsonNode.Parse(text); }
        catch (JsonException exception) { throw new InvalidOperationException($"{label} is invalid JSON", exception); }
    }

    private static JsonNode? TemplateLiteral(JsonNode? raw, IReadOnlyDictionary<string, GeneratedOutcome> context, IReadOnlyDictionary<string, JsonObject> variables)
    {
        if (raw is not JsonValue scalar || !scalar.TryGetValue<string>(out var source)) return raw?.DeepClone();
        var text = Render(source, context, variables);
        try { return JsonNode.Parse(text); } catch (JsonException) { return JsonValue.Create(text); }
    }

    private static void EnsureSize(string value, string label)
    {
        if (Encoding.UTF8.GetByteCount(value) > MaxDataBytes) throw new InvalidOperationException($"{label} exceeds the 2 MiB limit");
    }

    private static string RequiredPath(JsonNode? raw)
    {
        var path = raw?.GetValue<string>()?.Trim() ?? "";
        return path.Length > 0 ? path : throw new InvalidOperationException("A JSON path is required");
    }

    private static JsonNode? DataPath(JsonNode? value, string path) => TryPath(value, path, out var selected) ? selected : null;

    private static bool TryPath(JsonNode? value, string path, out JsonNode? selected)
    {
        selected = value;
        if (path.Length == 0) return true;
        foreach (var part in path.Split('.', StringSplitOptions.RemoveEmptyEntries))
        {
            if (selected is JsonObject objectValue && objectValue.TryGetPropertyValue(part, out selected)) continue;
            if (selected is JsonArray arrayValue && int.TryParse(part, out var index) && index >= 0 && index < arrayValue.Count) { selected = arrayValue[index]; continue; }
            selected = null;
            return false;
        }
        return true;
    }

    private static bool FilterMatch(JsonNode? actual, string operation, JsonNode? expected) => operation switch
    {
        "exists" => actual is not null,
        "truthy" => Truthy(actual),
        "equals" => JsonNode.DeepEquals(actual, expected),
        "not_equals" => !JsonNode.DeepEquals(actual, expected),
        "contains" => Contains(actual, expected),
        "gt" => JsonNumber(actual) > JsonNumber(expected), "gte" => JsonNumber(actual) >= JsonNumber(expected),
        "lt" => JsonNumber(actual) < JsonNumber(expected), "lte" => JsonNumber(actual) <= JsonNumber(expected),
        _ => throw new NotSupportedException($"Generated filter operation is not supported: {operation}"),
    };

    private static bool Truthy(JsonNode? value)
    {
        if (value is null) return false;
        if (value is JsonArray array) return array.Count > 0;
        if (value is JsonObject objectValue) return objectValue.Count > 0;
        if (value is JsonValue scalar && scalar.TryGetValue<bool>(out var boolean)) return boolean;
        if (value is JsonValue number && number.TryGetValue<double>(out var numeric)) return numeric != 0;
        return NodeText(value).Length > 0;
    }

    private static bool Contains(JsonNode? actual, JsonNode? expected)
    {
        if (actual is JsonObject objectValue && expected is JsonValue key && key.TryGetValue<string>(out var textKey)) return objectValue.ContainsKey(textKey);
        if (actual is JsonArray array) return array.Any(item => JsonNode.DeepEquals(item, expected));
        return NodeText(actual).Contains(NodeText(expected), StringComparison.Ordinal);
    }

    private static double JsonNumber(JsonNode? value)
    {
        if (value is JsonValue scalar && !scalar.TryGetValue<bool>(out _) && scalar.TryGetValue<double>(out var number)) return number;
        if (double.TryParse(NodeText(value), NumberStyles.Float, CultureInfo.InvariantCulture, out number)) return number;
        throw new InvalidOperationException("Filter comparison value is not numeric");
    }

    private static int CompareSortValues(JsonNode? left, JsonNode? right)
    {
        var leftKind = SortKind(left); var rightKind = SortKind(right);
        if (leftKind != rightKind) return leftKind.CompareTo(rightKind);
        return leftKind switch
        {
            0 => JsonNumber(left).CompareTo(JsonNumber(right)),
            1 => Truthy(left).CompareTo(Truthy(right)),
            2 => StringComparer.OrdinalIgnoreCase.Compare(NodeText(left), NodeText(right)),
            _ => StringComparer.Ordinal.Compare(Canonical(left), Canonical(right)),
        };
    }

    private static int SortKind(JsonNode? value)
    {
        if (value is null) return 4;
        if (value is JsonValue scalar && scalar.TryGetValue<bool>(out _)) return 1;
        if (value is JsonValue number && number.TryGetValue<double>(out _)) return 0;
        if (value is JsonValue text && text.TryGetValue<string>(out _)) return 2;
        return 3;
    }

    private static string Canonical(JsonNode? value)
    {
        if (value is JsonObject objectValue)
        {
            var sorted = new JsonObject();
            foreach (var pair in objectValue.OrderBy(pair => pair.Key, StringComparer.Ordinal)) sorted[pair.Key] = CanonicalNode(pair.Value);
            return sorted.ToJsonString();
        }
        return CanonicalNode(value)?.ToJsonString() ?? "null";
    }

    private static JsonNode? CanonicalNode(JsonNode? value)
    {
        if (value is JsonObject objectValue)
        {
            var sorted = new JsonObject();
            foreach (var pair in objectValue.OrderBy(pair => pair.Key, StringComparer.Ordinal)) sorted[pair.Key] = CanonicalNode(pair.Value);
            return sorted;
        }
        if (value is JsonArray array) return new JsonArray(array.Select(CanonicalNode).ToArray());
        return value?.DeepClone();
    }

    private static JsonObject Output(JsonObject config, IReadOnlyDictionary<string, GeneratedOutcome> context, IReadOnlyDictionary<string, JsonObject> variables)
    {
        var name = config["name"]?.GetValue<string>() ?? "output";
        var renderer = config["renderer"]?.GetValue<string>() ?? "auto";
        var rendered = Render(NodeText(config["value"]), context, variables);
        JsonNode? value = JsonValue.Create(rendered);
        if (renderer is "json" or "json_tree" or "json_raw" or "table" or "key_value" or "image_gallery" or "citation_list")
        {
            try { value = JsonNode.Parse(rendered); } catch (JsonException) { value = JsonValue.Create(rendered); }
        }
        return new JsonObject
        {
            ["display"] = true, ["output_contract"] = true, ["name"] = name, ["signal"] = name,
            ["type"] = renderer, ["renderer"] = renderer, ["value"] = value,
            ["title"] = Render(NodeText(config["title"]), context, variables), ["description"] = Render(NodeText(config["description"]), context, variables),
            ["downloadable"] = config["downloadable"]?.GetValue<bool>() ?? false, ["copyable"] = config["copyable"]?.GetValue<bool>() ?? true,
            ["sensitive"] = config["sensitive"]?.GetValue<bool>() ?? false, ["filename"] = Render(NodeText(config["filename"]), context, variables),
            ["mime_type"] = config["mime_type"]?.GetValue<string>() ?? "",
        };
    }

    internal static string NodeText(JsonNode? value) => value is JsonValue scalar && scalar.TryGetValue<string>(out var text) ? text : value?.ToJsonString() ?? "";

    internal static int Integer(JsonNode? value, int fallback)
    {
        if (value is JsonValue scalar && scalar.TryGetValue<int>(out var number)) return number;
        return int.TryParse(NodeText(value), NumberStyles.Integer, CultureInfo.InvariantCulture, out number) ? number : fallback;
    }

    internal static string Render(
        string template, IReadOnlyDictionary<string, GeneratedOutcome> context,
        IReadOnlyDictionary<string, JsonObject> variables, JsonNode? data = null) => Reference.Replace(template, match =>
    {
        var parts = match.Groups[1].Value.Split('.');
        if (parts.Length < 2) return "";
        if (parts[0] == "secrets" && parts.Length == 2) return GeneratedSecrets.Resolve(parts[1]);
        JsonNode? value;
        var offset = 1;
        if (parts[0] == "vars")
        {
            if (parts.Length < 3 || !variables.TryGetValue(parts[1], out var variable)) return "";
            value = variable;
            offset = 2;
        }
        else if (parts[0] == "data") value = data;
        else
        {
            if (!context.TryGetValue(parts[0], out var node)) return "";
            value = node.Output;
        }
        if (!TryPath(value, string.Join('.', parts.Skip(offset)), out value)) return "";
        return value is JsonValue scalar && scalar.TryGetValue<string>(out var text) ? text : value?.ToJsonString() ?? "";
    });

    internal static JsonObject Collect(IReadOnlyList<GeneratedNode> nodes, IReadOnlyDictionary<string, GeneratedOutcome> context)
    {
        var result = new JsonObject();
        foreach (var node in nodes)
            if (context.TryGetValue(node.Id, out var outcome) && outcome.Output["display"]?.GetValue<bool>() == true)
                result[outcome.Output["name"]?.GetValue<string>() ?? node.Id] = outcome.Output["value"]?.DeepClone();
        if (result.Count == 0)
        {
            var last = nodes.LastOrDefault(node => context.TryGetValue(node.Id, out var outcome) && outcome.Status == GeneratedStatus.Succeeded);
            if (last is not null) result["result"] = context[last.Id].Output.DeepClone();
        }
        return result;
    }
}
'''
