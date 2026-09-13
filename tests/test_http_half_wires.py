from __future__ import annotations

from pathlib import Path

from repository_inventory.builder import build_semantic_payload
from repository_inventory.reduction import reduce_inventory_payload


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _http_rows(payload: dict) -> list[dict]:
    rows = []
    for family in payload["structural_families"]:
        if family.get("family_kind") != "http_boundary_observation":
            continue
        row = dict(family.get("descriptor") or {})
        row["occurrence_count"] = family["occurrence_count"]
        row["file_count"] = family["file_count"]
        row["provenance"] = family["provenance"]
        rows.append(row)
    return rows


def test_direct_resttemplate_exchange_resolves_exact_value_binding(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "config/app.yml", "client:\n  path: /hello\n")
    _write(
        repo,
        "module/src/main/java/demo/Client.java",
        '''class Client {
  @Value("${client.path}") String path;
  RestTemplate restTemplate;
  void call() { restTemplate.exchange(path, HttpMethod.POST, null, String.class); }
}
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="direct-http")
    row = next(row for row in _http_rows(payload) if row.get("direction") == "outbound")
    assert row["method"] == "POST"
    assert row["path"] == "/hello"
    assert row["path_status"] == "resolved"
    assert row["property_keys"] == ["client.path"]


def test_one_hop_wrapper_is_projected_at_concrete_callsite_not_internal_forwarder(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "config/base.json", '{"client.path":{"stringValue":{"default":"/wrapped"}}}')
    _write(
        repo,
        "module/src/main/java/demo/Sender.java",
        '''class Sender {
  static void send(Object body, RestTemplate restTemplate, String path) {
    restTemplate.exchange(path, POST, body, String.class);
  }
}
''',
    )
    _write(
        repo,
        "module/src/main/java/demo/Caller.java",
        '''class Caller {
  @Value("${client.path}") String path;
  void call() { send(null, restTemplate, path); }
}
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="wrapped-http")
    outbound = [row for row in _http_rows(payload) if row.get("direction") == "outbound"]
    assert len(outbound) == 1
    assert outbound[0]["path"] == "/wrapped"
    assert outbound[0]["occurrence_count"] == 1


def test_conflicting_repository_local_config_is_preserved_as_ambiguity(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "config/base.json", '{"client.path":{"stringValue":{"default":"/from-base"}}}')
    _write(repo, "module/src/main/resources/application.yml", "client:\n  path: /from-app\n")
    _write(
        repo,
        "module/src/main/java/demo/Client.java",
        '''class Client {
  @Value("${client.path}") String path;
  RestTemplate restTemplate;
  void call() { restTemplate.exchange(path, POST, null, String.class); }
}
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="ambiguous-http")
    row = next(row for row in _http_rows(payload) if row.get("direction") == "outbound")
    assert row["path"] is None
    assert row["path_status"] == "ambiguous_declared_config"
    assert row["path_candidates"] == ["/from-app", "/from-base"]
    claims = row["provenance"]["claim_variants"]
    assert claims == [{"classification": "ambiguity", "confidence": 0.5, "basis": "java_value_binding_with_multiple_declared_structured_scalars"}]


def test_openapi_and_literal_java_route_collapse_to_one_inbound_boundary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "api/openapi.yaml",
        '''openapi: 3.0.0
paths:
  /updatePhoneFlags:
    post:
      responses: {}
''',
    )
    _write(
        repo,
        "module/src/main/java/demo/Controller.java",
        '''class Controller {
  @Post("/updatePhoneFlags")
  void update() {}
}
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="inbound-http")
    inbound = [row for row in _http_rows(payload) if row.get("direction") == "inbound"]
    assert len(inbound) == 1
    assert inbound[0]["method"] == "POST"
    assert inbound[0]["path"] == "/updatePhoneFlags"
    assert inbound[0]["file_count"] == 2
    assert len(inbound[0]["provenance"]["basis_variants"]) == 2


def test_dynamic_path_is_not_guessed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "module/src/main/java/demo/Client.java",
        '''class Client {
  RestTemplate restTemplate;
  String prefix;
  void call(String id) { restTemplate.exchange(prefix + id, POST, null, String.class); }
}
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="dynamic-http")
    row = next(row for row in _http_rows(payload) if row.get("direction") == "outbound")
    assert row["path"] is None
    assert row["path_candidates"] == []
    assert row["path_status"] == "unresolved_expression"


def test_reduced_v3_keeps_http_path_as_observed_identity_without_second_reducer(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "config/app.yml", "client:\n  path: /hello\n")
    _write(
        repo,
        "module/src/main/java/demo/Client.java",
        '''class Client {
  @Value("${client.path}") String path;
  RestTemplate restTemplate;
  void call() { restTemplate.exchange(path, POST, null, String.class); }
}
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="reduced-http")
    reduced = reduce_inventory_payload(payload)
    identities = [row for row in reduced["observed_identities"] if row["identity"].get("family_kind") == "http_boundary_observation"]
    assert identities
    assert {row["identity"].get("family_label") for row in identities} == {"/hello"}
    values = {facet["value"] for row in identities for facet in row["identity"].get("string_facets") or []}
    assert "client.path" in values
    exact = [row for row in reduced["exact_representations"] if row["exact_representation_basis"]["family_kind"] == "http_boundary_observation"]
    assert exact
    assert exact[0]["exact_representation_basis"]["observed_metrics"]["direction"] == "outbound"
    assert exact[0]["exact_representation_basis"]["observed_metrics"]["method"] == "POST"


