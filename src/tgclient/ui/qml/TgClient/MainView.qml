import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

SplitView {
    id: root
    objectName: "mainView"

    property var selectedChatId: 0       // int53, keep as var

    readonly property bool searching: search.query.trim() !== ""

    signal settingsRequested()

    // Another account: its own chats; nothing of the previous one stays selected.
    Connections {
        target: accounts
        function onActiveChanged() {
            root.selectedChatId = 0
            searchField.clear()
            list.positionViewAtBeginning()
        }
    }

    function openChat(chatId, messageId) {
        root.selectedChatId = chatId
        messages.open(chatId)
        if (messageId)
            messageView.showMessage(messageId)
    }


    handle: Rectangle {
        implicitWidth: 1
        color: Theme.separator
    }

    Rectangle {
        SplitView.preferredWidth: 340
        SplitView.minimumWidth: 260
        SplitView.maximumWidth: 520
        color: Theme.sidebar

        // Cmd/Ctrl+F searches the open chat; with Shift (or no chat open) everything.
        Shortcut {
            sequences: [StandardKey.Find]
            onActivated: root.selectedChatId !== 0 && !messages.topicsMode
                         ? messageView.openSearch() : searchField.focusInput()
        }
        Shortcut {
            sequences: ["Ctrl+Shift+F"]
            onActivated: searchField.focusInput()
        }
        Binding { target: search; property: "accentColor"; value: Theme.accent.toString() }

        ColumnLayout {
            anchors.fill: parent
            spacing: 0

            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: 10
                Layout.leftMargin: 12
                Layout.rightMargin: 6
                Layout.bottomMargin: 2
                spacing: 4

                AccountSwitcher {}

                SearchBox {
                    id: searchField
                    Layout.fillWidth: true
                    placeholder: qsTr("Search chats and messages")
                    onEdited: text => search.query = text
                }

                IconButton {
                    objectName: "digestButton"
                    visible: ai.configured
                    iconName: "inbox"
                    glyphSize: 15
                    Accessible.name: qsTr("Digest and promises")
                    onClicked: digestDialog.open()
                }

                IconButton {
                    iconName: "settings"
                    glyphSize: 15
                    Accessible.name: qsTr("Settings")
                    onClicked: root.settingsRequested()
                }
            }

            FolderTabs {
                Layout.fillWidth: true
                visible: !root.searching
                currentKey: chatList.listKey
                onSelected: key => {
                    chatList.setList(key)
                    list.positionViewAtBeginning()
                }
                onEditRequested: key => folderDialog.editFolder(key)
            }

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 1
                color: Theme.separator
            }

            ListView {
                id: results
                objectName: "searchResults"
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: root.searching
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                model: search
                delegate: SearchResultDelegate {
                    onActivated: root.openChat(chatId, messageId)
                }
                ScrollBar.vertical: ScrollBar {}

                footer: Column {
                    width: results.width
                    topPadding: 14
                    bottomPadding: 14
                    spacing: 8

                    Text {
                        x: 16
                        width: parent.width - 32
                        visible: search.busy || (results.count === 0)
                        text: search.busy ? qsTr("Searching\u2026") : qsTr("Nothing found")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontBody
                    }
                    Flow {
                        x: 16
                        width: parent.width - 32
                        visible: search.semanticSupported && !search.semanticEnabled
                        spacing: 4

                        Text {
                            text: qsTr("Search by meaning is off.")
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSmall
                        }
                        Text {
                            text: qsTr("Turn it on in Settings")
                            color: Theme.link
                            font.pixelSize: Theme.fontSmall
                            font.weight: Font.DemiBold

                            HoverHandler { cursorShape: Qt.PointingHandCursor }
                            TapHandler { onTapped: root.settingsRequested() }
                        }
                    }
                    Text {
                        x: 16
                        width: parent.width - 32
                        visible: search.modelState === "loading"
                        wrapMode: Text.Wrap
                        text: qsTr("Preparing search by meaning\u2026")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSmall
                    }
                }
            }

            // A new version: one quiet line above the chat list.
            Rectangle {
                objectName: "updateBanner"
                Layout.fillWidth: true
                implicitHeight: 36
                visible: updates.state === "available" || updates.state === "ready"
                color: Theme.selection

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 12
                    anchors.rightMargin: 8
                    spacing: 8
                    Icon {
                        name: "update"
                        color: Theme.accent
                        size: 16
                    }
                    Text {
                        Layout.fillWidth: true
                        text: updates.state === "ready" ? qsTr("Update ready")
                              : qsTr("Version %1 is out").arg(updates.newVersion)
                        elide: Text.ElideRight
                        color: Theme.text
                        font.pixelSize: Theme.fontSmall
                    }
                    PillButton {
                        text: updates.state === "ready" ? qsTr("Restart")
                              : updates.canInstall ? qsTr("Install") : qsTr("Details")
                        filled: true
                        onClicked: updates.state === "ready" ? updates.restart() : updates.install()
                    }
                }
            }

            ListView {
                id: list
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: !root.searching
                clip: true
                reuseItems: true
                boundsBehavior: Flickable.StopAtBounds
                model: chatList

                delegate: ChatDelegate {
                    width: ListView.view.width
                    selected: chatId === root.selectedChatId
                    onClicked: {
                        root.selectedChatId = chatId
                        messages.open(chatId)
                    }
                    onMenuRequested: chatMenu.openFor(chatId, title)
                }

                // A chat that gets a new message slides to its new place instead of jumping.
                move: Transition {
                    NumberAnimation { properties: "y"; duration: 180; easing.type: Easing.OutCubic }
                }
                displaced: Transition {
                    NumberAnimation { properties: "y"; duration: 180; easing.type: Easing.OutCubic }
                }

                onAtYEndChanged: if (atYEnd && !chatList.fullyLoaded) chatList.loadMore()
                onCountChanged: if (count > 0 && contentHeight < height && !chatList.fullyLoaded) chatList.loadMore()

                ScrollBar.vertical: ScrollBar {}

                // The archive: a row on top of all chats; inside it, a way back.
                header: Rectangle {
                    id: archiveRow
                    objectName: "archiveRow"
                    readonly property bool inArchive: chatList.listKey === "archive"
                    width: ListView.view.width
                    height: inArchive || (chatList.listKey === "main" && chatList.archiveCount > 0)
                            ? (inArchive ? 44 : 64) : 0
                    visible: height > 0
                    color: archiveHover.hovered ? Theme.hover : "transparent"
                    HoverHandler { id: archiveHover; cursorShape: Qt.PointingHandCursor }
                    TapHandler {
                        onTapped: {
                            chatList.setList(archiveRow.inArchive ? "main" : "archive")
                            list.positionViewAtBeginning()
                        }
                    }
                    Rectangle {
                        id: archiveIcon
                        x: archiveRow.inArchive ? 12 : 12
                        anchors.verticalCenter: parent.verticalCenter
                        width: archiveRow.inArchive ? 28 : 44
                        height: width
                        radius: width / 2
                        color: archiveRow.inArchive ? "transparent" : Theme.pill
                        Icon {
                            anchors.centerIn: parent
                            name: archiveRow.inArchive ? "back" : "inbox"
                            size: archiveRow.inArchive ? 18 : 22
                        }
                    }
                    Column {
                        anchors.left: archiveIcon.right
                        anchors.leftMargin: 10
                        anchors.right: archiveBadge.left
                        anchors.rightMargin: 8
                        anchors.verticalCenter: parent.verticalCenter
                        Text {
                            text: archiveRow.inArchive ? qsTr("Archive") : qsTr("Archived chats")
                            color: Theme.text
                            font.pixelSize: Theme.fontTitle
                            font.weight: Font.DemiBold
                        }
                        Text {
                            width: parent.width
                            visible: !archiveRow.inArchive && text !== ""
                            text: chatList.archivePreview
                            textFormat: Text.PlainText
                            elide: Text.ElideRight
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontBody
                        }
                    }
                    Rectangle {
                        id: archiveBadge
                        anchors.right: parent.right
                        anchors.rightMargin: 12
                        anchors.verticalCenter: parent.verticalCenter
                        visible: !archiveRow.inArchive && chatList.archiveUnread > 0
                        height: 20
                        width: Math.max(20, archiveCount.implicitWidth + 12)
                        radius: 10
                        color: Theme.badgeMuted
                        Text {
                            id: archiveCount
                            anchors.centerIn: parent
                            text: chatList.archiveUnread
                            color: Theme.textOnAccent
                            font.pixelSize: 11
                            font.weight: Font.Bold
                        }
                    }
                    Rectangle {
                        anchors.bottom: parent.bottom
                        width: parent.width
                        height: 1
                        color: Theme.separator
                        visible: archiveRow.inArchive
                    }
                }

                Text {
                    anchors.centerIn: parent
                    visible: list.count === 0 && chatList.fullyLoaded
                    text: qsTr("No chats here yet")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontBody
                }
            }
        }
    }

    DigestDialog {
        id: digestDialog
        onMessageRequested: (chatId, messageId) => root.openChat(chatId, messageId)
    }

    // Right click on a chat in the list.
    AppMenu {
        id: chatMenu
        objectName: "chatMenu"
        property var chatId: 0
        property string chatTitle: ""
        property var info: ({})
        readonly property bool group: info.type === "group" || info.type === "supergroup"

        function openFor(chatId, title) {
            chatMenu.chatId = chatId
            chatMenu.chatTitle = title
            chatMenu.info = chatActions.state(chatId)
            popup()
        }

        AppMenuItem {
            text: chatMenu.info.pinned ? qsTr("Unpin") : qsTr("Pin")
            iconName: "pin"
            onTriggered: chatActions.setPinned(chatMenu.chatId, chatList.listKey,
                                               !chatMenu.info.pinned)
        }
        AppMenuItem {
            text: chatMenu.info.unread ? qsTr("Mark as read") : qsTr("Mark as unread")
            iconName: "check"
            onTriggered: chatActions.setUnread(chatMenu.chatId, !chatMenu.info.unread)
        }
        AppMenuItem {
            text: chatMenu.info.archived ? qsTr("Unarchive") : qsTr("Archive")
            iconName: "inbox"
            onTriggered: chatActions.setArchived(chatMenu.chatId, !chatMenu.info.archived)
        }
        AppMenuSeparator {}
        AppMenuItem {
            visible: chatMenu.info.muted === true
            text: qsTr("Unmute")
            iconName: "bell"
            onTriggered: chatActions.mute(chatMenu.chatId, 0)
        }
        AppMenuItem {
            visible: chatMenu.info.muted === false
            text: qsTr("Mute for 1 hour")
            iconName: "bell"
            onTriggered: chatActions.mute(chatMenu.chatId, 3600)
        }
        AppMenuItem {
            visible: chatMenu.info.muted === false
            text: qsTr("Mute for 8 hours")
            iconName: "bell"
            onTriggered: chatActions.mute(chatMenu.chatId, 8 * 3600)
        }
        AppMenuItem {
            visible: chatMenu.info.muted === false
            text: qsTr("Mute for 2 days")
            iconName: "bell"
            onTriggered: chatActions.mute(chatMenu.chatId, 2 * 86400)
        }
        AppMenuItem {
            visible: chatMenu.info.muted === false
            text: qsTr("Mute forever")
            iconName: "bell"
            onTriggered: chatActions.mute(chatMenu.chatId, -1)
        }
        AppMenuSeparator {}
        AppMenuItem {
            visible: chatMenu.info.type !== "channel"
            text: qsTr("Clear history")
            iconName: "trash"
            danger: true
            onTriggered: confirmDialog.ask(
                qsTr("Clear the history of \u201c%1\u201d?").arg(chatMenu.chatTitle), "",
                qsTr("Clear"),
                chatMenu.info.canDeleteForAll ? qsTr("Also for %1").arg(chatMenu.chatTitle) : "",
                {action: "clear", chatId: chatMenu.chatId})
        }
        AppMenuItem {
            text: chatMenu.info.type === "channel" ? qsTr("Leave channel")
                  : chatMenu.group ? qsTr("Leave group") : qsTr("Delete chat")
            iconName: "close"
            danger: true
            onTriggered: confirmDialog.ask(
                chatMenu.group || chatMenu.info.type === "channel"
                    ? qsTr("Leave \u201c%1\u201d?").arg(chatMenu.chatTitle)
                    : qsTr("Delete the chat with %1?").arg(chatMenu.chatTitle), "",
                chatMenu.group || chatMenu.info.type === "channel" ? qsTr("Leave")
                                                                  : qsTr("Delete"),
                !chatMenu.group && chatMenu.info.canDeleteForAll
                    ? qsTr("Also for %1").arg(chatMenu.chatTitle) : "",
                {action: "leave", chatId: chatMenu.chatId})
        }
    }

    FolderEditorDialog {
        id: folderDialog
    }

    ConfirmDialog {
        id: confirmDialog
        objectName: "confirmDialog"
        onAccepted: (checked, payload) => {
            if (payload.action === "clear")
                chatActions.clearHistory(payload.chatId, checked)
            else
                chatActions.leave(payload.chatId, checked)
        }
    }

    Connections {
        target: chatActions
        function onLeft(chatId) {
            if (chatId === root.selectedChatId) {
                root.selectedChatId = 0
                messages.close()
            }
        }
    }

    Rectangle {
        SplitView.fillWidth: true
        color: Theme.window

        MessageView {
            id: messageView
            anchors.fill: parent
            visible: root.selectedChatId !== 0
            onOpenChatRequested: (chatId, messageId) => root.openChat(chatId, messageId)
            onSearchRequested: query => {  // a hashtag in a message
                searchField.text = query
                search.query = query
            }
        }

        Text {
            anchors.centerIn: parent
            visible: root.selectedChatId === 0
            text: qsTr("Select a chat to start messaging")
            color: Theme.textMuted
            font.pixelSize: Theme.fontTitle
        }
    }
}
