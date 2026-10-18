"""A very small routing layer on top of Starlette.

It gives us what we need from a web framework (typed JSON bodies, validation errors, one place
for error handling) plus generated OpenAPI docs, without any dependency beyond Starlette and
Pydantic. Handlers are plain ``async def handler(request, body=None)`` functions.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ValidationError
from pydantic_core import to_jsonable_python
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .errors import ApiError

Handler = Callable[..., Awaitable[Any]]


@dataclass
class Endpoint:
    method: str
    path: str
    handler: Handler
    summary: str
    tags: list[str]
    body: type[BaseModel] | None = None
    response: type[BaseModel] | None = None
    query: list[tuple[str, str, str]] = field(default_factory=list)  # (name, type, description)
    status: int = 200
    expensive: bool = False
    multipart: bool = False
    public: bool = False  # skips the optional API token check


def validation_details(exc: ValidationError) -> list[dict]:
    out = []
    for err in exc.errors(include_url=False, include_context=False, include_input=False):
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        out.append({"field": loc or "(body)", "message": err.get("msg", "Invalid value")})
    return out


async def parse_json(request: Request, model: type[BaseModel], max_bytes: int) -> BaseModel:
    raw = await request.body()
    if len(raw) > max_bytes:
        raise ApiError(413, "payload_too_large", f"JSON body is larger than {max_bytes // 1024} KB.")
    if not raw.strip():
        raw = b"{}"
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ApiError(400, "invalid_json", "The request body is not valid JSON.") from None
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        details = validation_details(exc)
        first = details[0]
        raise ApiError(422, "validation_error", f"{first['field']}: {first['message']}", details) from None


def to_response(result: Any, status: int) -> Response:
    if isinstance(result, Response):
        return result
    return JSONResponse(to_jsonable_python(result), status_code=status)


class Router:
    def __init__(self) -> None:
        self.endpoints: list[Endpoint] = []

    def add(self, method: str, path: str, *, summary: str, tags: list[str] | None = None,
            body: type[BaseModel] | None = None, response: type[BaseModel] | None = None,
            query: list[tuple[str, str, str]] | None = None, status: int = 200,
            expensive: bool = False, multipart: bool = False, public: bool = False):
        def deco(fn: Handler) -> Handler:
            self.endpoints.append(Endpoint(method.upper(), path, fn, summary, tags or [], body, response,
                                           query or [], status, expensive, multipart, public))
            return fn
        return deco

    def get(self, path: str, **kw):
        return self.add("GET", path, **kw)

    def post(self, path: str, **kw):
        return self.add("POST", path, **kw)

    def put(self, path: str, **kw):
        return self.add("PUT", path, **kw)

    def patch(self, path: str, **kw):
        return self.add("PATCH", path, **kw)

    def delete(self, path: str, **kw):
        return self.add("DELETE", path, **kw)

    def routes(self) -> list[Route]:
        return [Route(ep.path, _make_endpoint(ep), methods=[ep.method]) for ep in self.endpoints]


def _make_endpoint(ep: Endpoint):
    async def endpoint(request: Request) -> Response:
        vyra = request.app.state.vyra
        if ep.expensive:
            vyra.limiter.check(request)
        kwargs: dict[str, Any] = {}
        if ep.body is not None:
            kwargs["body"] = await parse_json(request, ep.body, vyra.settings.max_json_bytes)
        result = await ep.handler(request, **kwargs)
        return to_response(result, ep.status)

    endpoint.__name__ = ep.handler.__name__
    return endpoint


# --------------------------------------------------------------------------- OpenAPI

_PATH_PARAM = re.compile(r"{([a-zA-Z_][a-zA-Z0-9_]*)}")


def build_openapi(endpoints: list[Endpoint], title: str, version: str, description: str) -> dict:
    components: dict[str, Any] = {}

    def schema_ref(model: type[BaseModel]) -> dict:
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        defs = schema.pop("$defs", {})
        components.update(defs)
        components[model.__name__] = schema
        return {"$ref": f"#/components/schemas/{model.__name__}"}

    paths: dict[str, Any] = {}
    for ep in endpoints:
        params = [{"name": n, "in": "path", "required": True, "schema": {"type": "string"}}
                  for n in _PATH_PARAM.findall(ep.path)]
        params += [{"name": n, "in": "query", "required": False, "description": d, "schema": {"type": t}}
                   for n, t, d in ep.query]
        op: dict[str, Any] = {
            "summary": ep.summary,
            "tags": ep.tags,
            "operationId": f"{ep.method.lower()}_{ep.handler.__name__}",
            "parameters": params,
            "responses": {
                str(ep.status): {"description": "Success"},
                "4XX": {"description": "Error (JSON: {error:{code,message,details?}})"},
            },
        }
        if ep.response is not None:
            op["responses"][str(ep.status)]["content"] = {"application/json": {"schema": schema_ref(ep.response)}}
        if ep.body is not None:
            op["requestBody"] = {"required": True, "content": {"application/json": {"schema": schema_ref(ep.body)}}}
        elif ep.multipart:
            op["requestBody"] = {"required": True, "content": {"multipart/form-data": {"schema": {
                "type": "object", "properties": {"file": {"type": "string", "format": "binary"}}}}}}
        if ep.expensive:
            op["description"] = "Rate limited: calls a paid or quota-limited AI provider."
        paths.setdefault(ep.path, {})[ep.method.lower()] = op
    return {
        "openapi": "3.1.0",
        "info": {"title": title, "version": version, "description": description},
        "paths": paths,
        "components": {"schemas": components, "securitySchemes": {
            "VyraToken": {"type": "apiKey", "in": "header", "name": "X-Vyra-Token"}}},
    }


SWAGGER_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>VYRA API docs</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui.css"></head>
<body><div id="ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
<script>window.ui=SwaggerUIBundle({url:'/api/openapi.json',dom_id:'#ui'});</script>
<noscript>Enable JavaScript, or read the raw spec at <a href="/api/openapi.json">/api/openapi.json</a>.</noscript>
</body></html>"""
