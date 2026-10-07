"""Silent and scheduled sending, the list of scheduled messages."""

from __future__ import annotations

import unittest
from typing import Any

from fakes import wait_until
from test_history import CHAT, msg
from test_message_actions import ActionCase

AT = 1_900_000_000


class ScheduledTest(ActionCase):
    async def test_silent_and_scheduled(self) -> None:
        scheduled: list[dict[str, Any]] = []

        def answer(req: dict[str, Any]) -> list[dict[str, Any]] | None:
            extra = req["@extra"]
            match req["@type"]:
                case "sendMessage" if req.get("options", {}).get("scheduling_state"):
                    sent = {**msg(9001, req["input_message_content"]["text"]["text"], out=True),
                            "scheduling_state": req["options"]["scheduling_state"]}
                    scheduled.append(sent)
                    return [{"@type": "updateNewMessage", "message": sent},
                            {"@type": "updateChatHasScheduledMessages", "chat_id": CHAT,
                             "has_scheduled_messages": True}, {**sent, "@extra": extra}]
                case "getChatScheduledMessages":
                    return [{"@type": "messages", "total_count": len(scheduled),
                             "messages": scheduled, "@extra": extra}]
                case "editMessageSchedulingState" | "deleteMessages":
                    scheduled.clear()
            return None

        self.server.hook = answer
        await self.open_loaded()
        self.model.sendMessage("quietly", 0, {"silent": True})
        await wait_until(lambda: bool(self.sent("sendMessage")))
        options = self.sent("sendMessage")[0]["options"]
        self.assertEqual((options["disable_notification"], options["scheduling_state"]),
                         (True, None))

        self.model.sendMessage("later", 0, {"scheduleAt": AT})
        await wait_until(lambda: len(self.model.scheduled) == 1)
        self.assertEqual(self.sent("sendMessage")[1]["options"]["scheduling_state"],
                         {"@type": "messageSchedulingStateSendAtDate", "send_date": AT,
                          "repeat_period": 0})
        self.assertEqual(self.model.rowOf(9001), -1)  # not in the history
        self.assertTrue(self.model.hasScheduled)
        self.assertEqual(self.model.scheduled[0]["text"], "later")

        self.model.reschedule(9001, AT + 60)
        await wait_until(lambda: bool(self.sent("editMessageSchedulingState")))
        self.assertEqual(self.sent("editMessageSchedulingState")[0]["scheduling_state"]
                         ["send_date"], AT + 60)
        await wait_until(lambda: self.model.scheduled == [])
        self.model.sendScheduledNow(9001)
        await wait_until(lambda: len(self.sent("editMessageSchedulingState")) == 2)
        self.assertIsNone(self.sent("editMessageSchedulingState")[1]["scheduling_state"])


if __name__ == "__main__":
    unittest.main()
