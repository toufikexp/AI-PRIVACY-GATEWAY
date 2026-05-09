---
name: detection-pipeline
description: Three-detector ensemble internals. Use when working on detector implementations, the merge engine, retrieval, span validation, or detection accuracy. Covers Detector A (structural validators), Detector B (multilingual NER), Detector C (contextual LLM with RAG), parallel execution, span verification, tier-aware prefix caching, and merge conflict resolution.
---

# Detection Pipeline Internals

## Concurrent execution model

All three detectors run **in parallel via `asyncio.gather`**, not sequentially. Total latency is bounded by the slowest, not the sum.

```python
async def detect(input_text: str, customer_id: UUID) -> List[Detection]:
    # Phase 0: cache check (gates everything)
    cached = await result_cache.get(input_hash, customer_id)
    if cached:
        return cached

    # Phase 1+2: embed + detectors A & B start concurrently
    embed_task = asyncio.create_task(embedding_service.embed(input_text))
    detector_a_task = asyncio.create_task(detector_a.detect(input_text, customer_id))
    detector_b_task = asyncio.create_task(detector_b.detect(input_text))

    # Phase 2c: retrieval starts as soon as embedding completes
    embedding = await embed_task
    rules = await retrieval.hybrid_search(embedding, input_text, customer_id)

    # Phase 3: Detector C starts as soon as retrieval completes
    detector_c_task = asyncio.create_task(detector_c.detect(input_text, rules, customer_id))

    # Wait for all three
    detections_a, detections_b, detections_c = await asyncio.gather(
        detector_a_task, detector_b_task, detector_c_task
    )

    # Phase 4: merge
    merged = merge_engine.merge(detections_a, detections_b, detections_c, input_text, customer_id)

    await result_cache.set(input_hash, customer_id, merged)
    return merged
```

## Detector A — Structural Validators

Pure CPU regex + checksum. No ML. Highest precision (>99% with checksum), bounded recall (only formats it knows about).

### Implementation

```python
class StructuralDetector:
    def __init__(self, country_pack: CountryPack):
        self.patterns = country_pack.patterns  # compiled regex with metadata

    def detect(self, text: str, customer_id: UUID) -> List[Detection]:
        detections = []
        for pattern in self.patterns:
            for match in pattern.regex.finditer(text):
                value = match.group()
                if pattern.validator and not pattern.validator(value):
                    continue  # checksum failed; skip
                if pattern.context_keywords:
                    if not self._has_context(text, match.start(), pattern.context_keywords):
                        continue  # weak context; skip to avoid false positives
                detections.append(Detection(
                    span=(match.start(), match.end()),
                    value=value,
                    entity_type=pattern.entity_type,
                    tier=pattern.tier,
                    confidence=0.99,  # structural detections are high-confidence
                    detector="A",
                ))
        return detections
```

### Source libraries