def test_exact_java_constants_and_explicit_service_registration_compose_inbound_route(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "module/src/main/java/demo/Routes.java",
        '''package demo;
public final class Routes {
  public static final String PREFIX = "/sberProfileId";
  public static final String METHOD = "search";
  public static final String SEARCH = "/" + METHOD;
}
''',
    )
    _write(
        repo,
        "module/src/main/java/demo/SearchService.java",
        '''package demo;
interface SearchService {}
''',
    )
    _write(
        repo,
        "module/src/main/java/demo/SearchRestService.java",
        '''package demo;
class SearchRestService implements SearchService {
  @Post(Routes.SEARCH)
  void search() {}
}
''',
    )
    _write(
        repo,
        "module/src/main/java/demo/Config.java",
        '''package demo;
class Config {
  void configure(SearchService searchService) {
    serverBuilder.annotatedService(Routes.PREFIX, searchService, decorator);
  }
}
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="registered-java-http")
    inbound = [row for row in _http_rows(payload) if row.get("direction") == "inbound"]
    assert [(row["method"], row["path"]) for row in inbound] == [("POST", "/sberProfileId/search")]
    assert inbound[0]["file_count"] == 3


def test_registered_literal_java_route_publishes_only_composed_boundary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "module/src/main/java/demo/Service.java", '''interface Service {}\n''')
    _write(
        repo,
        "module/src/main/java/demo/RestService.java",
        '''class RestService implements Service {
  @Post("/item") void item() {}
}
''',
    )
    _write(
        repo,
        "module/src/main/java/demo/Config.java",
        '''class Config {
  void configure(Service service) { serverBuilder.annotatedService("/api", service, decorator); }
}
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="registered-literal-http")
    inbound = [row for row in _http_rows(payload) if row.get("direction") == "inbound"]
    assert [(row["method"], row["path"]) for row in inbound] == [("POST", "/api/item")]
    assert "/item" not in {row.get("path") for row in inbound}


def test_multiple_exact_service_prefixes_remain_ambiguous(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "module/src/main/java/demo/Service.java", '''interface Service {}\n''')
    _write(
        repo,
        "module/src/main/java/demo/RestService.java",
        '''class RestService implements Service { @Post("/item") void item() {} }\n''',
    )
    _write(
        repo,
        "module/src/main/java/demo/Config.java",
        '''class Config {
  void configure(Service a, Service b) {
    serverBuilder.annotatedService("/one", a, decorator);
    serverBuilder.annotatedService("/two", b, decorator);
  }
}
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="ambiguous-prefix-http")
    row = next(row for row in _http_rows(payload) if row.get("direction") == "inbound")
    assert row["path"] is None
    assert row["path_status"] == "ambiguous_service_registration"
    assert row["path_candidates"] == ["/one/item", "/two/item"]


def test_non_exact_java_route_expression_is_not_guessed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "module/src/main/java/demo/RestService.java",
        '''class RestService { static String route() { return "/x"; } @Post(route()) void item() {} }\n''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="dynamic-java-inbound")
    inbound = [row for row in _http_rows(payload) if row.get("direction") == "inbound"]
    assert inbound == []


def test_openapi_http_boundary_keeps_request_response_payload_and_shallow_fields(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "api/openapi.yaml",
        '''openapi: 3.0.0
paths:
  /items:
    post:
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Request'
      responses:
        '200':
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Response'
components:
  schemas:
    Request:
      type: object
      required: [id]
      properties:
        id: {type: string}
        tags:
          type: array
          items: {type: string}
    Response:
      type: object
      properties:
        status: {type: string}
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="openapi-payload")
    row = next(row for row in _http_rows(payload) if row.get("path") == "/items")
    assert row["request_payload"] == "Request"
    assert row["request_shape_status"] == "available_local_declaration"
    assert [(f["name"], f["collection"], f["required"]) for f in row["request_fields"]] == [
        ("id", "scalar", True),
        ("tags", "array", False),
    ]
    assert row["response_payload"] == "Response"
    reduced = reduce_inventory_payload(payload)
    identities = [row for row in reduced["observed_identities"] if row["identity"].get("family_kind") == "http_boundary_observation"]
    assert any(any(facet.get("path") == "$.request_payload" and facet.get("value") == "Request" for facet in row["identity"].get("string_facets") or []) for row in identities)


def test_direct_java_exchange_projects_local_request_response_payload_shape(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "config/app.yml", "client:\n  path: /search\n")
    _write(
        repo,
        "src/main/java/demo/Client.java",
        '''class Client {
  @Value("${client.path}") String path;
  RestTemplate restTemplate;
  Response call(Request request) {
    HttpEntity<Request> entity = new HttpEntity<>(request);
    return restTemplate.exchange(path, POST, entity, Response.class).getBody();
  }
}
class Request { String id; java.util.List<String> scopes; }
class Response { String status; }
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="java-payload")
    row = next(row for row in _http_rows(payload) if row.get("direction") == "outbound")
    assert row["request_payload"] == "Request"
    assert [(f["name"], f["collection"]) for f in row["request_fields"]] == [("id", "scalar"), ("scopes", "array")]
    assert row["response_payload"] == "Response"
    assert [f["name"] for f in row["response_fields"]] == ["status"]


