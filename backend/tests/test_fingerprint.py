from app.fingerprint import canonicalize_fields, fingerprint_payload, fingerprint_record


def test_key_order_does_not_affect_fingerprint():
    a = {"a": 1, "b": 2}
    b = {"b": 2, "a": 1}
    assert fingerprint_payload(a) == fingerprint_payload(b)


def test_nested_key_order_does_not_affect_fingerprint():
    a = {"outer": {"x": 1, "y": 2}}
    b = {"outer": {"y": 2, "x": 1}}
    assert fingerprint_payload(a) == fingerprint_payload(b)


def test_different_values_produce_different_fingerprints():
    assert fingerprint_payload({"a": 1}) != fingerprint_payload({"a": 2})


def test_missing_key_and_explicit_none_are_equivalent_after_canonicalization():
    fields = ["a", "b"]
    missing = canonicalize_fields({"a": 1}, fields)
    explicit_none = canonicalize_fields({"a": 1, "b": None}, fields)
    assert missing == explicit_none
    assert fingerprint_payload(missing) == fingerprint_payload(explicit_none)


def test_canonicalize_fields_ignores_keys_not_requested():
    raw = {"a": 1, "b": 2, "volatile": "should not affect fingerprint"}
    fp_with_extra = fingerprint_record(raw, ["a", "b"])
    raw_without_extra = {"a": 1, "b": 2}
    fp_without_extra = fingerprint_record(raw_without_extra, ["a", "b"])
    assert fp_with_extra == fp_without_extra


def test_volatile_field_excluded_from_fingerprint():
    fields = ["external_record_id", "category"]
    record_v1 = {
        "external_record_id": "X-1",
        "category": "theft",
        "observed_at": "2026-01-01T00:00:00Z",
    }
    record_v2 = {
        "external_record_id": "X-1",
        "category": "theft",
        "observed_at": "2026-06-01T00:00:00Z",
    }
    assert fingerprint_record(record_v1, fields) == fingerprint_record(record_v2, fields)


def test_semantically_identical_records_produce_identical_fingerprints():
    fields = ["a", "b", "c"]
    r1 = {"c": 3, "a": 1, "b": 2}
    r2 = {"a": 1, "b": 2, "c": 3}
    assert fingerprint_record(r1, fields) == fingerprint_record(r2, fields)


def test_type_differences_are_not_treated_as_equivalent():
    # Documented behavior: comparison is by exact JSON form, so int 1
    # and string "1" are NOT the same fingerprint. Callers must keep
    # consistent typing when canonicalizing.
    assert fingerprint_payload({"a": 1}) != fingerprint_payload({"a": "1"})


def test_fingerprint_is_deterministic_across_calls():
    payload = {"a": 1, "b": {"nested": True}, "c": [3, 1, 2]}
    assert fingerprint_payload(payload) == fingerprint_payload(dict(payload))


def test_fingerprint_is_sha256_hex():
    digest = fingerprint_payload({"a": 1})
    assert len(digest) == 64
    int(digest, 16)  # raises ValueError if not valid hex
