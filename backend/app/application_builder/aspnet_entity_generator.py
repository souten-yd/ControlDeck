from __future__ import annotations

import json
import re
from typing import Any


def entity_table_name(entity: dict[str, Any]) -> str:
    explicit = entity.get("tableName")
    if isinstance(explicit, str) and explicit:
        return explicit
    value = str(entity.get("id") or "entity")
    return re.sub(r"[^a-z0-9_]", "_", re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower())


def entity_base_path(entity: dict[str, Any]) -> str:
    crud = entity.get("crud") if isinstance(entity.get("crud"), dict) else {}
    explicit = crud.get("basePath")
    if isinstance(explicit, str) and explicit:
        return explicit
    value = str(entity.get("id") or "entity")
    value = re.sub(r"[^a-z0-9_-]", "-", re.sub(r"(?<!^)(?=[A-Z])", "-", value).lower())
    return f"/api/entities/{value}"


def render_entity_source(namespace: str, entities: list[dict[str, Any]], authentication: str) -> str:
    payload = []
    table_by_id = {str(item.get("id")): entity_table_name(item) for item in entities}
    for entity in entities:
        crud = entity.get("crud") if isinstance(entity.get("crud"), dict) else {}
        fields = []
        for field in entity.get("fields") or []:
            reference = field.get("reference") if isinstance(field.get("reference"), dict) else None
            fields.append({
                "name": field["id"], "type": field["type"], "nullable": bool(field.get("nullable")),
                "hasDefault": bool(field.get("hasDefault")), "default": field.get("default"),
                "maxLength": field.get("maxLength"), "unique": bool(field.get("unique")),
                "indexed": bool(field.get("indexed")),
                "referenceTable": table_by_id.get(str(reference.get("entityId"))) if reference else None,
                "onDelete": str(reference.get("onDelete") or "restrict") if reference else None,
            })
        payload.append({
            "id": entity["id"], "table": entity_table_name(entity), "basePath": entity_base_path(entity),
            "enabled": bool(crud.get("enabled")),
            "operations": crud.get("operations") or ["create", "read", "list", "update", "delete"],
            "fields": fields,
        })
    metadata = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    anonymous = "true" if authentication == "none" else "false"
    template = r'''#nullable enable
using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Data.Sqlite;

namespace __NAMESPACE__.Generated;

internal sealed record GeneratedEntityField(
    string Name, string Type, bool Nullable, bool HasDefault, JsonNode? Default, int? MaxLength,
    bool Unique, bool Indexed, string? ReferenceTable, string? OnDelete);
internal sealed record GeneratedEntityDefinition(
    string Id, string Table, string BasePath, bool Enabled, string[] Operations, GeneratedEntityField[] Fields);
internal sealed record GeneratedQueryFilter(string Field, string Operator, JsonNode? Value);
internal sealed record GeneratedQuerySort(string Field, string Direction);

public static class GeneratedEntities
{
    private const int MaxBodyBytes = 2 * 1024 * 1024;
    private static readonly GeneratedEntityDefinition[] Definitions = ParseDefinitions();
    private static string _connectionString = "";

    public static async Task InitializeAsync(CancellationToken cancellationToken)
    {
        if (Definitions.Length == 0) return;
        var rootValue = Environment.GetEnvironmentVariable("CONTROLDECK_APP_DATA_DIR");
        var root = string.IsNullOrWhiteSpace(rootValue)
            ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), GeneratedApplication.Name)
            : rootValue;
        root = Path.GetFullPath(root);
        Directory.CreateDirectory(root);
        var databasePath = Path.GetFullPath(Path.Combine(root, "application.sqlite3"));
        var comparison = OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
        if (!databasePath.StartsWith(root.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar, comparison))
            throw new InvalidOperationException("Generated database path escaped its data root");
        _connectionString = new SqliteConnectionStringBuilder { DataSource = databasePath, Mode = SqliteOpenMode.ReadWriteCreate }.ToString();
        await using var connection = await OpenAsync(cancellationToken);
        await ExecuteAsync(connection, "PRAGMA journal_mode=WAL;", cancellationToken);
        await ExecuteAsync(connection, "CREATE TABLE IF NOT EXISTS \"_controldeck_migrations\" (\"signature\" TEXT PRIMARY KEY NOT NULL, \"appliedAt\" TEXT NOT NULL);", cancellationToken);
        await ExecuteAsync(connection, "CREATE TABLE IF NOT EXISTS \"_controldeck_audit\" (\"id\" TEXT PRIMARY KEY NOT NULL, \"action\" TEXT NOT NULL, \"entity\" TEXT NOT NULL, \"resourceId\" TEXT NOT NULL, \"createdAt\" TEXT NOT NULL);", cancellationToken);
        await ExecuteAsync(connection, "BEGIN IMMEDIATE;", cancellationToken);
        try
        {
            foreach (var definition in Definitions) await MigrateAsync(connection, definition, cancellationToken);
            await ExecuteAsync(connection, "COMMIT;", cancellationToken);
        }
        catch
        {
            await ExecuteAsync(connection, "ROLLBACK;", CancellationToken.None);
            throw;
        }
    }

    public static void Map(WebApplication app)
    {
        foreach (var definition in Definitions.Where(item => item.Enabled))
        {
            var operations = definition.Operations.ToHashSet(StringComparer.Ordinal);
            if (operations.Contains("list")) app.MapGet(definition.BasePath, (HttpRequest request) => ListAsync(definition, request));
            if (operations.Contains("read")) app.MapGet(definition.BasePath + "/{id}", (string id, HttpRequest request) => ReadAsync(definition, id, request));
            if (operations.Contains("create")) app.MapPost(definition.BasePath, (HttpRequest request) => CreateAsync(definition, request));
            if (operations.Contains("update")) app.MapPatch(definition.BasePath + "/{id}", (string id, HttpRequest request) => UpdateAsync(definition, id, request));
            if (operations.Contains("delete")) app.MapDelete(definition.BasePath + "/{id}", (string id, HttpRequest request) => DeleteAsync(definition, id, request));
        }
    }

    private static async Task<IResult> ListAsync(GeneratedEntityDefinition definition, HttpRequest request)
    {
        if (!Authorized(request)) return Results.Unauthorized();
        var limit = ParseBounded(request.Query["limit"], 50, 1, 100);
        var offset = ParseBounded(request.Query["offset"], 0, 0, 1_000_000);
        await using var connection = await OpenAsync(request.HttpContext.RequestAborted);
        await using var command = connection.CreateCommand();
        if (!TryBuildListQuery(definition, request, command, out var where, out var order, out var queryError))
            return Results.BadRequest(new { error = queryError });
        command.CommandText = $"SELECT * FROM {Quote(definition.Table)}{where} ORDER BY {order} LIMIT $limit OFFSET $offset";
        command.Parameters.AddWithValue("$limit", limit); command.Parameters.AddWithValue("$offset", offset);
        var rows = new JsonArray();
        await using var reader = await command.ExecuteReaderAsync(request.HttpContext.RequestAborted);
        while (await reader.ReadAsync(request.HttpContext.RequestAborted)) rows.Add(ReadRow(reader, definition));
        return Results.Json(new { items = rows, limit, offset });
    }

    private static bool TryBuildListQuery(
        GeneratedEntityDefinition definition, HttpRequest request, SqliteCommand command,
        out string where, out string order, out string error)
    {
        where = ""; order = Quote("id"); error = "Query options are invalid";
        var filterText = request.Query["filter"].ToString(); var sortText = request.Query["sort"].ToString();
        if (filterText.Length > 16_384 || sortText.Length > 8_192) { error = "Query options are too large"; return false; }
        GeneratedQueryFilter[] filters; GeneratedQuerySort[] sorts;
        try
        {
            filters = string.IsNullOrEmpty(filterText) ? [] : JsonSerializer.Deserialize<GeneratedQueryFilter[]>(filterText, JsonOptions()) ?? [];
            sorts = string.IsNullOrEmpty(sortText) ? [] : JsonSerializer.Deserialize<GeneratedQuerySort[]>(sortText, JsonOptions()) ?? [];
        }
        catch (JsonException) { return false; }
        if (filters.Length > 20 || sorts.Length > 3) { error = "Too many query options"; return false; }
        var clauses = new List<string>();
        for (var index = 0; index < filters.Length; index++)
        {
            var filter = filters[index]; var field = QueryField(definition, filter.Field);
            if (field is null || !AllowedQueryOperator(field, filter.Operator)) return false;
            var column = Quote(filter.Field);
            if (filter.Operator == "is-null") { clauses.Add(column + " IS NULL"); continue; }
            if (!ValidQueryValue(filter.Value, field)) return false;
            var parameter = "$filter" + index;
            if (filter.Operator is "contains" or "starts-with")
            {
                var escaped = EscapeLike(filter.Value!.GetValue<string>());
                command.Parameters.AddWithValue(parameter, filter.Operator == "contains" ? "%" + escaped + "%" : escaped + "%");
                clauses.Add(column + " LIKE " + parameter + " ESCAPE '\\'");
                continue;
            }
            var sqlOperator = filter.Operator switch { "eq" => "=", "ne" => "<>", "gt" => ">", "gte" => ">=", "lt" => "<", "lte" => "<=", _ => "" };
            if (string.IsNullOrEmpty(sqlOperator)) return false;
            command.Parameters.AddWithValue(parameter, ToDatabase(filter.Value, field) ?? DBNull.Value);
            clauses.Add(column + " " + sqlOperator + " " + parameter);
        }
        if (clauses.Count > 0) where = " WHERE " + string.Join(" AND ", clauses);
        var orderParts = new List<string>(); var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var item in sorts)
        {
            if (QueryField(definition, item.Field) is null || !seen.Add(item.Field) || item.Direction is not ("asc" or "desc")) return false;
            orderParts.Add(Quote(item.Field) + (item.Direction == "desc" ? " DESC" : " ASC"));
        }
        if (seen.Add("id")) orderParts.Add(Quote("id") + " ASC");
        order = string.Join(",", orderParts); error = ""; return true;
    }

    private static GeneratedEntityField? QueryField(GeneratedEntityDefinition definition, string name)
    {
        if (name == "id") return new("id", "string", false, false, null, null, false, false, null, null);
        if (name is "createdAt" or "updatedAt") return new(name, "datetime", false, false, null, null, false, false, null, null);
        return definition.Fields.FirstOrDefault(item => item.Name == name);
    }
    private static bool AllowedQueryOperator(GeneratedEntityField field, string value)
    {
        if (value == "is-null") return field.Nullable;
        if (value is "eq" or "ne") return field.Type != "json";
        if (value is "contains" or "starts-with") return field.Type == "string";
        return (value is "gt" or "gte" or "lt" or "lte") && (field.Type is "integer" or "number" or "datetime");
    }
    private static bool ValidQueryValue(JsonNode? value, GeneratedEntityField field)
    {
        if (value is null) return false;
        return field.Type switch
        {
            "string" => value is JsonValue stringValue && stringValue.TryGetValue<string>(out _),
            "datetime" => value is JsonValue dateValue && dateValue.TryGetValue<string>(out var date) && DateTimeOffset.TryParse(date, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out _),
            "integer" => value is JsonValue integerValue && integerValue.TryGetValue<long>(out _),
            "number" => value is JsonValue numberValue && numberValue.TryGetValue<double>(out var number) && double.IsFinite(number),
            "boolean" => value is JsonValue booleanValue && booleanValue.TryGetValue<bool>(out _),
            _ => false,
        };
    }
    private static string EscapeLike(string value) => value.Replace("\\", "\\\\", StringComparison.Ordinal).Replace("%", "\\%", StringComparison.Ordinal).Replace("_", "\\_", StringComparison.Ordinal);
    private static JsonSerializerOptions JsonOptions() => new() { PropertyNameCaseInsensitive = true };

    private static async Task<IResult> ReadAsync(GeneratedEntityDefinition definition, string id, HttpRequest request)
    {
        if (!Authorized(request)) return Results.Unauthorized();
        if (!ValidId(id)) return Results.BadRequest(new { error = "Entity id must be a UUID" });
        await using var connection = await OpenAsync(request.HttpContext.RequestAborted);
        var row = await FindAsync(connection, definition, id, request.HttpContext.RequestAborted);
        return row is null ? Results.NotFound() : Results.Json(row);
    }

    private static async Task<IResult> CreateAsync(GeneratedEntityDefinition definition, HttpRequest request)
    {
        if (!Authorized(request)) return Results.Unauthorized();
        var body = await ReadBodyAsync(request);
        if (body is null) return Results.BadRequest(new { error = "A JSON object body is required" });
        var errors = ValidateBody(body, definition, partial: false);
        if (errors.Count > 0) return Results.Json(new { error = "Entity validation failed", diagnostics = errors }, statusCode: 400);
        var id = Guid.NewGuid().ToString("D", CultureInfo.InvariantCulture); var now = DateTimeOffset.UtcNow.ToString("O", CultureInfo.InvariantCulture);
        var names = new List<string> { "id", "createdAt", "updatedAt" }; var values = new List<object?> { id, now, now };
        foreach (var field in definition.Fields)
        {
            names.Add(field.Name);
            values.Add(body.TryGetPropertyValue(field.Name, out var node) ? ToDatabase(node, field) : DefaultDatabase(field));
        }
        await using var connection = await OpenAsync(request.HttpContext.RequestAborted);
        await using var command = connection.CreateCommand();
        command.CommandText = $"INSERT INTO {Quote(definition.Table)} ({string.Join(",", names.Select(Quote))}) VALUES ({string.Join(",", names.Select((_, i) => "$p" + i))})";
        for (var i = 0; i < values.Count; i++) command.Parameters.AddWithValue("$p" + i, values[i] ?? DBNull.Value);
        try { await command.ExecuteNonQueryAsync(request.HttpContext.RequestAborted); }
        catch (SqliteException exception) when (exception.SqliteErrorCode is 19) { return Results.Conflict(new { error = "Entity constraint failed" }); }
        var row = await FindAsync(connection, definition, id, request.HttpContext.RequestAborted);
        return Results.Created(definition.BasePath + "/" + id, row);
    }

    private static async Task<IResult> UpdateAsync(GeneratedEntityDefinition definition, string id, HttpRequest request)
    {
        if (!Authorized(request)) return Results.Unauthorized();
        if (!ValidId(id)) return Results.BadRequest(new { error = "Entity id must be a UUID" });
        var body = await ReadBodyAsync(request);
        if (body is null || body.Count == 0) return Results.BadRequest(new { error = "A non-empty JSON object body is required" });
        var errors = ValidateBody(body, definition, partial: true);
        if (errors.Count > 0) return Results.Json(new { error = "Entity validation failed", diagnostics = errors }, statusCode: 400);
        var fields = definition.Fields.Where(item => body.ContainsKey(item.Name)).ToArray();
        await using var connection = await OpenAsync(request.HttpContext.RequestAborted);
        await using var command = connection.CreateCommand();
        var assignments = fields.Select((field, index) => $"{Quote(field.Name)}=$p{index}").Append($"{Quote("updatedAt")}=$updated");
        command.CommandText = $"UPDATE {Quote(definition.Table)} SET {string.Join(",", assignments)} WHERE {Quote("id")}=$id";
        for (var i = 0; i < fields.Length; i++) command.Parameters.AddWithValue("$p" + i, ToDatabase(body[fields[i].Name], fields[i]) ?? DBNull.Value);
        command.Parameters.AddWithValue("$updated", DateTimeOffset.UtcNow.ToString("O", CultureInfo.InvariantCulture)); command.Parameters.AddWithValue("$id", id);
        try { if (await command.ExecuteNonQueryAsync(request.HttpContext.RequestAborted) == 0) return Results.NotFound(); }
        catch (SqliteException exception) when (exception.SqliteErrorCode is 19) { return Results.Conflict(new { error = "Entity constraint failed" }); }
        return Results.Json(await FindAsync(connection, definition, id, request.HttpContext.RequestAborted));
    }

    private static async Task<IResult> DeleteAsync(GeneratedEntityDefinition definition, string id, HttpRequest request)
    {
        if (!Authorized(request)) return Results.Unauthorized();
        if (!ValidId(id)) return Results.BadRequest(new { error = "Entity id must be a UUID" });
        await using var connection = await OpenAsync(request.HttpContext.RequestAborted);
        await ExecuteAsync(connection, "BEGIN IMMEDIATE;", request.HttpContext.RequestAborted);
        try
        {
            await using var command = connection.CreateCommand();
            command.CommandText = $"DELETE FROM {Quote(definition.Table)} WHERE {Quote("id")}=$id"; command.Parameters.AddWithValue("$id", id);
            if (await command.ExecuteNonQueryAsync(request.HttpContext.RequestAborted) == 0)
            {
                await ExecuteAsync(connection, "ROLLBACK;", CancellationToken.None); return Results.NotFound();
            }
            await using var audit = connection.CreateCommand();
            audit.CommandText = "INSERT INTO \"_controldeck_audit\" (\"id\",\"action\",\"entity\",\"resourceId\",\"createdAt\") VALUES ($auditId,'delete',$entity,$resourceId,$createdAt)";
            audit.Parameters.AddWithValue("$auditId", Guid.NewGuid().ToString("D", CultureInfo.InvariantCulture));
            audit.Parameters.AddWithValue("$entity", definition.Id); audit.Parameters.AddWithValue("$resourceId", id);
            audit.Parameters.AddWithValue("$createdAt", DateTimeOffset.UtcNow.ToString("O", CultureInfo.InvariantCulture));
            await audit.ExecuteNonQueryAsync(request.HttpContext.RequestAborted);
            await ExecuteAsync(connection, "COMMIT;", request.HttpContext.RequestAborted);
        }
        catch (SqliteException exception) when (exception.SqliteErrorCode is 19)
        {
            await ExecuteAsync(connection, "ROLLBACK;", CancellationToken.None); return Results.Conflict(new { error = "Entity is still referenced" });
        }
        catch
        {
            await ExecuteAsync(connection, "ROLLBACK;", CancellationToken.None); throw;
        }
        Console.Error.WriteLine($"Generated entity delete completed: entity={definition.Id}");
        return Results.NoContent();
    }

    private static List<object> ValidateBody(JsonObject body, GeneratedEntityDefinition definition, bool partial)
    {
        var errors = new List<object>(); var fields = definition.Fields.ToDictionary(item => item.Name, StringComparer.Ordinal);
        foreach (var name in body.Select(item => item.Key))
            if (!fields.ContainsKey(name)) errors.Add(new { path = "$.'" + name.Replace("'", "", StringComparison.Ordinal) + "'", keyword = "additionalProperties", message = "Unknown entity field" });
        foreach (var field in definition.Fields)
        {
            if (!body.TryGetPropertyValue(field.Name, out var value))
            {
                if (!partial && !field.Nullable && !field.HasDefault) errors.Add(new { path = "$." + field.Name, keyword = "required", message = "Required entity field is missing" });
                continue;
            }
            if (value is null) { if (!field.Nullable) errors.Add(new { path = "$." + field.Name, keyword = "type", message = "Null is not allowed" }); continue; }
            var valid = field.Type switch
            {
                "string" or "datetime" => value is JsonValue stringValue && stringValue.TryGetValue<string>(out _),
                "integer" => value is JsonValue integerValue && integerValue.TryGetValue<long>(out _),
                "number" => value is JsonValue numberValue && numberValue.TryGetValue<double>(out _),
                "boolean" => value is JsonValue booleanValue && booleanValue.TryGetValue<bool>(out _),
                "json" => true, _ => false,
            };
            if (!valid) errors.Add(new { path = "$." + field.Name, keyword = "type", message = "Entity field type is invalid" });
            if (valid && field.Type == "string" && field.MaxLength.HasValue)
                if (value!.GetValue<string>().Length > field.MaxLength.Value)
                    errors.Add(new { path = "$." + field.Name, keyword = "maxLength", message = "Entity field is too long" });
            if (valid && field.Type == "datetime" && !DateTimeOffset.TryParse(value!.GetValue<string>(), CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out _))
                errors.Add(new { path = "$." + field.Name, keyword = "format", message = "Datetime must use an ISO 8601 offset" });
        }
        return errors.Take(100).ToList();
    }

    private static async Task<JsonObject?> ReadBodyAsync(HttpRequest request)
    {
        if (request.ContentType is not null && !request.ContentType.StartsWith("application/json", StringComparison.OrdinalIgnoreCase)) return null;
        request.EnableBuffering();
        if (request.ContentLength > MaxBodyBytes) throw new BadHttpRequestException("Request body is too large", 413);
        try { return await JsonNode.ParseAsync(request.Body, cancellationToken: request.HttpContext.RequestAborted) as JsonObject; }
        catch (JsonException) { return null; }
    }

    private static async Task MigrateAsync(SqliteConnection connection, GeneratedEntityDefinition definition, CancellationToken cancellationToken)
    {
        var columns = new List<string> { "\"id\" TEXT PRIMARY KEY NOT NULL" };
        columns.AddRange(definition.Fields.Select(FieldSql));
        columns.Add("\"createdAt\" TEXT NOT NULL"); columns.Add("\"updatedAt\" TEXT NOT NULL");
        columns.AddRange(definition.Fields.Where(item => item.ReferenceTable is not null).Select(item =>
            $"FOREIGN KEY ({Quote(item.Name)}) REFERENCES {Quote(item.ReferenceTable!)}(\"id\") ON DELETE {DeleteSql(item.OnDelete)}"));
        await ExecuteAsync(connection, $"CREATE TABLE IF NOT EXISTS {Quote(definition.Table)} ({string.Join(",", columns)});", cancellationToken);
        var existing = new Dictionary<string, (string Type, bool NotNull)>(StringComparer.Ordinal);
        await using (var command = connection.CreateCommand())
        {
            command.CommandText = $"PRAGMA table_info({Quote(definition.Table)})";
            await using var reader = await command.ExecuteReaderAsync(cancellationToken);
            while (await reader.ReadAsync(cancellationToken)) existing[reader.GetString(1)] = (reader.GetString(2).ToUpperInvariant(), reader.GetInt64(3) != 0);
        }
        foreach (var field in definition.Fields.Where(item => !existing.ContainsKey(item.Name)))
        {
            if (!field.Nullable && !field.HasDefault) throw new InvalidOperationException($"Cannot add required field {definition.Id}.{field.Name} without a default");
            await ExecuteAsync(connection, $"ALTER TABLE {Quote(definition.Table)} ADD COLUMN {FieldSql(field)};", cancellationToken);
        }
        foreach (var field in definition.Fields.Where(item => existing.ContainsKey(item.Name)))
        {
            var stored = existing[field.Name];
            if (stored.Type != StorageType(field) || stored.NotNull != !field.Nullable)
                throw new InvalidOperationException($"Incompatible field migration for {definition.Id}.{field.Name}");
        }
        var foreignKeys = new HashSet<string>(StringComparer.Ordinal);
        await using (var foreignKeyCommand = connection.CreateCommand())
        {
            foreignKeyCommand.CommandText = $"PRAGMA foreign_key_list({Quote(definition.Table)})";
            await using var reader = await foreignKeyCommand.ExecuteReaderAsync(cancellationToken);
            while (await reader.ReadAsync(cancellationToken)) foreignKeys.Add(reader.GetString(3) + "|" + reader.GetString(2) + "|" + reader.GetString(4) + "|" + reader.GetString(6).ToUpperInvariant());
        }
        foreach (var field in definition.Fields.Where(item => item.ReferenceTable is not null))
        {
            var expected = field.Name + "|" + field.ReferenceTable + "|id|" + DeleteSql(field.OnDelete);
            if (!foreignKeys.Contains(expected)) throw new InvalidOperationException($"Incompatible relation migration for {definition.Id}.{field.Name}");
        }
        foreach (var field in definition.Fields.Where(item => item.Indexed || item.Unique))
        {
            var indexName = $"ix_{definition.Table}_{field.Name}";
            await ExecuteAsync(connection, $"CREATE {(field.Unique ? "UNIQUE " : "")}INDEX IF NOT EXISTS {Quote(indexName)} ON {Quote(definition.Table)} ({Quote(field.Name)});", cancellationToken);
        }
        await using var migration = connection.CreateCommand();
        migration.CommandText = "INSERT OR IGNORE INTO \"_controldeck_migrations\" (\"signature\",\"appliedAt\") VALUES ($signature,$appliedAt)";
        migration.Parameters.AddWithValue("$signature", GeneratedApplication.SpecChecksum + ":" + definition.Id);
        migration.Parameters.AddWithValue("$appliedAt", DateTimeOffset.UtcNow.ToString("O", CultureInfo.InvariantCulture));
        await migration.ExecuteNonQueryAsync(cancellationToken);
    }

    private static string FieldSql(GeneratedEntityField field)
    {
        var sql = Quote(field.Name) + " " + StorageType(field);
        if (!field.Nullable) sql += " NOT NULL";
        if (field.HasDefault) sql += " DEFAULT " + Literal(DefaultDatabase(field));
        return sql;
    }
    private static string StorageType(GeneratedEntityField field) => field.Type switch { "integer" or "boolean" => "INTEGER", "number" => "REAL", _ => "TEXT" };
    private static string DeleteSql(string? value) => value switch { "cascade" => "CASCADE", "set-null" => "SET NULL", _ => "RESTRICT" };
    private static string Quote(string value) => "\"" + value.Replace("\"", "\"\"", StringComparison.Ordinal) + "\"";
    private static string Literal(object? value) => value switch { null => "NULL", long or int or double => Convert.ToString(value, CultureInfo.InvariantCulture)!, bool item => item ? "1" : "0", _ => "'" + Convert.ToString(value, CultureInfo.InvariantCulture)!.Replace("'", "''", StringComparison.Ordinal) + "'" };
    private static object? DefaultDatabase(GeneratedEntityField field) => field.HasDefault ? ToDatabase(field.Default, field) : null;
    private static object? ToDatabase(JsonNode? value, GeneratedEntityField field)
    {
        if (value is null) return null;
        return field.Type switch { "string" => value.GetValue<string>(), "datetime" => DateTimeOffset.Parse(value.GetValue<string>(), CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind).ToUniversalTime().ToString("O", CultureInfo.InvariantCulture), "integer" => value.GetValue<long>(), "number" => value.GetValue<double>(), "boolean" => value.GetValue<bool>() ? 1L : 0L, "json" => value.ToJsonString(), _ => null };
    }
    private static JsonObject ReadRow(SqliteDataReader reader, GeneratedEntityDefinition definition)
    {
        var result = new JsonObject { ["id"] = reader.GetString(reader.GetOrdinal("id")), ["createdAt"] = reader.GetString(reader.GetOrdinal("createdAt")), ["updatedAt"] = reader.GetString(reader.GetOrdinal("updatedAt")) };
        foreach (var field in definition.Fields)
        {
            var ordinal = reader.GetOrdinal(field.Name); if (reader.IsDBNull(ordinal)) { result[field.Name] = null; continue; }
            result[field.Name] = field.Type switch { "integer" => JsonValue.Create(reader.GetInt64(ordinal)), "number" => JsonValue.Create(reader.GetDouble(ordinal)), "boolean" => JsonValue.Create(reader.GetInt64(ordinal) != 0), "json" => JsonNode.Parse(reader.GetString(ordinal)), _ => JsonValue.Create(reader.GetString(ordinal)) };
        }
        return result;
    }
    private static async Task<JsonObject?> FindAsync(SqliteConnection connection, GeneratedEntityDefinition definition, string id, CancellationToken cancellationToken)
    {
        await using var command = connection.CreateCommand(); command.CommandText = $"SELECT * FROM {Quote(definition.Table)} WHERE {Quote("id")}=$id"; command.Parameters.AddWithValue("$id", id);
        await using var reader = await command.ExecuteReaderAsync(cancellationToken); return await reader.ReadAsync(cancellationToken) ? ReadRow(reader, definition) : null;
    }
    private static async Task<SqliteConnection> OpenAsync(CancellationToken cancellationToken)
    {
        if (string.IsNullOrEmpty(_connectionString)) throw new InvalidOperationException("Generated entity database is not initialized");
        var connection = new SqliteConnection(_connectionString); await connection.OpenAsync(cancellationToken); await ExecuteAsync(connection, "PRAGMA foreign_keys=ON;", cancellationToken); return connection;
    }
    private static async Task ExecuteAsync(SqliteConnection connection, string sql, CancellationToken cancellationToken) { await using var command = connection.CreateCommand(); command.CommandText = sql; await command.ExecuteNonQueryAsync(cancellationToken); }
    private static int ParseBounded(string? raw, int fallback, int minimum, int maximum) => int.TryParse(raw, NumberStyles.None, CultureInfo.InvariantCulture, out var value) ? Math.Clamp(value, minimum, maximum) : fallback;
    private static bool ValidId(string id) => Guid.TryParseExact(id, "D", out _);
    private static bool Authorized(HttpRequest request) => GeneratedApiKey.IsAuthorized(request, anonymous: __ANONYMOUS__);
    private static GeneratedEntityDefinition[] ParseDefinitions() => JsonSerializer.Deserialize<GeneratedEntityDefinition[]>(__METADATA__, new JsonSerializerOptions { PropertyNameCaseInsensitive = true }) ?? [];
}
'''
    csharp_metadata = json.dumps(metadata, ensure_ascii=False)
    return (template.replace("__NAMESPACE__", namespace)
            .replace("__ANONYMOUS__", anonymous)
            .replace("__METADATA__", csharp_metadata))
