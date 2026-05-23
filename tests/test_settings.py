"""Tests for settings._validate_settings() contract.

_validate_settings() is called at import time, so tests call it directly after
monkeypatching individual module-level variables rather than reloading the module.
"""

import pytest

import settings as s


def test_final_k_greater_than_mmr_k_raises(monkeypatch):
    monkeypatch.setattr(s, "FINAL_K", 10)
    monkeypatch.setattr(s, "MMR_K", 5)
    with pytest.raises(ValueError, match="FINAL_K"):
        s._validate_settings()


def test_mmr_k_greater_than_recall_k_raises(monkeypatch):
    monkeypatch.setattr(s, "MMR_K", 20)
    monkeypatch.setattr(s, "RECALL_K", 15)
    with pytest.raises(ValueError, match="MMR_K"):
        s._validate_settings()


def test_chunk_overlap_equals_chunk_size_raises(monkeypatch):
    monkeypatch.setattr(s, "CHUNK_OVERLAP", 500)
    monkeypatch.setattr(s, "CHUNK_SIZE", 500)
    with pytest.raises(ValueError, match="CHUNK_OVERLAP"):
        s._validate_settings()


def test_chunk_overlap_negative_raises(monkeypatch):
    monkeypatch.setattr(s, "CHUNK_OVERLAP", -1)
    with pytest.raises(ValueError, match="CHUNK_OVERLAP"):
        s._validate_settings()


def test_max_chunk_chars_less_than_chunk_size_raises(monkeypatch):
    monkeypatch.setattr(s, "MAX_CHUNK_CHARS", 100)
    monkeypatch.setattr(s, "CHUNK_SIZE", 500)
    with pytest.raises(ValueError, match="MAX_CHUNK_CHARS"):
        s._validate_settings()


def test_mmr_lambda_mult_above_one_raises(monkeypatch):
    monkeypatch.setattr(s, "MMR_LAMBDA_MULT", 1.1)
    with pytest.raises(ValueError, match="MMR_LAMBDA_MULT"):
        s._validate_settings()


def test_mmr_lambda_mult_negative_raises(monkeypatch):
    monkeypatch.setattr(s, "MMR_LAMBDA_MULT", -0.1)
    with pytest.raises(ValueError, match="MMR_LAMBDA_MULT"):
        s._validate_settings()


def test_positive_int_zero_raises(monkeypatch):
    monkeypatch.setattr(s, "FINAL_K", 0)
    with pytest.raises(ValueError, match="FINAL_K"):
        s._validate_settings()


def test_positive_float_zero_raises(monkeypatch):
    monkeypatch.setattr(s, "RATE_WINDOW_SECONDS", 0.0)
    with pytest.raises(ValueError, match="RATE_WINDOW_SECONDS"):
        s._validate_settings()


def test_ollama_max_retries_negative_raises(monkeypatch):
    monkeypatch.setattr(s, "OLLAMA_MAX_RETRIES", -1)
    with pytest.raises(ValueError, match="OLLAMA_MAX_RETRIES"):
        s._validate_settings()


def test_ollama_max_retries_zero_passes(monkeypatch):
    monkeypatch.setattr(s, "OLLAMA_MAX_RETRIES", 0)
    s._validate_settings()  # should not raise


def test_invalid_rag_mode_raises(monkeypatch):
    monkeypatch.setattr(s, "RAG_MODE", "hallucinate")
    with pytest.raises(ValueError, match="RAG_MODE"):
        s._validate_settings()


def test_valid_defaults_pass():
    s._validate_settings()  # default settings must always be valid
