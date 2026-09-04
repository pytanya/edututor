"""Тесты адаптивных полей состояния и блока адаптации."""

import pytest
from pydantic import ValidationError

from src.agent.prompts import SYSTEM_PROMPT, _build_adaptation_block, build_messages
from src.models.schemas import AgentGraphState, LearningStyle


class TestAdaptiveFields:
    """Проверка полей AdaptiveFields в AgentGraphState."""

    def test_defaults_are_sensible(self):
        state = AgentGraphState()
        assert state.current_knowledge_level == 0.5
        assert state.learning_style == LearningStyle.READING
        assert state.fatigue_level == 0.0

    def test_knowledge_level_validates_range(self):
        state = AgentGraphState(current_knowledge_level=0.0)
        assert state.current_knowledge_level == 0.0
        state = AgentGraphState(current_knowledge_level=1.0)
        assert state.current_knowledge_level == 1.0
        with pytest.raises(ValidationError):
            AgentGraphState(current_knowledge_level=-0.1)
        with pytest.raises(ValidationError):
            AgentGraphState(current_knowledge_level=1.1)

    def test_fatigue_level_validates_range(self):
        state = AgentGraphState(fatigue_level=0.0)
        assert state.fatigue_level == 0.0
        state = AgentGraphState(fatigue_level=1.0)
        assert state.fatigue_level == 1.0
        with pytest.raises(ValidationError):
            AgentGraphState(fatigue_level=-0.1)
        with pytest.raises(ValidationError):
            AgentGraphState(fatigue_level=1.1)


class TestAdaptationBlock:
    """Проверка логики формирования блока адаптации."""

    def test_high_knowledge(self):
        state = AgentGraphState(current_knowledge_level=0.8)
        block = _build_adaptation_block(state)
        assert "ВЫСОКИЙ" in block

    def test_low_knowledge(self):
        state = AgentGraphState(current_knowledge_level=0.3)
        block = _build_adaptation_block(state)
        assert "НИЗКИЙ" in block

    def test_high_fatigue(self):
        state = AgentGraphState(fatigue_level=0.7)
        block = _build_adaptation_block(state)
        assert "УСТАЛ" in block

    def test_visual_style(self):
        state = AgentGraphState(learning_style=LearningStyle.VISUAL)
        block = _build_adaptation_block(state)
        assert "схемы" in block or "ASCII" in block

    def test_kinesthetic_style(self):
        state = AgentGraphState(learning_style=LearningStyle.KINESTHETIC)
        block = _build_adaptation_block(state)
        assert "практические" in block or "упражнения" in block

    def test_default_state_medium(self):
        state = AgentGraphState()
        block = _build_adaptation_block(state)
        assert "текстом" in block
        assert "ВЫСОКИЙ" not in block
        assert "НИЗКИЙ" not in block
        assert "УСТАЛ" not in block


class TestBuildMessages:
    """Проверка сборки сообщений для LLM."""

    def test_adaptation_block_injected(self):
        state = AgentGraphState(messages=[{"role": "user", "content": "hello"}])
        msgs = build_messages(state)
        system_msgs = [m for m in msgs if m["role"] == "system"]
        assert len(system_msgs) >= 2
        assert system_msgs[0]["content"] == SYSTEM_PROMPT
        assert "текстом" in system_msgs[1]["content"]

    def test_rag_context_appended(self):
        state = AgentGraphState(
            messages=[],
            rag_context=[{"text": "RAG chunk 1"}, {"text": "RAG chunk 2"}],
        )
        msgs = build_messages(state)
        user_msgs = [m for m in msgs if m["role"] == "user"]
        assert any("Контекст из базы знаний" in m["content"] for m in user_msgs)

    def test_user_messages_preserved(self):
        user_msg = {"role": "user", "content": "объясни тему"}
        state = AgentGraphState(messages=[user_msg])
        msgs = build_messages(state)
        assert user_msg in msgs
