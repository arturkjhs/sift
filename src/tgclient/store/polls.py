"""Polls, quizzes and checklists in a message, as QML shows them. Qt-free."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _text(value: Any) -> str:
    return value.get("text", "") if isinstance(value, dict) else str(value or "")


def poll_view(content: dict[str, Any], name_of: Callable[[dict[str, Any]], str]
              = lambda sender: "") -> dict[str, Any]:
    """{} unless the message is a poll or a checklist."""
    match content.get("@type"):
        case "messagePoll":
            return _poll(content.get("poll") or {})
        case "messageChecklist":
            return _checklist(content.get("list") or {}, name_of)
    return {}


def _poll(poll: dict[str, Any]) -> dict[str, Any]:
    kind = (poll.get("type") or {})
    quiz = kind.get("@type") == "pollTypeQuiz"
    correct = set(kind.get("correct_option_ids") or ())
    if "correct_option_id" in kind:  # older TDLib
        correct.add(kind["correct_option_id"])
    options = poll.get("options") or []
    voted = any(o.get("is_chosen") for o in options)
    closed = bool(poll.get("is_closed"))
    order = poll.get("option_order") or list(range(len(options)))
    rows = []
    for index in order:
        if not 0 <= index < len(options):
            continue
        option = options[index]
        rows.append({
            "index": index, "text": _text(option.get("text")),
            "votes": option.get("voter_count", 0), "percent": option.get("vote_percentage", 0),
            "chosen": bool(option.get("is_chosen")),
            "pending": bool(option.get("is_being_chosen")),
            "correct": quiz and index in correct,
        })
    multiple = bool(poll.get("allows_multiple_answers") or kind.get("allow_multiple_answers"))
    return {
        "kind": "quiz" if quiz else "poll", "question": _text(poll.get("question")),
        "options": rows, "total": poll.get("total_voter_count", 0), "voted": voted,
        "closed": closed, "multiple": multiple and not quiz,
        "anonymous": bool(poll.get("is_anonymous", True)),
        # results show after voting or closing (or when TDLib says they're visible)
        "showResults": voted or closed,
        "canVote": not closed and not poll.get("vote_restriction_reason")
                   and (not voted or bool(poll.get("allows_revoting", not quiz))),
        "explanation": _text(kind.get("explanation")) if quiz and (voted or closed) else "",
    }


def _checklist(checklist: dict[str, Any], name_of: Callable[[dict[str, Any]], str]
               ) -> dict[str, Any]:
    tasks = []
    for task in checklist.get("tasks") or []:
        done_by = task.get("completed_by") or (
            {"@type": "messageSenderUser", "user_id": task["completed_by_user_id"]}
            if task.get("completed_by_user_id") else None)
        tasks.append({"id": task.get("id", 0), "text": _text(task.get("text")),
                      "done": bool(done_by or task.get("completion_date")),
                      "doneBy": name_of(done_by) if done_by else ""})
    return {"kind": "checklist", "question": _text(checklist.get("title")), "tasks": tasks,
            "doneCount": sum(t["done"] for t in tasks),
            "canMark": bool(checklist.get("can_mark_tasks_as_done"))}
