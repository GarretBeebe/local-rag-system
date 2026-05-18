"""Unit tests for chat request validation (size limits, field constraints)."""

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from settings import (
    MAX_CHAT_CONTENT_ITEMS,
    MAX_CHAT_MESSAGE_CHARS,
    MAX_CHAT_MESSAGES,
    MAX_CHAT_QUESTION_CHARS,
    MAX_CHAT_TOTAL_CHARS,
    MAX_MODEL_NAME_CHARS,
)
from web.schemas import (
    ChatMessage,
    ChatRequest,
    LoginRequest,
)
from web.schemas import (
    extract_question_from_messages as _extract_question_from_messages,
)
from web.schemas import (
    validate_chat_request as _validate_chat_request,
)


def _msg(content: str | list, role: str = "user") -> ChatMessage:
    return ChatMessage(role=role, content=content)


def _req(messages=None, model="test-model") -> ChatRequest:
    if messages is None:
        messages = [_msg("hello")]
    return ChatRequest(model=model, messages=messages)


# --- LoginRequest ---

def test_login_request_valid():
    r = LoginRequest(username="alice", password="secret")
    assert r.username == "alice"


def test_login_request_username_too_long():
    with pytest.raises(ValidationError):
        LoginRequest(username="a" * 129, password="x")


def test_login_request_password_too_long():
    with pytest.raises(ValidationError):
        LoginRequest(username="alice", password="x" * 129)


# --- ChatRequest field constraints ---

def test_chat_request_valid():
    req = _req()
    assert req.model == "test-model"


def test_chat_request_empty_model_rejected():
    with pytest.raises(ValidationError):
        ChatRequest(model="", messages=[_msg("hi")])


def test_chat_request_model_too_long():
    with pytest.raises(ValidationError):
        ChatRequest(model="x" * (MAX_MODEL_NAME_CHARS + 1), messages=[_msg("hi")])


def test_chat_request_no_messages_rejected():
    with pytest.raises(ValidationError):
        ChatRequest(model="m", messages=[])


def test_chat_request_too_many_messages():
    with pytest.raises(ValidationError):
        ChatRequest(model="m", messages=[_msg("x")] * (MAX_CHAT_MESSAGES + 1))


def test_chat_request_invalid_role_rejected():
    with pytest.raises(ValidationError):
        ChatMessage(role="invalid", content="hi")


def test_chat_request_stream_defaults_to_false():
    req = _req()
    assert req.stream is False


def test_chat_request_rag_mode_strict():
    req = ChatRequest(model="m", messages=[_msg("hi")], rag_mode="strict")
    assert req.rag_mode == "strict"


def test_chat_request_invalid_rag_mode():
    with pytest.raises(ValidationError):
        ChatRequest(model="m", messages=[_msg("hi")], rag_mode="unknown")


# --- _validate_chat_request ---

def test_validate_single_message_within_limits():
    _validate_chat_request(_req([_msg("hello")]))


def test_validate_message_too_long():
    long_msg = _msg("x" * (MAX_CHAT_MESSAGE_CHARS + 1))
    with pytest.raises(HTTPException) as exc:
        _validate_chat_request(_req([long_msg]))
    assert exc.value.status_code == 400


def test_validate_total_size_exceeded():
    big = "x" * (MAX_CHAT_MESSAGE_CHARS)
    messages = [_msg(big)] * (MAX_CHAT_TOTAL_CHARS // MAX_CHAT_MESSAGE_CHARS + 1)
    with pytest.raises(HTTPException) as exc:
        _validate_chat_request(_req(messages[:MAX_CHAT_MESSAGES]))
    assert exc.value.status_code == 400


def test_validate_structured_content_too_many_items():
    content = [{"type": "text", "text": "x"}] * (MAX_CHAT_CONTENT_ITEMS + 1)
    with pytest.raises(HTTPException) as exc:
        _validate_chat_request(_req([_msg(content)]))
    assert exc.value.status_code == 400


# --- _extract_question_from_messages ---

def test_extract_question_string_content():
    msgs = [_msg("what is RAG?")]
    assert _extract_question_from_messages(msgs) == "what is RAG?"


def test_extract_question_structured_content():
    msgs = [_msg([{"type": "text", "text": "what is RAG?"}])]
    q = _extract_question_from_messages(msgs)
    assert "what is RAG?" in q


def test_extract_question_uses_last_message():
    msgs = [_msg("first"), _msg("second")]
    assert _extract_question_from_messages(msgs) == "second"


def test_extract_question_empty_raises():
    with pytest.raises(HTTPException):
        _extract_question_from_messages([_msg("   ")])


def test_extract_question_too_long_raises():
    with pytest.raises(HTTPException):
        _extract_question_from_messages([_msg("x" * (MAX_CHAT_QUESTION_CHARS + 1))])
