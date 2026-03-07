"""Tests for the memory module."""
import pytest
from multillm.memory import (
    store_memory, search_memory, list_memories, get_memory, delete_memory,
    share_context, get_shared_context,
    get_settings, get_setting, set_setting, update_settings, delete_setting,
)


class TestMemoryCRUD:

    def test_store_and_get(self):
        mem_id = store_memory(
            title="Test Memory",
            content="This is test content",
            project="test-project",
            source_llm="claude",
            category="finding",
        )
        assert mem_id.startswith("mem_")

        mem = get_memory(mem_id)
        assert mem is not None
        assert mem["title"] == "Test Memory"
        assert mem["content"] == "This is test content"
        assert mem["project"] == "test-project"
        assert mem["source_llm"] == "claude"
        assert mem["category"] == "finding"

        # Clean up
        delete_memory(mem_id)

    def test_delete_memory(self):
        mem_id = store_memory(title="To Delete", content="Delete me")
        assert get_memory(mem_id) is not None
        assert delete_memory(mem_id)
        assert get_memory(mem_id) is None

    def test_delete_nonexistent(self):
        assert not delete_memory("mem_nonexistent")

    def test_list_memories(self):
        ids = []
        for i in range(3):
            ids.append(store_memory(
                title=f"List Test {i}",
                content=f"Content {i}",
                project="list-test",
            ))

        results = list_memories(project="list-test")
        assert len(results) >= 3

        # Clean up
        for mid in ids:
            delete_memory(mid)

    def test_list_memories_filter_category(self):
        m1 = store_memory(title="Cat A", content="content", category="decision", project="cat-test")
        m2 = store_memory(title="Cat B", content="content", category="finding", project="cat-test")

        decisions = list_memories(project="cat-test", category="decision")
        assert all(r["category"] == "decision" for r in decisions)

        delete_memory(m1)
        delete_memory(m2)


class TestMemorySearch:

    def test_search_basic(self):
        mem_id = store_memory(
            title="Kubernetes Deployment",
            content="Deploy the application using kubectl apply -f deployment.yaml",
            project="search-test",
        )

        results = search_memory("kubernetes deployment", project="search-test")
        assert len(results) >= 1
        assert any(r["id"] == mem_id for r in results)

        delete_memory(mem_id)

    def test_search_no_results(self):
        results = search_memory("xyznonexistentquery12345")
        assert len(results) == 0

    def test_search_across_projects(self):
        m1 = store_memory(title="Python Testing", content="pytest framework", project="proj-a")
        m2 = store_memory(title="Python Linting", content="ruff linter", project="proj-b")

        # Search across all projects
        results = search_memory("python")
        assert len(results) >= 2

        delete_memory(m1)
        delete_memory(m2)


class TestSharedContext:

    def test_share_and_get_context(self):
        ctx_id = share_context(
            session_id="sess-123",
            source_llm="claude",
            content="Important finding about the codebase",
            context_type="finding",
        )
        assert ctx_id.startswith("ctx_")

        entries = get_shared_context(session_id="sess-123")
        assert len(entries) >= 1
        assert any(e["id"] == ctx_id for e in entries)

    def test_context_target_filter(self):
        share_context(
            session_id="sess-filter",
            source_llm="claude",
            content="For GPT only",
            target_llm="gpt",
        )
        share_context(
            session_id="sess-filter",
            source_llm="claude",
            content="For all",
            target_llm="*",
        )

        gpt_entries = get_shared_context(session_id="sess-filter", target_llm="gpt")
        assert len(gpt_entries) >= 2  # targeted + wildcard

    def test_expired_context(self):
        share_context(
            session_id="sess-expire",
            source_llm="claude",
            content="Will expire",
            ttl_seconds=-1,  # Already expired
        )
        entries = get_shared_context(session_id="sess-expire")
        assert len(entries) == 0


class TestSettings:

    def test_get_defaults(self):
        settings = get_settings()
        assert "default_model" in settings
        assert "streaming_enabled" in settings

    def test_set_and_get_setting(self):
        set_setting("test_key", "test_value")
        assert get_setting("test_key") == "test_value"
        delete_setting("test_key")

    def test_set_complex_value(self):
        set_setting("complex_setting", {"nested": True, "count": 42})
        val = get_setting("complex_setting")
        assert val == {"nested": True, "count": 42}
        delete_setting("complex_setting")

    def test_update_settings(self):
        update_settings({"batch_a": 1, "batch_b": "hello"})
        assert get_setting("batch_a") == 1
        assert get_setting("batch_b") == "hello"
        delete_setting("batch_a")
        delete_setting("batch_b")

    def test_setting_override_default(self):
        original = get_setting("default_model")
        set_setting("default_model", "gemini/flash")
        assert get_setting("default_model") == "gemini/flash"
        # Restore
        if original:
            set_setting("default_model", original)
        else:
            delete_setting("default_model")

    def test_delete_setting(self):
        set_setting("to_delete", "value")
        assert delete_setting("to_delete")
        assert not delete_setting("to_delete")  # Already deleted

    def test_get_nonexistent_setting(self):
        assert get_setting("nonexistent_key_xyz") is None
        assert get_setting("nonexistent_key_xyz", "fallback") == "fallback"
