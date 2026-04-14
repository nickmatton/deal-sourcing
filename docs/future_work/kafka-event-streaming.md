# Kafka (Event Streaming)

## What It Is

Kafka is a free, open-source distributed event streaming platform. At its core, it's a durable, ordered log of events that decouples producers (things that generate data) from consumers (things that process data). Producers write events to "topics," and any number of consumers can read from those topics independently, at their own pace.

Key properties that distinguish Kafka from a simple message queue:
- **Durable.** Messages persist on disk. If a consumer crashes, it picks up where it left off.
- **Replayable.** You can reprocess old events (e.g., rerun last week's data through a new model version).
- **Ordered.** Events within a partition are strictly ordered, which matters for time-series data.
- **High throughput.** Handles millions of events per second. Vastly overkill for this project, but it's why companies use it.

## Why You Might Want It

The pipeline currently runs as a single batch job: CLI starts → stages run sequentially → results printed → done. Kafka would enable an **event-driven architecture** where stages run independently and react to data as it arrives:

1. **Real-time ingestion triggers.** A PitchBook webhook fires when a new deal is announced. Kafka receives the event, and the signal detection stage automatically rescores affected companies without re-running the entire pipeline.

2. **Stage decoupling.** Instead of the CLI orchestrating every stage in sequence, each stage is an independent service that consumes events from the previous stage's output topic. Ingestion writes to `raw-companies`, entity resolution reads from `raw-companies` and writes to `resolved-entities`, signal detection reads from `resolved-entities`, etc. Stages can be deployed, scaled, and restarted independently.

3. **Audit event stream.** Instead of writing audit entries to a JSONL file, publish them to a Kafka topic. Multiple consumers can then process the same audit stream: one writes to long-term storage, another feeds a monitoring dashboard, a third triggers compliance alerts.

4. **Feedback loop.** When deal outcomes arrive (months or years later), publish them to a `deal-outcomes` topic. A retraining consumer watches this topic and triggers model retraining when enough new labeled data accumulates.

## When to Add It

Add Kafka when:
- You need real-time event processing (data arrives continuously, not in batches)
- Multiple independent services need to react to the same events
- You need event replay (reprocess historical data through new model versions)
- You're running the pipeline as always-on services rather than CLI invocations

You do NOT need it when:
- The pipeline runs as a batch job triggered by a human (current state)
- All stages run in a single Python process
- You have fewer than ~1,000 events per day
- You're the only developer

**Simpler alternatives:**
- For basic async processing: Python's `asyncio.Queue` or `multiprocessing.Queue`
- For scheduled batch runs: cron + the existing CLI
- For stage decoupling: Dagster (already partially set up in `pipelines/definitions.py`) handles DAG orchestration without a message broker
- For durable event storage: append to a JSONL file or database table (the audit trail already does this)

## How to Add It to This Pipeline

### 1. Install

```bash
pip install confluent-kafka>=2.0
# or
pip install aiokafka>=0.10  # async variant
```

### 2. Run Kafka

Locally via Docker:

```bash
docker run -d --name kafka \
    -p 9092:9092 \
    -e KAFKA_CFG_NODE_ID=0 \
    -e KAFKA_CFG_PROCESS_ROLES=controller,broker \
    -e KAFKA_CFG_LISTENERS=PLAINTEXT://:9092,CONTROLLER://:9093 \
    -e KAFKA_CFG_LISTENER_SECURITY_PROTOCOL_MAP=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT \
    -e KAFKA_CFG_CONTROLLER_QUORUM_VOTERS=0@localhost:9093 \
    -e KAFKA_CFG_CONTROLLER_LISTENER_NAMES=CONTROLLER \
    bitnami/kafka:3.7
```

### 3. Define Topics

Create topics for each pipeline stage boundary:

```python
TOPICS = {
    "raw-companies": "New company records from ingestion",
    "resolved-entities": "Companies after entity resolution",
    "deal-signals": "Companies scored by signal detection",
    "valuations": "Shadow valuations produced",
    "underwriting-results": "Monte Carlo simulation results",
    "outreach-events": "Outreach attempts and outcomes",
    "deal-outcomes": "Final deal outcomes for feedback loop",
    "audit-events": "All audit trail events",
}
```

### 4. Build Producers and Consumers

Each pipeline stage becomes a producer-consumer pair:

```python
from confluent_kafka import Producer, Consumer
import json

# Producer: ingestion stage publishes normalized companies
producer = Producer({"bootstrap.servers": "localhost:9092"})

def publish_company(company: CompanyNormalized) -> None:
    producer.produce(
        "resolved-entities",
        key=company.entity_id.encode(),
        value=json.dumps(company.model_dump()).encode(),
    )
    producer.flush()

# Consumer: signal detection stage reads companies and scores them
consumer = Consumer({
    "bootstrap.servers": "localhost:9092",
    "group.id": "signal-detection",
    "auto.offset.reset": "earliest",
})
consumer.subscribe(["resolved-entities"])

while True:
    msg = consumer.poll(1.0)
    if msg is None:
        continue
    company = CompanyNormalized.model_validate_json(msg.value())
    signal = score_company(company)
    publish_signal(signal)  # write to "deal-signals" topic
```

### 5. Replace the Audit Logger

The existing `AuditLogger` writes to a JSONL file. A Kafka-backed version publishes to a topic instead:

```python
class KafkaAuditLogger(AuditLogger):
    def __init__(self, bootstrap_servers: str = "localhost:9092") -> None:
        self._producer = Producer({"bootstrap.servers": bootstrap_servers})

    def log(self, entry: AuditEntry) -> None:
        self._producer.produce(
            "audit-events",
            value=json.dumps(entry.model_dump()).encode(),
        )
        self._producer.flush()
```

### 6. Config

```python
# src/common/config.py
class KafkaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KAFKA_")
    bootstrap_servers: str = "localhost:9092"
```

```env
# .env
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
```

## Cost

Free. Kafka is Apache 2.0 licensed. Confluent Cloud (managed hosting) charges based on throughput (~$1/GB ingested), but running locally costs nothing beyond the ~500MB of RAM the broker uses. For this project's volume (hundreds of events per pipeline run, not millions), local Kafka is more than sufficient.

## Recommendation

Kafka is the last piece of infrastructure you should add. Dagster (for batch orchestration) and simple cron jobs cover the pipeline's needs until you have real-time data feeds and multiple services that need to communicate asynchronously. When you get there, Kafka is the right tool -- but that's likely 6-12 months away.
