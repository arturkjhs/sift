import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: root
    objectName: "messageView"
    property var replyToId: 0
    property bool summaryOpen: false
    property var highlightId: 0          // message to flash after a jump (search, quote, summary)
    readonly property bool isGroupChat: messages.chatType === "group" || messages.chatType === "supergroup"

    Connections {
        target: messages
        function onChatChanged() {
            if (messages.chatId !== list.lastChatId) {
                list.lastChatId = messages.chatId
                list.stickToBottom = true
                root.replyToId = 0
                root.summaryOpen = false
                composer.focusInput()
            }
        }
    }

    // Rich text is generated in Python, so it needs the theme's colors.
    Binding { target: messages; property: "linkColor"; value: Theme.link.toString() }
    Binding { target: messages; property: "codeBackground"; value: Theme.codeBackground.toString() }
    Binding { target: messages; property: "spoilerColor"; value: Theme.spoiler.toString() }
    Binding { target: ai; property: "chatId"; value: messages.chatId }
    Binding { target: ai; property: "linkColor"; value: Theme.link.toString() }
    Binding { target: ai; property: "codeBackground"; value: Theme.codeBackground.toString() }

    Connections {
        target: messages
        function onJumpReady(row) {
            list.stickToBottom = false
            list.positionViewAtIndex(row, ListView.Center)
            flashTimer.restart()
        }
    }

    // Scroll to a message (loading older history if needed) and flash it.
    function showMessage(messageId) {
        root.highlightId = messageId
        messages.jumpTo(messageId)
    }

    function summarizePerson(senderKey, name) {
        ai.summarizePerson(senderKey, name)
        root.summaryOpen = true
    }

    Timer {
        id: flashTimer
        interval: 1600
        onTriggered: root.highlightId = 0
    }

    function summarize(scope) {
        ai.summarize(scope)
        root.summaryOpen = true
    }

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
                anchors.right: aiTools.left
                anchors.rightMargin: 8
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

            Row {
                id: aiTools
                anchors.right: parent.right
                anchors.rightMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                spacing: 6
                visible: ai.available

                PillButton {
                    id: summaryButton
                    objectName: "summaryButton"
                    visible: ai.enabled
                    iconName: "sparkle"
                    text: qsTr("Summarize")
                    onClicked: summaryMenu.popup(summaryButton, 0, summaryButton.height + 4)
                }

                // AI switch: filled when on. Turning it on asks first, turning it off doesn't.
                PillButton {
                    objectName: "aiSwitch"
                    filled: ai.enabled
                    text: ai.enabled ? qsTr("AI on") : qsTr("AI off")
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: qsTr("Summaries and voice transcription for this chat")
                    onClicked: {
                        if (ai.enabled) {
                            ai.setEnabled(false)
                            root.summaryOpen = false
                        } else {
                            aiConsent.open()
                        }
                    }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 1
            color: Theme.separator
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

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
                    flashed: messageId === root.highlightId
                    onJumpRequested: id => root.showMessage(id)
                    onMenuRequested: id => {
                        messageMenu.messageId = id
                        messageMenu.fileId = fileState === "ready" ? fileId : 0
                        messageMenu.isVoice = mediaKind === "voice"
                        messageMenu.transcribed = transcriptState === "done"
                                                  || transcriptState === "pending"
                        messageMenu.senderKey = isOutgoing ? "" : senderKey
                        messageMenu.senderName = senderName
                        messageMenu.popup()
                    }
                    onSenderClicked: (key, name, id) => {
                        personMenu.senderKey = key
                        personMenu.senderName = name
                        personMenu.messageId = id
                        personMenu.initials = senderInitials
                        personMenu.avatarSource = senderAvatar
                        personMenu.colorIndex = senderColor
                        personMenu.popup()
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

            Rectangle {
                Layout.fillHeight: true
                implicitWidth: 1
                visible: summaryPanel.visible
                color: Theme.separator
            }

            SummaryPanel {
                id: summaryPanel
                Layout.fillHeight: true
                Layout.preferredWidth: Math.min(380, root.width * 0.42)
                visible: root.summaryOpen
                onCloseRequested: root.summaryOpen = false
                onMessageRequested: id => root.showMessage(id)
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

    // Back to the newest message.
    AbstractButton {
        x: list.width - width - 18
        anchors.bottom: parent.bottom
        anchors.bottomMargin: composer.height + 14
        width: 38
        height: 38
        visible: !list.atYEnd && list.count > 0
        hoverEnabled: true
        Accessible.name: qsTr("Scroll to latest")
        onClicked: {
            list.stickToBottom = true
            list.positionViewAtBeginning()
        }

        background: Item {
            Rectangle {
                anchors.fill: parent
                anchors.topMargin: 2
                anchors.bottomMargin: -2
                radius: width / 2
                color: Theme.shadow
                opacity: 0.5
            }
            Rectangle {
                anchors.fill: parent
                radius: width / 2
                color: parent.parent.hovered ? Theme.hover : Theme.popup
                border.width: 1
                border.color: Theme.popupBorder
            }
        }
        contentItem: Item {
            Icon {
                anchors.centerIn: parent
                name: "arrow-down"
                size: 18
            }
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

    AppMenu {
        id: summaryMenu
        AppMenuItem {
            text: qsTr("Unread messages")
            iconName: "summary"
            onTriggered: root.summarize("unread")
        }
        AppMenuItem {
            text: qsTr("Last 24 hours")
            iconName: "summary"
            onTriggered: root.summarize("day")
        }
        AppMenuItem {
            text: qsTr("Last 7 days")
            iconName: "summary"
            onTriggered: root.summarize("week")
        }
        AppMenuItem {
            // Private chat: the chat id is the other person's user id.
            visible: messages.chatType === "private"
            text: qsTr("About %1").arg(messages.chatTitle)
            iconName: "person"
            onTriggered: root.summarizePerson("user:" + messages.chatId, messages.chatTitle)
        }
        AppMenuSeparator {}
        AppMenuItem {
            text: qsTr("Show last summary")
            iconName: "open"
            onTriggered: {
                ai.showChatSummary()
                root.summaryOpen = true
            }
        }
    }

    Popup {
        id: aiConsent
        objectName: "aiConsent"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(460, root.width - 48)
        modal: true
        padding: 20
        background: Rectangle {
            radius: 12
            color: Theme.sidebar
            border.width: 1
            border.color: Theme.separator
        }

        contentItem: ColumnLayout {
            spacing: 12

            Text {
                Layout.fillWidth: true
                text: qsTr("Turn on AI for “%1”?").arg(messages.chatTitle)
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                color: Theme.text
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: Theme.text
                font.pixelSize: Theme.fontBody
                text: qsTr("Nothing is sent automatically. When you press Summarize, the messages "
                           + "in the chosen range (text, captions, sender names, times, "
                           + "transcripts) are sent to OpenRouter and processed by %1. "
                           + "When you press Transcribe, that voice message's audio is sent to %2.")
                      .arg(ai.summaryModel).arg(ai.transcriptionModel)
            }
            Text {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: Theme.textMuted
                font.pixelSize: Theme.fontSmall
                text: qsTr("Requests are routed only to providers with zero data retention. "
                           + "Results are stored on this computer only.")
            }
            Text {
                Layout.fillWidth: true
                visible: !ai.configured
                wrapMode: Text.Wrap
                color: Theme.danger
                font.pixelSize: Theme.fontSmall
                text: qsTr("No OpenRouter API key yet: add it in Settings.")
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Item { Layout.fillWidth: true }
                PillButton {
                    text: qsTr("Cancel")
                    onClicked: aiConsent.close()
                }
                PillButton {
                    objectName: "aiConsentAccept"
                    text: qsTr("Turn on")
                    filled: true
                    onClicked: {
                        ai.setEnabled(true)
                        aiConsent.close()
                    }
                }
            }
        }
    }

    AppMenu {
        id: messageMenu
        objectName: "messageMenu"
        property var messageId: 0
        property var fileId: 0
        property bool isVoice: false
        property bool transcribed: false
        property string senderKey: ""
        property string senderName: ""

        AppMenuItem {
            text: qsTr("Reply")
            iconName: "reply"
            hint: qsTr("Double-click")
            onTriggered: {
                root.replyToId = messageMenu.messageId
                composer.focusInput()
            }
        }
        AppMenuItem {
            text: messageMenu.isVoice ? qsTr("Copy transcript") : qsTr("Copy text")
            iconName: "copy"
            visible: !messageMenu.isVoice || messageMenu.transcribed
            onTriggered: messages.copyText(messageMenu.messageId)
        }
        AppMenuItem {
            text: qsTr("Transcribe")
            iconName: "transcript"
            visible: messageMenu.isVoice && !messageMenu.transcribed && ai.enabled
            onTriggered: ai.transcribe(messageMenu.messageId)
        }
        AppMenuItem {
            text: qsTr("Open file")
            iconName: "open"
            visible: messageMenu.fileId !== 0
            onTriggered: messages.openFile(messageMenu.fileId)
        }
        AppMenuItem {
            text: qsTr("Show in folder")
            iconName: "folder"
            visible: messageMenu.fileId !== 0
            onTriggered: messages.showInFolder(messageMenu.fileId)
        }
        AppMenuSeparator {
            visible: personItem.visible
            height: visible ? implicitHeight : 0
        }
        AppMenuItem {
            id: personItem
            visible: messageMenu.senderKey !== "" && root.isGroupChat && ai.available
            enabled: ai.enabled
            text: qsTr("About %1").arg(messageMenu.senderName)
            hint: ai.enabled ? "" : qsTr("AI is off")
            iconName: "sparkle"
            onTriggered: root.summarizePerson(messageMenu.senderKey, messageMenu.senderName)
        }
    }

    // Click on a sender's name or avatar in a group.
    AppMenu {
        id: personMenu
        objectName: "personMenu"
        property string senderKey: ""
        property string senderName: ""
        property var messageId: 0
        property string initials: ""
        property string avatarSource: ""
        property int colorIndex: 0

        Item {  // header: who this is about
            implicitWidth: personHeader.implicitWidth + 20
            implicitHeight: 46

            Row {
                id: personHeader
                x: 10
                anchors.verticalCenter: parent.verticalCenter
                spacing: 10

                Avatar {
                    anchors.verticalCenter: parent.verticalCenter
                    size: 30
                    avatarSource: personMenu.avatarSource
                    initials: personMenu.initials
                    colorIndex: personMenu.colorIndex
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: personMenu.senderName
                    textFormat: Text.PlainText
                    color: Theme.text
                    font.pixelSize: Theme.fontBody
                    font.weight: Font.DemiBold
                }
            }
        }
        AppMenuSeparator {}
        AppMenuItem {
            text: qsTr("Reply")
            iconName: "reply"
            onTriggered: {
                root.replyToId = personMenu.messageId
                composer.focusInput()
            }
        }
        AppMenuItem {
            text: qsTr("Summarize this person")
            iconName: "sparkle"
            visible: ai.available
            enabled: ai.enabled
            hint: ai.enabled ? "" : qsTr("AI is off")
            onTriggered: root.summarizePerson(personMenu.senderKey, personMenu.senderName)
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