def test_java_inbound_manual_deserialize_projects_exact_request_payload(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "src/main/java/demo/Controller.java",
        '''class Controller {
  @Post("/search") HttpResponse search(AggregatedHttpRequest request) {
    String json = request.contentUtf8();
    RequestDto dto = converter.deserialize(json, RequestDto.class);
    return HttpResponse.of(OK);
  }
}
class RequestDto { String sberProfileId; String scope; }
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="java-deserialize")
    row = next(row for row in _http_rows(payload) if row.get("direction") == "inbound")
    assert row["request_payload"] == "RequestDto"
    assert row["request_shape_status"] == "available_local_declaration"
    assert [f["name"] for f in row["request_fields"]] == ["sberProfileId", "scope"]


def test_one_hop_wrapper_keeps_external_payload_identity_and_explicit_shape_gap(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, "config/app.yml", "client:\n  path: /flags\n")
    _write(
        repo,
        "src/main/java/demo/Sender.java",
        '''class Sender {
  static <T> ExternalResponse send(T body, RestTemplate restTemplate, String path) {
    HttpEntity<T> entity = new HttpEntity<>(body);
    return restTemplate.exchange(path, POST, entity, ExternalResponse.class).getBody();
  }
}
''',
    )
    _write(
        repo,
        "src/main/java/demo/Caller.java",
        '''class Caller {
  @Value("${client.path}") String path;
  ExternalResponse call(ExternalRequest request, RestTemplate restTemplate) {
    return send(request, restTemplate, path);
  }
}
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="wrapper-external-payload")
    row = next(row for row in _http_rows(payload) if row.get("direction") == "outbound")
    assert row["request_payload"] == "ExternalRequest"
    assert row["request_shape_status"] == "unavailable_external_declaration"
    assert "request_fields" not in row
    assert row["response_payload"] == "ExternalResponse"
    assert row["response_shape_status"] == "unavailable_external_declaration"


def test_jdk_http_client_builder_projects_config_bound_payloads(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "src/main/resources/application.yml",
        "http:\n  ucp:\n    cpcGet:\n      url: http://localhost:8096/stub/cpcGet\n",
    )
    _write(
        repo,
        "src/main/java/demo/UcpClient.java",
        '''class UcpClient {
  HttpClient httpClient;
  ObjectMapper objectMapper;
  URI url;

  UcpClient(HttpProperties httpProperties) {
    HttpProperties.ApiProperties apiProperties = httpProperties.getUcp().getCpcGet();
    url = URI.create(apiProperties.getUrl());
  }

  Response call() throws Exception {
    Request request = new Request();
    HttpRequest.Builder httpRequest = HttpRequest.newBuilder()
        .POST(HttpRequest.BodyPublishers.ofString(objectMapper.writeValueAsString(request)))
        .uri(url);
    HttpResponse<String> response = httpClient.send(httpRequest.build(), HttpResponse.BodyHandlers.ofString());
    return objectMapper.readValue(response.body(), Response.class);
  }
}
class Request { String ucpID; String rqUID; }
class Response { String birthDate; String name; }
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="jdk-http")
    outbound = [row for row in _http_rows(payload) if row.get("direction") == "outbound"]
    assert len(outbound) == 1
    row = outbound[0]
    assert row["method"] == "POST"
    assert row["path"] is None
    assert row["path_status"] == "unresolved_property"
    assert row["property_keys"] == ["http.ucp.cpcGet.url"]
    assert row["request_payload"] == "Request"
    assert [field["name"] for field in row["request_fields"]] == ["ucpID", "rqUID"]
    assert row["response_payload"] == "Response"
    assert [field["name"] for field in row["response_fields"]] == ["birthDate", "name"]

    reduced = reduce_inventory_payload(payload)
    identities = [
        item for item in reduced["observed_identities"]
        if item["identity"].get("family_kind") == "http_boundary_observation"
    ]
    assert len(identities) == 1
    assert identities[0]["identity"].get("family_label") == "http.ucp.cpcGet.url"
    facets = identities[0]["identity"].get("string_facets") or []
    assert any(facet.get("value") == "Request" for facet in facets)
    assert any(facet.get("value") == "Response" for facet in facets)


def test_jdk_http_builder_without_send_is_not_a_boundary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "src/main/java/demo/BuilderOnly.java",
        '''class BuilderOnly {
  void prepare(URI url) {
    HttpRequest.Builder request = HttpRequest.newBuilder().POST(noBody()).uri(url);
  }
}
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="jdk-builder-only")
    assert [row for row in _http_rows(payload) if row.get("direction") == "outbound"] == []
