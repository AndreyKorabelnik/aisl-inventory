from __future__ import annotations

from pathlib import Path

from repository_inventory.builder import build_semantic_payload
from repository_inventory.reduction import reduce_inventory_payload


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _kafka_rows(payload: dict) -> list[dict]:
    rows: list[dict] = []
    for family in payload["structural_families"]:
        if family.get("family_kind") != "kafka_boundary_observation":
            continue
        row = dict(family.get("descriptor") or {})
        row["occurrence_count"] = family["occurrence_count"]
        row["file_count"] = family["file_count"]
        row["provenance"] = family["provenance"]
        rows.append(row)
    return rows


def test_kafka_listener_resolves_required_args_enum_config_key_and_payload(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "src/main/java/demo/Consumer.java",
        '''
@RequiredArgsConstructor
enum Settings { TOPIC("kafka.orders.topic.name"); private final String name; }
@RequiredArgsConstructor
enum ConsumerProps {
  ORDERS("orders.id", Settings.TOPIC);
  private final String id;
  private final Settings topic;
  String getTopic(ApplicationSettings settings) { return settings.getStringValue(topic); }
}
class Base { Base(ConsumerProps props, Object monitor, ApplicationSettings settings) {} }
class Consumer extends Base {
  Consumer(Object monitor, ApplicationSettings settings) { super(ConsumerProps.ORDERS, monitor, settings); }
  @KafkaListener(topics = "#{__listener.props.getTopic(settings)}")
  void onReceive(Object request) { deserialize(request, OrderEvent.class); }
  Object deserialize(Object request, Class<?> type) { return null; }
}
class OrderEvent { String orderId; java.util.List<String> tags; }
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="kafka-consume")
    rows = _kafka_rows(payload)
    assert len(rows) == 1
    row = rows[0]
    assert row["direction"] == "consume"
    assert row["protocol"] == "kafka"
    assert row["topic_identity_kind"] == "config_key"
    assert row["topic_identity"] == "kafka.orders.topic.name"
    assert row["topic_status"] == "resolved"
    assert row["payload_identity"] == "OrderEvent"
    assert [(f["name"], f["collection"]) for f in row["payload_fields"]] == [("orderId", "scalar"), ("tags", "array")]

    reduced = reduce_inventory_payload(payload)
    identities = [row for row in reduced["observed_identities"] if row["identity"].get("family_kind") == "kafka_boundary_observation"]
    assert len(identities) == 1
    identity = identities[0]["identity"]
    assert identity["family_label"] == "kafka.orders.topic.name"
    facets = identity.get("string_facets") or []
    assert any(f.get("path") == "$.payload_identity" and f.get("value") == "OrderEvent" for f in facets)


def test_kafka_template_publish_resolves_method_local_config_key_and_payload(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "src/main/java/demo/Producer.java",
        '''
enum Settings { TOPIC("kafka.events.topic.name"); Settings(String name) {} }
class Producer {
  ApplicationSettings settings;
  KafkaTemplate<Object,Object> kafkaTemplate;
  void send(Event event) {
    String topic = settings.getStringValue(Settings.TOPIC);
    kafkaTemplate.send(topic, event.getId(), event);
  }
}
class Event { String id; }
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="kafka-publish")
    row = next(row for row in _kafka_rows(payload) if row.get("direction") == "publish")
    assert row["topic_identity_kind"] == "config_key"
    assert row["topic_identity"] == "kafka.events.topic.name"
    assert row["payload_identity"] == "Event"
    assert [f["name"] for f in row["payload_fields"]] == ["id"]


