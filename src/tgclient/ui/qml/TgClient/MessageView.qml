import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: root
    property var replyToId: 0
    readonly property bool isGroupChat: messages.chatType === "group" || messages.chatType === "supergroup"

    Connections {
        target: messages
        function onChatChanged() {
            if (messages.chatId !== list.lastChatId) {
                list.lastChatId = messages.chatId
                list.stickToBottom = true
                root.replyToId = 0
                composer.focusInput()
            }
        }
    }

    // Rich text is generated in Python, so it needs the theme's colors.
    Binding { target: messages; property: "linkColor"; value: Theme.link.toString() }
    Binding { target: messages; property: "codeBackground"; value: Theme.codeBackground.toString() }
    Binding { target: messages; property: "spoilerColor"; value: Theme.spoiler.toString() }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 52
            color: Theme.sidebar

            Column {
                anchors.left: parent.left
                anchors.leftMargin: 16
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter

                Text {
                    width: parent.width
                    text: messages.chatTitle
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    color: Theme.text
                    font.pixelSize: 15
                    font.weight: Font.DemiBold
                }
                Text {
                    text: messages.loading ? qsTr("Loading messages") : ""
                    visible: text !== ""
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 1
            color: Theme.separator
        }

        ListView {
            id: list
            property var lastChatId: 0
            // Follow the newest message until the user scrolls away. Delegates finish laying
            // out (and images load) after the first positioning, so re-pin when content grows.
            property bool stickToBottom: true

            function pinIfSticky() {
                if (stickToBottom)
                    positionViewAtBeginning()
            }

            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: messages
            // Row 0 is the newest message: older pages are appended on top without moving
            // what's on screen, and new messages stick to the bottom.
            verticalLayoutDirection: ListView.BottomToTop
            // Mouse drag selects text instead of flicking; wheel and touchpad still scroll.
            acceptedButtons: Qt.NoButton
            boundsBehavior: Flickable.StopAtBounds
            cacheBuffer: 1200

            header: Item { height: 10 }   // spacing above the composer (BottomToTop)
            footer: Item { height: 10 }

            delegate: MessageDelegate {
                isGroupChat: root.isGroupChat
                onReplyRequested: id => {
                    root.replyToId = id
                    composer.focusInput()
                }
                onJumpRequested: id => {
                    const row = messages.rowOf(id)
                    if (row >= 0)
                        list.positionViewAtIndex(row, ListView.Center)
                }
                onMenuRequested: id => {
                    messageMenu.messageId = id
                    messageMenu.fileId = fileState === "ready" ? fileId : 0
                    messageMenu.popup()
                }
                onLinkActivated: link => {
                    if (!link.startsWith("tg://"))   // TODO: handle tg:// links in-app
                        Qt.openUrlExternally(link)
                }
            }

            onContentYChanged: viewTimer.restart()
            onCountChanged: {
                viewTimer.restart()
                Qt.callLater(pinIfSticky)
            }
            onContentHeightChanged: Qt.callLater(pinIfSticky)
            onMovementEnded: stickToBottom = atYEnd

            ScrollBar.vertical: ScrollBar {
                onPressedChanged: if (!pressed) list.stickToBottom = list.atYEnd
            }

            Text {
                anchors.centerIn: parent
                visible: list.count === 0 && !messages.loading
                text: qsTr("No messages here yet")
                color: Theme.textMuted
                font.pixelSize: Theme.fontBody
            }
        }

        Composer {
            id: composer
            Layout.fillWidth: true
            visible: messages.canWrite
            replyToId: root.replyToId
            onCancelReply: root.replyToId = 0
            onSent: {
                root.replyToId = 0
                list.stickToBottom = true
                list.positionViewAtBeginning()
            }
        }
    }

    RoundButton {
        anchors.right: parent.right
        anchors.rightMargin: 20
        anchors.bottom: parent.bottom
        anchors.bottomMargin: composer.height + 16
        visible: !list.atYEnd && list.count > 0
        text: "\u2193"
        font.pixelSize: 16
        Accessible.name: qsTr("Scroll to latest")
        onClicked: {
            list.stickToBottom = true
            list.positionViewAtBeginning()
        }
    }

    // Drop files anywhere on the chat to send them.
    DropArea {
        id: dropArea
        anchors.fill: parent
        enabled: messages.canWrite
        keys: ["text/uri-list"]
        onDropped: drop => {
            if (drop.hasUrls) {
                messages.sendFiles(drop.urls)
                drop.acceptProposedAction()
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: 12
        visible: dropArea.containsDrag
        radius: 12
        color: Qt.rgba(Theme.window.r, Theme.window.g, Theme.window.b, 0.9)
        border.width: 2
        border.color: Theme.accent

        Text {
            anchors.centerIn: parent
            text: qsTr("Drop files to send them")
            color: Theme.accent
            font.pixelSize: 16
            font.weight: Font.DemiBold
        }
    }

    Menu {
        id: messageMenu
        property var messageId: 0
        property var fileId: 0
        MenuItem {
            text: qsTr("Reply")
            onTriggered: {
                root.replyToId = messageMenu.messageId
                composer.focusInput()
            }
        }
        MenuItem {
            text: qsTr("Copy text")
            onTriggered: messages.copyText(messageMenu.messageId)
        }
        MenuItem {
            text: qsTr("Show in folder")
            enabled: messageMenu.fileId !== 0
            height: enabled ? implicitHeight : 0
            visible: enabled
            onTriggered: messages.showInFolder(messageMenu.fileId)
        }
    }

    // Mark messages as read once they've been on screen for a moment.
    Timer {
        id: viewTimer
        interval: 300
        onTriggered: {
            if (!root.visible || list.count === 0)
                return
            const top = list.indexAt(list.width / 2, list.contentY + 1)
            const bottom = list.indexAt(list.width / 2, list.contentY + list.height - 1)
            messages.markViewed(top >= 0 ? top : list.count - 1, bottom >= 0 ? bottom : 0)
        }
    }
}
