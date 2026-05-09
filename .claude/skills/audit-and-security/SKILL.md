---
name: audit-and-security
description: Audit log architecture, encryption, key management, and tamper-evidence. Use when implementing audit writers, key storage, hash chaining, HMAC signatures, external anchoring, or HSM integration. Covers tier-based key management strategies (sealed file → Vault → HSM) and the regulatory-defensible audit chain.
---

# Audit Log Architecture & Security

## Overview

Audit logs are the regulator-facing record of what the system did. They serve three purposes:

1. **Compliance evidence** — what happened, when, who initiated it, what data was involved
2. **Forensic investigation** — reconstructing incidents
3. **Customer assurance** — proving the system behaved as specified

Three properties matter:
- **Confidentiality** — sensitive fields encrypted; only authorized principals can decrypt
- **Integrity** — tamper-evident; modification or deletion detectable
- **Availability** — zero data loss invariant; system fails closed if audit unhealthy

## Schema

```sql
CREATE TABLE audit_log (
    id BIGSERIAL PRIMARY KEY,
    sequence_number BIGINT NOT NULL UNIQUE,  -- monotonic per data plane
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    customer_id UUID NOT NULL,
    request_id UUID NOT NULL,
    event_type VARCHAR(64) NOT NULL,  -- 'detection', 'substitution', 'exception_applied', 'rule_change', etc.
    actor_type VARCHAR(32) NOT NULL,  -- 'system', 'user', 'admin'
    actor_id VARCHAR(128),

    -- Encrypted sensitive payload
    encrypted_payload BYTEA NOT NULL,
    encryption_key_id VARCHAR(64) NOT NULL,  -- enables key rotation
    encryption_iv BYTEA NOT NULL,
    encryption_tag BYTEA NOT NULL,

    -- Tamper-evidence
    content_hash BYTEA NOT NULL,  -- SHA-256 of structured fields + encrypted_payload
    previous_hash BYTEA NOT NULL,  -- SHA-256 of previous entry's content_hash; zero for genesis
    hmac_signature BYTEA NOT NULL  -- HMAC-SHA256 over content_hash + previous_hash + sequence_number
);

CREATE INDEX audit_log_customer_time_idx ON audit_log (customer_id, timestamp DESC);
CREATE INDEX audit_log_request_idx ON audit_log (request_id);
CREATE UNIQUE INDEX audit_log_seq_idx ON audit_log (sequence_number);

-- Row-level security (defense in depth)
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY audit_customer_isolation ON audit_log
    FOR ALL USING (customer_id = current_setting('app.current_customer_id')::uuid);

-- External anchors (chain head hashes anchored externally)
CREATE TABLE audit_chain_anchors (
    id BIGSERIAL PRIMARY KEY,
    sequence_number BIGINT NOT NULL,  -- last entry covered by this anchor
    chain_head_hash BYTEA NOT NULL,
    anchored_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    anchor_destination VARCHAR(64) NOT NULL,  -- 'master_plane' or 'rfc3161:<authority>'
    anchor_response BYTEA NOT NULL,  -- the timestamping token or master plane signature
    FOREIGN KEY (sequence_number) REFERENCES audit_log(sequence_number)
);
```

## Encryption

### Algorithm

AES-256-GCM. Authenticated encryption with associated data (AEAD). Same algorithm everywhere; only key storage varies by tier.

### Encrypted payload

The `encrypted_payload` field contains a serialized JSON structure with sensitive details:
- Detected entities (values, types, spans)
- Substituted synthetic values
- Customer-specific rule references
- Input/output text excerpts (if configured for debug retention)

Non-sensitive metadata (timestamps, customer_id, event_type, sequence_number) lives in unencrypted columns to support efficient querying without decryption.

### Key versioning

The `encryption_key_id` field allows multiple keys to coexist during rotation:

```python
class AuditEncryptor:
    def __init__(self, key_provider: KeyProvider):
        self.key_provider = key_provider

    async def encrypt(self, plaintext: bytes) -> EncryptedRecord:
        # Always use the current key for new encryption
        current_key_id, current_key = await self.key_provider.get_current_key()
        iv = secrets.token_bytes(12)
        cipher = AESGCM(current_key)
        ciphertext_with_tag = cipher.encrypt(iv, plaintext, None)
        return EncryptedRecord(
            payload=ciphertext_with_tag[:-16],
            tag=ciphertext_with_tag[-16:],
            iv=iv,
            key_id=current_key_id,
        )

    async def decrypt(self, record: EncryptedRecord) -> bytes:
        # Use the key referenced by the record's key_id (could be old key during rotation)
        key = await self.key_provider.get_key(record.key_id)
        cipher = AESGCM(key)
        return cipher.decrypt(record.iv, record.payload + record.tag, None)
```