def test_kafka_producer_record_publish_uses_exact_local_method_return_type(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "src/main/java/demo/Producer.java",
        '''
enum Settings { TOPIC("kafka.sync.topic.name"); Settings(String name) {} }
class Producer {
  ApplicationSettings settings;
  KafkaTemplate<Object,Object> kafkaTemplate;
  void send() {
    String topic = settings.getStringValue(Settings.TOPIC);
    ProducerRecord<Object,Object> record = new ProducerRecord<>(topic, "key", mapEvent());
    kafkaTemplate.send(record);
  }
  SyncEvent mapEvent() { return new SyncEvent(); }
}
class SyncEvent { String id; }
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="kafka-record")
    row = next(row for row in _kafka_rows(payload) if row.get("direction") == "publish")
    assert row["topic_identity"] == "kafka.sync.topic.name"
    assert row["payload_identity"] == "SyncEvent"


def test_kafka_transaction_nested_foreach_keeps_exact_collection_element_payload(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "src/main/java/demo/Producer.java",
        '''
enum Settings { TOPIC("kafka.history.topic.name"); Settings(String name) {} }
class Producer {
  ApplicationSettings settings;
  KafkaTemplate<Object,Object> transactionKafkaTemplate;
  void send(java.util.List<HistoryEvent> events) {
    String topic = settings.getStringValue(Settings.TOPIC);
    transactionKafkaTemplate.executeInTransaction(tpl -> {
      events.forEach(event -> tpl.send(topic, event.getId(), event));
      return null;
    });
  }
}
class HistoryEvent { String id; }
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="kafka-transaction")
    rows = [row for row in _kafka_rows(payload) if row.get("direction") == "publish"]
    assert len(rows) == 1
    assert rows[0]["topic_identity"] == "kafka.history.topic.name"
    assert rows[0]["payload_identity"] == "HistoryEvent"


def test_dynamic_listener_topic_is_preserved_as_unresolved_not_guessed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "src/main/java/demo/Consumer.java",
        '''
@RequiredArgsConstructor
enum Settings { TOPIC("kafka.real.topic.name"); private final String name; }
@RequiredArgsConstructor
enum ConsumerProps {
  EVENTS("id", Settings.TOPIC);
  private final String id;
  private final Settings topic;
  String getTopic(ApplicationSettings settings) { return settings.getStringValue(topic); }
}
class Base { Base(ConsumerProps props) {} }
class Consumer extends Base {
  Consumer() { super(ConsumerProps.EVENTS); }
  @KafkaListener(topics = "#{dynamicTopicProvider.topic()}")
  void onReceive(Object request) { deserialize(request, Event.class); }
  Object deserialize(Object request, Class<?> type) { return null; }
}
class Event { String id; }
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="kafka-dynamic-listener")
    row = next(row for row in _kafka_rows(payload) if row.get("direction") == "consume")
    assert row["topic_identity"] is None
    assert row["topic_identity_kind"] == "unresolved_expression"
    assert row["topic_status"] == "unresolved_listener_topic_accessor"
    assert row["topic_candidates"] == []


def test_dynamic_publish_topic_expression_is_preserved_as_unresolved(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(
        repo,
        "src/main/java/demo/Producer.java",
        '''