- `python-stdnum` for many country-specific identifiers (Luhn, MOD-11, country-specific NIN algorithms)
- `phonenumbers` (port of Google's libphonenumber) for phone formats
- ISO standards implementations: 7812 (cards), 9362 (BIC), 13616 (IBAN)

### Adding a new country pattern

1. Source the format spec from authoritative source (national ID authority, central bank, etc.)
2. Implement validator function (checksum, structural rules)
3. Add to country pack with entity_type, tier (typically Tier 1 for national identifiers), substitution strategy
4. Write ≥5 unit tests: valid examples, invalid checksums, edge cases, format variants, false-positive resistance
5. Add to reference corpus for accuracy regression testing

## Detector B — Multilingual NER

Fine-tuned mDeBERTa-v3-base on combined AR/FR/EN NER datasets. ONNX int8 quantized. Runs on CPU.

### Why not larger model

Tested mDeBERTa-base (180M params), DeBERTa-v3-large (440M), XLM-RoBERTa-large (560M). The base model with proper fine-tuning matches large model accuracy on our entity types while running 3x faster on CPU. Larger only helps if you can't curate training data well.

### Fine-tuning data

- Arabic: AraBench, AQMAR, ANER (combined ≈100K labeled sentences)
- French: WikiNeural-fr, MultiCoNER-fr (≈80K)
- English: CoNLL-2003, OntoNotes-5 (≈300K)
- Code-switched: synthetic generation from monolingual data with realistic transition patterns

### Inference path

```python
class NERDetector:
    def __init__(self, model_path: Path):
        self.session = onnxruntime.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
            sess_options=self._optimized_options(),
        )
        self.tokenizer = AutoTokenizer.from_pretrained(model_path)

    async def detect(self, text: str) -> List[Detection]:
        # Run in thread pool to avoid blocking event loop
        return await asyncio.get_event_loop().run_in_executor(
            self._executor, self._detect_sync, text
        )

    def _detect_sync(self, text: str) -> List[Detection]:
        encoded = self.tokenizer(text, return_offsets_mapping=True, truncation=True)
        outputs = self.session.run(None, {"input_ids": encoded["input_ids"], ...})
        spans = self._decode_bio_tags(outputs, encoded["offset_mapping"], text)
        return [self._span_to_detection(span) for span in spans]
```

### Output mapping

Detector B returns generic NER categories (PER, LOC, ORG, DATE, MONEY). The merge engine maps these to tier-specific entity types using the rule base. Don't hardcode the mapping in the detector; keep it in the merger.

## Detector C — Contextual LLM with RAG

Qwen2.5-7B-Instruct AWQ-int4 via vLLM. RAG retrieval over the layered rule base.

### vLLM client

The vLLM server runs as a separate process with OpenAI-compatible API. The detector is just an HTTP client.

```python
class ContextualDetector:
    def __init__(self, vllm_url: str, system_prompt_template: str):
        self.client = httpx.AsyncClient(base_url=vllm_url, timeout=10)
        self.system_prompt_template = system_prompt_template

    async def detect(
        self, text: str, retrieved_rules: List[Rule], customer_id: UUID
    ) -> List[Detection]:
        # Build prompt: cached prefix (Tier 1 + Tier 2) + dynamic Tier 3 + input
        system_prompt = self._build_system_prompt(customer_id, retrieved_rules)
        user_prompt = self._build_user_prompt(text)

        response = await self.client.post("/v1/chat/completions", json={
            "model": "qwen2.5-7b-instruct-awq",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_schema", "schema": DETECTION_SCHEMA},
            "temperature": 0,
            "max_tokens": 1024,
        })

        raw_detections = json.loads(response.json()["choices"][0]["message"]["content"])

        # CRITICAL: span validation. LLM may hallucinate.
        return self._validate_spans(raw_detections, text)

    def _validate_spans(self, raw: List[dict], text: str) -> List[Detection]:
        validated = []
        for d in raw:
            start, end = d["span_start"], d["span_end"]
            if start < 0 or end > len(text) or text[start:end] != d["value"]:
                logger.warning("hallucinated_span_dropped", span=(start, end), value=d["value"])
                continue
            validated.append(Detection(...))
        return validated
```

### Tier-aware prefix caching

vLLM's prefix cache works at token level. The cached prefix per customer:
- System instructions (stable)
- Tier 1 country pack rules for the customer's country (stable per country)
- The subset of Tier 2 industry pack rules the customer has enabled (stable per customer)

This typically totals 800–1500 tokens. After first request, subsequent requests reuse the prefix and only process the dynamic suffix (retrieved Tier 3 rules + input + schema).

```python
def _build_system_prompt(self, customer_id: UUID, retrieved_rules: List[Rule]) -> str:
    # Stable prefix portion — vLLM caches this
    stable = self.system_prompt_template.format(
        country_rules=self._format_rules(self._get_tier1_for_customer(customer_id)),
        industry_rules=self._format_rules(self._get_tier2_for_customer(customer_id)),
    )
    # Dynamic portion — appended per request
    dynamic = self._format_rules(retrieved_rules, header="## Customer-Specific Rules")
    return f"{stable}\n\n{dynamic}"
```

### Why not stuff all rules into the prompt

For a customer with 250 Tier 3 rules at ~150 tokens each: 37,500 tokens for Tier 3 alone. Add Tier 1 + Tier 2: exceeds Qwen2.5-7B's 32K context window. Even when fitting, longer prompts dilute attention. RAG with focused retrieval is both faster and more accurate.

### Structured output schema

```python
DETECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "detections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "span_start": {"type": "integer"},
                    "span_end": {"type": "integer"},
                    "value": {"type": "string"},
                    "entity_type": {"type": "string"},
                    "tier": {"type": "integer", "enum": [1, 2, 3]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "matching_rule_id": {"type": "string"},
                    "reasoning": {"type": "string"},
                },
                "required": ["span_start", "span_end", "value", "entity_type", "tier", "confidence"],
            },
        },
    },
    "required": ["detections"],
}
```

The schema is enforced by vLLM's guided decoding. The LLM cannot return malformed output; it can return empty `detections: []` but cannot omit the field.

## Retrieval — Hybrid pgvector + keyword + tier filter

```python
async def hybrid_search(
    embedding: list[float],
    query_text: str,
    customer_id: UUID,
    top_k: int = 10,
) -> List[Rule]:
    # Always include Tier 1 rules with relevance > low threshold
    # Hybrid score: 0.6 * vector + 0.3 * keyword + 0.1 * recency
    sql = """
    WITH vector_scores AS (
        SELECT id, 1 - (embedding <=> $1::vector) AS vec_score
        FROM rules
        WHERE customer_id IS NULL OR customer_id = $2
        ORDER BY embedding <=> $1::vector
        LIMIT 50
    ),
    keyword_scores AS (
        SELECT id, ts_rank(rule_tsv, plainto_tsquery($3)) AS kw_score
        FROM rules
        WHERE rule_tsv @@ plainto_tsquery($3)
          AND (customer_id IS NULL OR customer_id = $2)
        LIMIT 50
    )
    SELECT r.*, COALESCE(v.vec_score, 0) * 0.6
                + COALESCE(k.kw_score, 0) * 0.3
                + (CASE WHEN r.updated_at > NOW() - INTERVAL '30 days' THEN 0.1 ELSE 0 END)
            AS hybrid_score
    FROM rules r
    LEFT JOIN vector_scores v ON r.id = v.id
    LEFT JOIN keyword_scores k ON r.id = k.id
    WHERE r.id IN (SELECT id FROM vector_scores UNION SELECT id FROM keyword_scores)
       OR (r.tier = 1 AND r.country_id = (SELECT country_id FROM customers WHERE id = $2))
    ORDER BY r.tier ASC, hybrid_score DESC
    LIMIT $4
    """
    rows = await db.fetch(sql, embedding, customer_id, query_text, top_k)
    return [Rule.from_row(row) for row in rows]
```

### HNSW tuning

`CREATE INDEX rules_embedding_idx ON rules USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);`

For >1000 rules, increase `m` to 32. For >10000 rules, also tune `ef_search` per query: `SET LOCAL hnsw.ef_search = 100;`

After bulk inserts: `ANALYZE rules;` — query planner will silently produce bad plans without this.

## Merge engine

```python
def merge(
    detections_a: List[Detection],
    detections_b: List[Detection],
    detections_c: List[Detection],
    input_text: str,
    customer_id: UUID,
) -> List[Detection]:
    all_dets = detections_a + detections_b + detections_c

    # 1. Span coverage validation (idempotent — Detector C already did this; defense in depth)
    all_dets = [d for d in all_dets if input_text[d.span[0]:d.span[1]] == d.value]

    # 2. Group by overlapping spans
    groups = _group_overlapping(all_dets)

    # 3. Resolve each group
    resolved = []
    for group in groups:
        if len(group) == 1:
            resolved.append(group[0])
            continue

        # Longer span wins
        max_len = max(d.span[1] - d.span[0] for d in group)
        candidates = [d for d in group if d.span[1] - d.span[0] == max_len]

        # If equal length: structural > NER > LLM
        candidates.sort(key=lambda d: {"A": 0, "B": 1, "C": 2}[d.detector])

        winner = candidates[0]

        # Tier precedence: if any detector in group claimed higher tier, escalate
        max_tier = min(d.tier for d in group)  # Tier 1 = highest priority = lowest number
        winner = winner._replace(tier=max_tier)

        # Confidence aggregation
        if len(group) > 1:
            winner = winner._replace(confidence=min(0.99, max(d.confidence for d in group) + 0.05))

        resolved.append(winner)

    # 4. Apply customer Tier 1 exceptions
    resolved = exception_engine.apply(resolved, customer_id)

    # 5. Threshold filter
    customer_thresholds = config.get_thresholds(customer_id)
    final = [d for d in resolved if d.confidence >= customer_thresholds[d.tier]]

    return final
```

## Common implementation gotchas

- **Don't await sequentially.** `await detector_a.detect(...); await detector_b.detect(...)` triples your latency. Use `asyncio.gather` or `asyncio.create_task`.
- **Don't trust Detector C spans without validation.** The LLM hallucinates positions even with the schema. Always verify `text[start:end] == value`.
- **Don't forget to release Detector B inference threads.** Use a bounded executor; otherwise concurrent requests can exhaust threads.
- **Don't share vLLM HTTP client across event loops.** httpx clients are tied to one loop; create one per worker process.
- **Don't skip HNSW `ANALYZE`.** Without it, retrieval quietly degrades to sequential scans.
- **Don't put customer-specific data in the cached prefix.** Tier 3 rules are per-request, not prefix.
- **Don't trust LLM-generated `entity_type` strings without validation.** Map them to known entity types via the rule base; reject unknown types.

## Testing patterns

```python
# Mock vLLM in unit tests — never start the real server
@pytest.fixture
def mock_vllm(monkeypatch):
    async def fake_post(self, path, json):
        return MockResponse({
            "choices": [{"message": {"content": json.dumps({"detections": []})}}]
        })
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

# Use parametrize for entity coverage
@pytest.mark.parametrize("text,expected", [
    ("My NIN is 198507150045123456", [("198507150045123456", "ALGERIAN_NIN", 1)]),
    ("198507150045123456 is not a NIN context", []),  # context keyword absent
    ("198507150045123457", []),  # checksum fails
])
def test_algerian_nin_detection(text, expected):
    detector = StructuralDetector(load_country_pack("algeria"))
    detections = detector.detect(text, customer_id=TEST_UUID)
    assert [(d.value, d.entity_type, d.tier) for d in detections] == expected
```