## Key management — three tiers

### Standard tiers (Starter, Professional)

Sealed file with restrictive permissions:
- Mode `0400`, owned by the proxy service user, on a separate volume from the database
- Key loaded once at proxy startup, held in process memory, never logged
- File lives at `/etc/llm-privacy-gateway/keys/audit_encryption_key.bin`
- Volume is encrypted at rest (LUKS, EBS encryption, etc.) as defense-in-depth

```python
class SealedFileKeyProvider:
    def __init__(self, key_directory: Path):
        self.key_directory = key_directory
        self._cache: dict[str, bytes] = {}

    async def get_current_key(self) -> tuple[str, bytes]:
        current_key_id = await self._read_current_key_id()
        return current_key_id, await self.get_key(current_key_id)

    async def get_key(self, key_id: str) -> bytes:
        if key_id in self._cache:
            return self._cache[key_id]
        key_path = self.key_directory / f"{key_id}.bin"
        if not key_path.exists() or not self._has_correct_permissions(key_path):
            raise KeyAccessError(f"Key {key_id} unavailable or has incorrect permissions")
        self._cache[key_id] = key_path.read_bytes()
        return self._cache[key_id]
```

### Enterprise tier (HashiCorp Vault — optional)

Customer chooses based on existing infrastructure. If they run Vault, integrate via Vault's transit secret engine. Otherwise fall back to sealed file.

```python
class VaultKeyProvider:
    def __init__(self, vault_url: str, mount_path: str = "transit", key_name: str = "audit_encryption"):
        self.client = hvac.AsyncClient(url=vault_url)  # auth via mTLS or AppRole
        self.mount_path = mount_path
        self.key_name = key_name

    async def get_current_key(self) -> tuple[str, bytes]:
        # Vault transit engine doesn't expose raw keys; we use Vault's encrypt/decrypt API instead
        # Adapter pattern: present same KeyProvider interface; encryption happens server-side
        ...
```

For Enterprise tier with Vault, the encryption pattern shifts — encryption operations happen via Vault API rather than locally. The `AuditEncryptor` adapts.

### Sovereign tier (HSM via PKCS#11)

Customer-supplied HSM (their existing enterprise HSM) or vendor-supplied appliance. Keys generated and stored inside the HSM. Encrypt/decrypt operations executed by the HSM. Keys NEVER leave the HSM.

```python
class HSMKeyProvider:
    def __init__(self, pkcs11_lib_path: str, slot_id: int, pin: str):
        self.lib = PyKCS11.PyKCS11Lib()
        self.lib.load(pkcs11_lib_path)
        self.slot_id = slot_id
        self.pin = pin

    async def encrypt(self, plaintext: bytes) -> tuple[str, bytes, bytes, bytes]:
        # HSM-side encryption — key never extracted
        session = self.lib.openSession(self.slot_id)
        try:
            session.login(self.pin)
            key_handle = session.findObjects([
                (PyKCS11.CKA_LABEL, "audit_encryption_key"),
                (PyKCS11.CKA_CLASS, PyKCS11.CKO_SECRET_KEY),
            ])[0]
            iv = secrets.token_bytes(12)
            mechanism = PyKCS11.Mechanism(PyKCS11.CKM_AES_GCM, PyKCS11.AES_GCM_PARAMS(iv=iv, tagLen=16))
            ciphertext_with_tag = bytes(session.encrypt(key_handle, plaintext, mechanism))
            return "hsm:audit_v1", iv, ciphertext_with_tag[:-16], ciphertext_with_tag[-16:]
        finally:
            session.logout()
            session.closeSession()
```

For HSM, the `AuditEncryptor` interface is reshaped: encryption is delegated to the HSM provider rather than performed locally.

### Hard requirements (all tiers)

1. **Key MUST NOT live in the same database as the encrypted audit data.** If the DB is compromised, attacker has data but not key.
2. **Key MUST NOT appear in any log output.** CI test scans logs for known key bytes:
   ```python
   def test_no_key_bytes_in_logs(caplog):
       known_test_key = b"\x00" * 32
       audit_writer.encrypt(b"test data")
       for record in caplog.records:
           assert known_test_key.hex() not in record.message
   ```
3. **Key rotation supported.** Quarterly default for sealed-file; on-demand for Vault and HSM. Old keys retained for re-decrypting historical records during retention period.
4. **Key access requires authenticated principal.** No anonymous key reads.

## Tamper-evidence

### Hash chain construction