class Producer {
  KafkaTemplate<Object,Object> kafkaTemplate;
  void send(Event event) { kafkaTemplate.send(resolveTopic(), event); }
  String resolveTopic() { return "runtime"; }
}
class Event { String id; }
''',
    )
    payload, _, _ = build_semantic_payload(repository=repo, repository_id="kafka-dynamic-publish")
    row = next(row for row in _kafka_rows(payload) if row.get("direction") == "publish")
    assert row["topic_identity"] is None
    assert row["topic_identity_kind"] == "unresolved_expression"
    assert row["topic_status"] == "unresolved_expression"
    assert row["payload_identity"] == "Event"


def _reduced_kafka_candidates(reduced: dict) -> list[dict]:
    exact_by_id = {row["exact_representation_id"]: row for row in reduced["exact_representations"]}
    candidates: list[dict] = []
    for row in reduced["observed_identities"]:
        identity = row.get("identity") or {}
        if identity.get("family_kind") != "kafka_boundary_observation":
            continue
        exact_ids = row.get("exact_representation_ids") or []
        exact = exact_by_id[exact_ids[0]] if exact_ids else {}
        metrics = ((exact.get("exact_representation_basis") or {}).get("observed_metrics") or {})
        facets = identity.get("string_facets") or []
        values_by_path = {facet.get("path"): facet.get("value") for facet in facets}
        fields = sorted(
            facet.get("value")
            for facet in facets
            if isinstance(facet.get("path"), str) and facet["path"].endswith(".name")
        )
        candidates.append(
            {
                "direction": metrics.get("direction"),
                "topic_identity": identity.get("family_label"),
                "topic_status": metrics.get("topic_status"),
                "topic_identity_kind": metrics.get("topic_identity_kind"),
                "payload_identity": values_by_path.get("$.payload_identity"),
                "payload_fields": fields,
            }
        )
    return candidates


def test_literal_kafka_pair_is_exactly_correlatable_from_full_and_reduced_only(tmp_path: Path) -> None:
    producer = tmp_path / "producer"
    consumer = tmp_path / "consumer"
    producer.mkdir()
    consumer.mkdir()
    _write(
        producer,
        "src/main/java/demo/Producer.java",
        '''
class CustomerEvent { String customerId; String[] tags; }
class Producer {
  KafkaTemplate<String, CustomerEvent> kafkaTemplate;
  void send(CustomerEvent event) { kafkaTemplate.send("customer-events", event); }
}
''',
    )
    _write(
        consumer,
        "src/main/java/demo/Consumer.java",
        '''
class CustomerEvent { String customerId; String[] tags; }
class Consumer {
  @KafkaListener(topics = "customer-events")
  void onMessage(String value) { deserialize(value, CustomerEvent.class); }
  <T> T deserialize(String value, Class<T> type) { return null; }
}
''',
    )

    producer_full, _, _ = build_semantic_payload(repository=producer, repository_id="literal-producer")
    consumer_full, _, _ = build_semantic_payload(repository=consumer, repository_id="literal-consumer")

    # Acceptance-only matcher: reads completed Inventory artifacts, never repository source.
    producer_row = next(row for row in _kafka_rows(producer_full) if row["direction"] == "publish")
    consumer_row = next(row for row in _kafka_rows(consumer_full) if row["direction"] == "consume")
    assert consumer_row["topic_identity_kind"] == "literal"
    assert consumer_row["topic_identity"] == "customer-events"
    assert consumer_row["topic_status"] == "resolved"
    assert consumer_row["provenance"]["claim_variants"] == [
        {"classification": "observed_fact", "confidence": 1.0, "basis": "kafka_listener_literal_topic"}
    ]
    assert producer_row["topic_identity"] == consumer_row["topic_identity"]
    assert producer_row["payload_identity"] == consumer_row["payload_identity"] == "CustomerEvent"
    assert [f["name"] for f in producer_row["payload_fields"]] == ["customerId", "tags"]
    assert [f["name"] for f in consumer_row["payload_fields"]] == ["customerId", "tags"]

    producer_reduced = _reduced_kafka_candidates(reduce_inventory_payload(producer_full))
    consumer_reduced = _reduced_kafka_candidates(reduce_inventory_payload(consumer_full))
    producer_candidate = next(row for row in producer_reduced if row["direction"] == "publish")
    consumer_candidate = next(row for row in consumer_reduced if row["direction"] == "consume")
    assert producer_candidate["topic_status"] == consumer_candidate["topic_status"] == "resolved"
    assert producer_candidate["topic_identity_kind"] == consumer_candidate["topic_identity_kind"] == "literal"
    assert producer_candidate["topic_identity"] == consumer_candidate["topic_identity"] == "customer-events"
    assert producer_candidate["payload_identity"] == consumer_candidate["payload_identity"] == "CustomerEvent"
    assert producer_candidate["payload_fields"] == consumer_candidate["payload_fields"] == ["customerId", "tags"]
