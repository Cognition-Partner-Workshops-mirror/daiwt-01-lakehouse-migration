import io
from types import SimpleNamespace

import pytest
from databricks.sdk.errors import NotFound, PermissionDenied

from parity.parity_check import GoldenStore, compare, digest, identifier, Warehouse

ENTRY = {"unit": "load_sale", "table": "gold.fact_sale", "path": "outputs/fact_sale.csv", "columns": ["id", "amount"]}
REGISTRY = {"load_sale": {"outputs": ["gold.fact_sale"], "job": "load_sale"}}


def test_empty():
    assert compare({"tables": []}, {}, None, None) == {
        "status": "PASS", "goldens": 0, "results": []}


def test_unmigrated_is_skipped_without_reading():
    result = compare({"tables": [ENTRY]}, {}, None, None)
    assert result["status"] == "PASS"
    assert result["results"] == [{"table": "gold.fact_sale", "status": "SKIP", "reason": "not yet migrated"}]


@pytest.mark.parametrize("actual,status", [
    ([["2", "3.00"], ["1", "2.00"]], "PASS"),
    ([["1", "2.00"]], "FAIL"),
    ([["1", "2.00"], ["2", "3.01"]], "FAIL"),
    ([["1", "2.00"], ["1", "2.00"]], "FAIL"),
])
def test_strict_parity(actual, status):
    result = compare({"tables": [ENTRY]}, REGISTRY,
                     lambda _: [["1", "2.00"], ["2", "3.00"]], lambda _: actual)
    assert result["results"][0]["status"] == status
    assert result["status"] == status


def test_empty_migrated_table():
    assert compare({"tables": [ENTRY]}, REGISTRY, lambda _: [], lambda _: [])["status"] == "PASS"


def test_read_error_fails():
    def broken(_):
        raise RuntimeError("cannot read")
    result = compare({"tables": [ENTRY]}, REGISTRY, broken, lambda _: [])
    assert result["status"] == "FAIL"
    assert result["results"][0]["reason"] == "cannot read"


def test_hash_preserves_null_and_duplicates():
    assert digest([[None]]) != digest([[""]])
    assert digest([["a"], ["a"]]) != digest([["a"]])


@pytest.mark.parametrize("manifest", [{}, {"tables": {}}, {"tables": [ENTRY, ENTRY]}])
def test_bad_manifest(manifest):
    with pytest.raises((KeyError, ValueError)):
        compare(manifest, {}, None, None)


def test_duplicate_producer():
    with pytest.raises(ValueError):
        compare({"tables": []}, {"a": REGISTRY["load_sale"], "b": REGISTRY["load_sale"]}, None, None)


def store(download, volume=lambda _: None):
    client = SimpleNamespace(files=SimpleNamespace(download=download),
                             volumes=SimpleNamespace(read=volume))
    return GoldenStore(client, "/Volumes/daiwt_01_mig_prod/landing/golden")


def raise_error(exc):
    def broken(_):
        raise exc
    return broken


def test_missing_manifest_is_empty():
    assert store(raise_error(NotFound("missing"))).manifest() == {"tables": []}


def test_missing_volume_is_not_empty():
    with pytest.raises(NotFound):
        store(None, raise_error(NotFound("missing volume"))).manifest()


def test_manifest_permission_failure():
    with pytest.raises(PermissionDenied):
        store(raise_error(PermissionDenied("denied"))).manifest()


def test_manifest_and_csv():
    assert store(lambda _: SimpleNamespace(contents=io.BytesIO(b'{"tables": []}'))).manifest() == {"tables": []}
    golden = store(lambda _: SimpleNamespace(contents=io.BytesIO(b"id,amount\n1,2.00\n2,\\N\n")))
    assert golden.rows(ENTRY) == [["1", "2.00"], ["2", None]]


@pytest.mark.parametrize("data", [b"wrong,header\n", b"id,amount\n1\n", b""])
def test_invalid_csv(data):
    with pytest.raises((ValueError, StopIteration)):
        store(lambda _: SimpleNamespace(contents=io.BytesIO(data))).rows(ENTRY)


def test_no_path_escape():
    with pytest.raises(ValueError):
        store(None).rows({**ENTRY, "path": "outputs/../manifest.json"})


def test_identifier_rejects_sql():
    with pytest.raises(ValueError):
        identifier("gold; DROP CATALOG x")


def test_sql_failure():
    response = SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(value="FAILED"), error="bad SQL"))
    client = SimpleNamespace(statement_execution=SimpleNamespace(execute_statement=lambda **_: response))
    with pytest.raises(RuntimeError, match="bad SQL"):
        Warehouse(client, "w").sql("SELECT 1")


def test_chunked_result():
    response = SimpleNamespace(
        status=SimpleNamespace(state=SimpleNamespace(value="SUCCEEDED")),
        statement_id="s", result=SimpleNamespace(data_array=[["1"]], next_chunk_index=1),
        manifest=SimpleNamespace(truncated=False))
    client = SimpleNamespace(statement_execution=SimpleNamespace(
        execute_statement=lambda **_: response,
        get_statement_result_chunk_n=lambda *_: SimpleNamespace(data_array=[["2"]], next_chunk_index=None)))
    assert Warehouse(client, "w").sql("SELECT 1") == [["1"], ["2"]]
