"""Voice MCP tools with the HTTP layer mocked (no network)."""
import pytest

from app import mcp_server as m

TASKS = [
    {"id": "t1", "title": "Write the report", "priority": 1},
    {"id": "t2", "title": "Call the bank", "priority": 2},
    {"id": "t3", "title": "Call mum", "priority": 3},
]


class Api:
    def __init__(self):
        self.calls, self.replies = [], {}

    async def __call__(self, method, path, **kw):
        self.calls.append((method, path, kw))
        key = (method, "/v1/cos/tasks" if "/tasks/" in path else path)
        return self.replies.get(key, (500, None))


@pytest.fixture
def api(monkeypatch):
    a = Api()
    monkeypatch.setattr(m, "_api", a)
    m._last_list.clear()
    a.replies[("GET", "/v1/cos/tasks")] = (200, TASKS)
    return a


async def test_exactly_five_tools():
    assert sorted(t.name for t in await m.mcp.list_tools()) == [
        "add_task", "list_tasks", "save_request", "schedule_callback", "update_task"]


async def test_list_tasks(api):
    out = await m.list_tasks()
    assert out == ("1. Write the report (priority high)\n2. Call the bank (priority normal)\n"
                   "3. Call mum (priority low)")
    assert api.calls[0][2]["params"]["due"] == "today"


async def test_list_tasks_empty_and_long(api):
    api.replies[("GET", "/v1/cos/tasks")] = (200, [])
    assert await m.list_tasks() == "No tasks for today."
    api.replies[("GET", "/v1/cos/tasks")] = (
        200, [{"id": str(i), "title": "x" * 80, "priority": 2} for i in range(30)])
    out = await m.list_tasks()
    assert len(out) <= 300 and "more." in out


async def test_add_task(api):
    api.replies[("POST", "/v1/cos/tasks")] = (201, {"duplicate": False})
    assert await m.add_task("Buy milk\x00\n now", "high", "tomorrow") == "Added: Buy milk now"
    body = api.calls[-1][2]["json"]
    assert body["priority"] == 1 and body["source"] == "voice" and "due_date" in body
    await m.add_task("x", "weird", "none")
    body = api.calls[-1][2]["json"]
    assert body["priority"] == 2 and "due_date" not in body


async def test_add_task_duplicate_and_limits(api):
    api.replies[("POST", "/v1/cos/tasks")] = (200, {"duplicate": True})
    assert (await m.add_task("Buy milk")).startswith("Already on your list: Buy milk")
    await m.add_task("y" * 5000)
    assert len(api.calls[-1][2]["json"]["title"]) == 200
    assert await m.add_task("  \x01 ") == "Tell me the task first."


async def test_update_task_by_number_and_title(api):
    api.replies[("PATCH", "/v1/cos/tasks")] = (200, {})
    await m.list_tasks()
    assert await m.update_task("2", "done") == "Marked Call the bank as done."
    assert api.calls[-1][1] == "/v1/cos/tasks/t2" and api.calls[-1][2]["json"] == {"status": "done"}
    assert await m.update_task("report", "in progress") == (
        "Marked Write the report as in progress.")
    assert api.calls[-1][1] == "/v1/cos/tasks/t1"


async def test_update_task_ambiguous_and_missing(api):
    assert await m.update_task("call", "done") == "Which one: Call the bank or Call mum?"
    assert await m.update_task("zebra", "done") == "I couldn't find that task."
    assert await m.update_task("9", "done") == "I couldn't find that task."
    assert not [c for c in api.calls if c[0] == "PATCH"]
    assert "Status must be" in await m.update_task("1", "banana")


async def test_schedule_callback(api):
    api.replies[("POST", "/v1/cos/callbacks")] = (201, {"due_at": "2026-11-14T13:15:00+00:00"})
    out = await m.schedule_callback(30)
    assert out.startswith("I'll call you at ") and len(out) == len("I'll call you at 18:45.")
    assert await m.schedule_callback(1) == "Pick between 5 and 240 minutes."


@pytest.mark.parametrize("status,detail,expect", [
    (409, "Quiet hours (22:30-07:30 Asia/Kolkata): pick a later time", "quiet hours"),
    (409, "Calls must be at least 10 minutes apart", "too close"),
    (429, "Daily call limit reached", "limit"),
    (423, "Chief of Staff is stopped", "switched off"),
])
async def test_callback_refusals(api, status, detail, expect):
    api.replies[("POST", "/v1/cos/callbacks")] = (status, {"detail": detail})
    assert expect in (await m.schedule_callback(30)).lower()


async def test_save_request(api):
    api.replies[("POST", "/v1/cos/tasks")] = (201, {"duplicate": False})
    assert await m.save_request("research X " + "z" * 500) == m.SAVED
    body = api.calls[-1][2]["json"]
    assert body["project"] == "for-jarvis" and body["source"] == "voice"
    assert body["title"].startswith("Request: research X") and len(body["title"]) <= 200


async def test_never_raises(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(m, "_api", boom)
    for out in (await m.list_tasks(), await m.add_task("x"), await m.update_task("1", "done"),
                await m.schedule_callback(30), await m.save_request("x")):
        assert out == "Sorry, I can't reach my task list right now."


async def test_api_error_is_a_sentence(api):
    api.replies[("GET", "/v1/cos/tasks")] = (401, {"detail": "Invalid or missing key"})
    assert await m.list_tasks() == "I'm not allowed to do that."


def test_server_binds_loopback():
    assert m.mcp.settings.host == "127.0.0.1"
