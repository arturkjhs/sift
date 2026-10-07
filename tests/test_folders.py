"""Chat folders: create, edit (keeping what the editor doesn't show), delete, reorder; the
archive row's counts."""

from __future__ import annotations

import unittest
from typing import Any

from fakes import FakeLib, new_chat, ok, position, qt_app, wait_until

from tgclient.store.chats import ChatStore
from tgclient.store.users import UserStore
from tgclient.td import TdHub

FOLDER = {"@type": "chatFolder", "name": {"text": {"text": "Work"}},
          "icon": {"name": "Work"}, "color_id": 3, "is_shareable": False,
          "pinned_chat_ids": [1], "included_chat_ids": [1, 2], "excluded_chat_ids": [9],
          "include_groups": True, "exclude_muted": True}


def responder(req: dict[str, Any]) -> list[dict[str, Any]]:
    if req["@type"] == "getChatFolder":
        return [{**FOLDER, "@extra": req["@extra"]}]
    return [ok(req)]


class FoldersTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        qt_app()
        from tgclient.ui.folders import FolderEditor

        self.lib = FakeLib(responder)
        self.hub = TdHub(self.lib)
        self.addCleanup(self.hub.stop)
        self.client = self.hub.create_client()
        self.chats = ChatStore(self.client)
        self.users = UserStore(self.client)
        archived = new_chat(3, "Old group", 0, "chatTypeSupergroup")
        archived["chat"]["positions"] = [position(5, "chatListArchive")]
        for event in (new_chat(1, "Olena", 10), new_chat(2, "Petr", 9), archived,
                      {"@type": "updateChatFolders", "main_chat_list_position": 1,
                       "chat_folders": [{"id": 4, "name": {"text": {"text": "Work"}}},
                                        {"id": 5, "name": {"text": {"text": "Family"}}}]},
                      {"@type": "updateUnreadChatCount",
                       "chat_list": {"@type": "chatListArchive"}, "total_count": 7,
                       "unread_count": 2, "unread_unmuted_count": 1}):
            self.lib.push(event)
        await wait_until(lambda: 7 == self.chats.total_chats.get("archive"))
        self.editor = FolderEditor(self.client, self.chats)

    def sent(self, kind: str) -> list[dict[str, Any]]:
        return [r for r in self.lib.sent if r["@type"] == kind]

    async def test_edit_create_move_delete(self) -> None:
        loaded: list[dict[str, Any]] = []
        self.editor.loaded.connect(loaded.append)
        self.editor.edit("folder:4")
        await wait_until(lambda: bool(loaded))
        folder = loaded[0]
        self.assertEqual((folder["name"], folder["chats"], folder["includeGroups"],
                          folder["excludeMuted"], folder["includeBots"]),
                         ("Work", [1, 2], True, True, False))
        self.editor.save({**folder, "name": "Work stuff and more", "chats": [2],
                          "includeBots": True})
        await wait_until(lambda: bool(self.sent("editChatFolder")))
        body = self.sent("editChatFolder")[0]["folder"]
        self.assertEqual(body["name"]["text"]["text"], "Work stuff a")  # 12 characters max
        self.assertEqual((body["included_chat_ids"], body["pinned_chat_ids"],
                          body["excluded_chat_ids"], body["icon"], body["include_bots"]),
                         ([2], [], [9], {"name": "Work"}, True))

        self.editor.edit("")
        self.assertEqual(loaded[-1]["id"], 0)
        failed: list[str] = []
        self.editor.failed.connect(failed.append)
        self.editor.save({"id": 0, "name": "Empty", "chats": []})
        await wait_until(lambda: bool(failed))
        self.editor.save({"id": 0, "name": "Friends", "chats": [1, 2]})
        await wait_until(lambda: bool(self.sent("createChatFolder")))

        self.editor.move("folder:5", -1)  # Work, All chats, Family -> Work, Family, All chats
        await wait_until(lambda: bool(self.sent("reorderChatFolders")))
        order = self.sent("reorderChatFolders")[0]
        self.assertEqual((order["chat_folder_ids"], order["main_chat_list_position"]), ([4, 5], 2))
        self.editor.remove("folder:5")
        await wait_until(lambda: bool(self.sent("deleteChatFolder")))

    async def test_archive_row(self) -> None:
        from tgclient.models.chat_list import ChatListModel
        from tgclient.models.folders import FolderModel

        model = ChatListModel(self.chats, self.users)
        self.assertEqual((model.archiveCount, model.archiveUnread, model.archivePreview),
                         (7, 1, "Old group"))
        folders = FolderModel(self.chats)
        self.assertEqual(folders.rowCount(), 3)  # Work, All chats, Family: no Archive tab


if __name__ == "__main__":
    unittest.main()
