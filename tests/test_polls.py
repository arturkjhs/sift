"""Polls, quizzes and checklists: the view QML gets, live vote updates, voting and marking."""

from __future__ import annotations

import unittest
from typing import Any

from fakes import wait_until
from test_history import msg
from test_message_actions import ActionCase

from tgclient.store.polls import poll_view


def option(text: str, votes: int, percent: int, chosen: bool = False) -> dict[str, Any]:
    return {"@type": "pollOption", "id": text, "text": {"text": text}, "voter_count": votes,
            "vote_percentage": percent, "is_chosen": chosen, "is_being_chosen": False}


def poll(chosen: int = -1, quiz: bool = False, multiple: bool = False,
         closed: bool = False) -> dict[str, Any]:
    kind = ({"@type": "pollTypeQuiz", "correct_option_ids": [1],
             "explanation": {"text": "Sunday is free"}} if quiz
            else {"@type": "pollTypeRegular"})
    return {"@type": "poll", "id": "77", "question": {"text": "When do we go?"},
            "options": [option("Saturday", 3, 60, chosen == 0), option("Sunday", 2, 40,
                                                                         chosen == 1)],
            "total_voter_count": 5, "is_anonymous": False, "allows_multiple_answers": multiple,
            "allows_revoting": True, "type": kind, "is_closed": closed}


def poll_message(mid: int, **kwargs: Any) -> dict[str, Any]:
    return {**msg(mid), "content": {"@type": "messagePoll", "poll": poll(**kwargs)}}


CHECKLIST = {"@type": "messageChecklist", "list": {
    "title": {"text": "Packing"}, "can_mark_tasks_as_done": True, "tasks": [
        {"id": 1, "text": {"text": "Rope"}, "completed_by": {
            "@type": "messageSenderUser", "user_id": 5}, "completion_date": 10},
        {"id": 2, "text": {"text": "Helmet"}, "completed_by": None, "completion_date": 0}]}}


class PollViewTest(unittest.TestCase):
    def test_views(self) -> None:
        view = poll_view({"@type": "messagePoll", "poll": poll()})
        self.assertEqual((view["kind"], view["question"], view["showResults"], view["canVote"]),
                         ("poll", "When do we go?", False, True))
        self.assertEqual([o["text"] for o in view["options"]], ["Saturday", "Sunday"])
        voted = poll_view({"@type": "messagePoll", "poll": poll(chosen=0)})
        self.assertTrue(voted["showResults"] and voted["voted"])
        quiz = poll_view({"@type": "messagePoll", "poll": poll(chosen=0, quiz=True)})
        self.assertEqual([o["correct"] for o in quiz["options"]], [False, True])
        self.assertEqual(quiz["explanation"], "Sunday is free")
        self.assertFalse(quiz["multiple"])
        closed = poll_view({"@type": "messagePoll", "poll": poll(closed=True)})
        self.assertTrue(closed["showResults"] and not closed["canVote"])
        checklist = poll_view(CHECKLIST, lambda sender: "Olena")
        self.assertEqual((checklist["doneCount"], checklist["tasks"][0]["doneBy"],
                          checklist["tasks"][1]["done"]), (1, "Olena", False))
        self.assertEqual(poll_view({"@type": "messageText"}), {})


class PollModelTest(ActionCase):
    async def test_vote_update_and_mark(self) -> None:
        await self.open_loaded()
        await self.push({"@type": "updateNewMessage", "message": poll_message(7)},
                        {"@type": "updateNewMessage", "message": {**msg(8),
                                                                  "content": CHECKLIST}})
        row = self.model.rowOf(7)
        self.assertEqual(self.role(row, self.Role.Poll)["total"], 5)
        self.assertEqual(self.role(row, self.Role.MediaLabel), "")
        self.model.vote(7, [1])
        await wait_until(lambda: bool(self.sent("setPollAnswer")))
        self.assertEqual(self.sent("setPollAnswer")[0]["option_ids"], [1])
        await self.push({"@type": "updatePoll", "poll": {**poll(chosen=1),
                                                         "total_voter_count": 6}})
        view = self.role(self.model.rowOf(7), self.Role.Poll)
        self.assertEqual((view["total"], view["voted"]), (6, True))
        self.model.markTask(8, 2, True)
        await wait_until(lambda: bool(self.sent("markChecklistTasksAsDone")))
        request = self.sent("markChecklistTasksAsDone")[0]
        self.assertEqual((request["marked_as_done_task_ids"],
                          request["marked_as_not_done_task_ids"]), ([2], []))
        self.assertEqual(self.role(self.model.rowOf(8), self.Role.Poll)["tasks"][0]["doneBy"],
                         "Olena K")


    async def test_send_poll_and_quiz(self) -> None:
        await self.open_loaded()
        self.model.sendPoll({"question": "When?", "options": ["Sat", " ", "Sun"],
                             "anonymous": False, "multiple": True})
        self.model.sendPoll({"question": "Capital of Czechia?", "options": ["Brno", "Prague"],
                             "quiz": True, "correct": 1, "explanation": "Since 1918"})
        self.model.sendPoll({"question": "Lonely", "options": ["one"]})  # not enough options
        await wait_until(lambda: len(self.sent("sendMessage")) == 2)
        poll, quiz = (r["input_message_content"] for r in self.sent("sendMessage"))
        self.assertEqual([o["text"]["text"] for o in poll["options"]], ["Sat", "Sun"])
        self.assertEqual((poll["is_anonymous"], poll["allows_multiple_answers"],
                          poll["type"]["@type"]), (False, True, "inputPollTypeRegular"))
        self.assertEqual((quiz["type"]["correct_option_ids"], quiz["allows_multiple_answers"],
                          quiz["type"]["explanation"]["text"]), ([1], False, "Since 1918"))


if __name__ == "__main__":
    unittest.main()