Each audit entry contains:
- Structured fields (sequence_number, timestamp, customer_id, etc.)
- Encrypted payload + IV + tag + key_id
- `content_hash` = SHA-256 of the canonical serialization of the above
- `previous_hash` = previous entry's `content_hash` (or zero for genesis)
- `hmac_signature` = HMAC-SHA256 over `content_hash || previous_hash || sequence_number` using a separate tamper-evidence key

The HMAC key is separate from the encryption key. Compromising one doesn't compromise the other. HMAC key follows the same tier-based storage strategy (sealed file / Vault / HSM).

```python
class AuditChainWriter:
    def __init__(
        self,
        encryptor: AuditEncryptor,
        hmac_key_provider: KeyProvider,
        db: Database,
    ):
        self.encryptor = encryptor
        self.hmac_key_provider = hmac_key_provider
        self.db = db

    async def write(self, entry: AuditEntry) -> None:
        async with self.db.transaction():
            # Lock to ensure sequential append
            await self.db.execute("LOCK TABLE audit_log IN EXCLUSIVE MODE")

            # Get previous entry's content_hash
            prev_row = await self.db.fetch_one(
                "SELECT content_hash, sequence_number FROM audit_log ORDER BY sequence_number DESC LIMIT 1"
            )
            previous_hash = prev_row["content_hash"] if prev_row else b"\x00" * 32
            sequence_number = (prev_row["sequence_number"] + 1) if prev_row else 1

            # Encrypt payload
            encrypted = await self.encryptor.encrypt(entry.payload_bytes())

            # Compute content hash
            canonical = self._canonicalize(entry, encrypted, sequence_number)
            content_hash = hashlib.sha256(canonical).digest()

            # HMAC signature
            hmac_key_id, hmac_key = await self.hmac_key_provider.get_current_key()
            hmac_input = content_hash + previous_hash + sequence_number.to_bytes(8, "big")
            hmac_signature = hmac.new(hmac_key, hmac_input, hashlib.sha256).digest()

            # Insert
            await self.db.execute("""
                INSERT INTO audit_log (
                    sequence_number, timestamp, customer_id, request_id,
                    event_type, actor_type, actor_id,
                    encrypted_payload, encryption_key_id, encryption_iv, encryption_tag,
                    content_hash, previous_hash, hmac_signature
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
            """, sequence_number, entry.timestamp, ...)
```

### Verification

```python
class AuditChainVerifier:
    async def verify(
        self,
        start_seq: int = 1,
        end_seq: int | None = None,
    ) -> VerificationResult:
        results = VerificationResult()
        prev_hash = b"\x00" * 32

        async for row in self._stream_entries(start_seq, end_seq):
            # 1. Recompute content_hash
            recomputed = hashlib.sha256(self._canonicalize_row(row)).digest()
            if recomputed != row["content_hash"]:
                results.add_failure(row["sequence_number"], "content_hash_mismatch")

            # 2. Verify chain linkage
            if row["previous_hash"] != prev_hash:
                results.add_failure(row["sequence_number"], "chain_link_broken")

            # 3. Verify HMAC
            hmac_key = await self.hmac_key_provider.get_key_by_signing(row)
            expected_hmac = hmac.new(
                hmac_key,
                row["content_hash"] + row["previous_hash"] + row["sequence_number"].to_bytes(8, "big"),
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(expected_hmac, row["hmac_signature"]):
                results.add_failure(row["sequence_number"], "hmac_invalid")

            prev_hash = row["content_hash"]

        # 4. Verify against external anchors
        await self._verify_anchors(results, start_seq, end_seq)

        return results
```

### External anchoring

Hash chaining alone doesn't protect against full data plane compromise (attacker could rewrite history AND the chain head). External anchoring closes this gap.

Every hour OR every 1000 entries (whichever first), the current chain head hash is anchored externally:

**Standard tiers** — anchor sent to the master plane via the existing telemetry channel:

```python
async def anchor_to_master(self, sequence_number: int, chain_head_hash: bytes):
    response = await self.master_client.post("/audit-anchors", json={
        "data_plane_id": self.data_plane_id,
        "sequence_number": sequence_number,
        "chain_head_hash": chain_head_hash.hex(),
    })
    # Master plane returns a signed acknowledgment
    await self.db.execute("""
        INSERT INTO audit_chain_anchors (sequence_number, chain_head_hash, anchor_destination, anchor_response)
        VALUES ($1, $2, 'master_plane', $3)
    """, sequence_number, chain_head_hash, response.content)
```

**Sovereign tier** — anchor sent to a customer-designated RFC 3161 timestamping authority (TSA). No master plane connectivity required:

```python
async def anchor_to_tsa(self, sequence_number: int, chain_head_hash: bytes):
    # RFC 3161 timestamp request
    tsq = TimeStampReq(
        version=1,
        message_imprint=MessageImprint(
            hash_algorithm=AlgorithmIdentifier(algorithm=SHA256_OID),
            hashed_message=chain_head_hash,
        ),
        cert_req=True,
    )
    response = await self.tsa_client.post(
        self.tsa_url,
        content=tsq.dump(),
        headers={"Content-Type": "application/timestamp-query"},
    )
    timestamp_token = response.content  # signed by TSA
    await self.db.execute("""
        INSERT INTO audit_chain_anchors (sequence_number, chain_head_hash, anchor_destination, anchor_response)
        VALUES ($1, $2, $3, $4)
    """, sequence_number, chain_head_hash, f"rfc3161:{self.tsa_authority_name}", timestamp_token)
```

If an attacker rewrites history on the data plane, the rewritten chain won't match the anchored hashes. Verification compares chain head against external anchors and detects the discrepancy.

## Zero data loss invariant

Audit log writes are blocking. If the audit DB is unhealthy, requests fail closed.

```python
@router.post("/v1/chat/completions")
async def chat_completions(request, customer_id, ...):
    # ... process request ...
    try:
        await audit_writer.write(audit_entry)
    except AuditWriteError as e:
        # Queue in memory; if queue full, fail the request
        if not await audit_queue.try_enqueue(audit_entry, timeout=5):
            raise HTTPException(503, "Audit subsystem unavailable; request rejected")
        logger.error("audit_write_deferred", error=str(e))
        # Background worker drains the queue when DB recovers
    return final_response
```

This is non-negotiable. We never silently drop audit records to maintain availability.

## Why not blockchain

Some commentators recommend blockchain-anchored audit logs. This was considered and rejected.

Standard hash chain + HMAC + RFC 3161 timestamping provides equivalent tamper-evidence guarantees with significantly lower operational complexity. Public blockchains add latency, cost, and operational dependencies (gas fees, network availability, key management for blockchain identities) without meaningful security improvement for this use case.

If a customer's regulatory regime specifically requires blockchain anchoring, RFC 3161 timestamping can use a blockchain-anchored TSA — preserving the architecture while satisfying the regulatory preference. This is configuration, not architecture.

## Hard rules

1. **Key NEVER lives in the same DB as encrypted data.** Defense in depth.
2. **Key NEVER in logs.** CI test enforces.
3. **Key rotation supported with key versioning.** Old keys retained for retention period.
4. **Audit writes are blocking.** Failure modes never silently drop audit records.
5. **Chain verification is read-only.** Verifying the chain doesn't modify it.
6. **HMAC key separate from encryption key.** Compromising one doesn't compromise both.
7. **External anchoring is mandatory.** Either master plane (standard) or RFC 3161 TSA (Sovereign).
8. **Verification scripts are CI-tested.** Plant a tampered entry in test data, verify the verifier catches it.

## Testing patterns

```python
async def test_chain_verification_detects_modified_entry(audit_chain_with_1000_entries):
    # Modify a single entry's encrypted_payload
    await db.execute(
        "UPDATE audit_log SET encrypted_payload = $1 WHERE sequence_number = 500",
        b"tampered",
    )
    result = await verifier.verify()
    assert result.has_failures
    assert any(f.sequence_number == 500 for f in result.failures)

async def test_chain_verification_detects_deleted_entry(audit_chain_with_1000_entries):
    await db.execute("DELETE FROM audit_log WHERE sequence_number = 500")
    result = await verifier.verify()
    assert result.has_failures
    # Entry 501's previous_hash no longer matches anything

async def test_external_anchor_detects_full_chain_rewrite(audit_chain, master_client_mock):
    # Capture original anchor
    original_anchor = await db.fetch_one("SELECT * FROM audit_chain_anchors ORDER BY id DESC LIMIT 1")

    # Attacker rewrites entire chain
    await db.execute("DELETE FROM audit_log")
    await populate_fake_chain(db)

    # Verify against anchor
    result = await verifier.verify_against_anchor(original_anchor)
    assert not result.is_valid
    assert "chain_head_hash_mismatch" in result.failure_reasons

async def test_no_key_bytes_in_logs(caplog, key_provider):
    test_key = b"\x42" * 32
    await key_provider._cache_key("test", test_key)
    await audit_writer.write(test_audit_entry)
    for record in caplog.records:
        assert test_key.hex() not in record.getMessage()
        assert b"\x42\x42" not in (record.args or {}).values() if record.args else True
```
