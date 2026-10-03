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

    signal openChatRequested(var chatId, var messageId)

    // Put a draft (saved, or changed on another device) into the composer without saving it back.
    function loadDraft(text, replyTo) {
        composer.draftSuspended = true
        root.replyToId = replyTo
        composer.loadDraft(text)
        composer.draftSuspended = false
    }

    Connections {
        target: messages
        function onChatChanged() {
            if (messages.chatId !== list.lastChatId) {
                list.lastChatId = messages.chatId
                list.stickToBottom = true
                list.anchorId = 0
                root.loadDraft(composerModel.draftText, composerModel.draftReplyTo)
                root.summaryOpen = false
                composer.focusInput()
            }
        }
        // Opened with unread messages: start at the first one, under the separator.
        function onUnreadReady(messageId) {
            list.stickToBottom = false
            list.anchorId = messageId
            list.pinIfSticky()
        }
        function onActionsReady(messageId, actions) {
            if (messageId !== messageMenu.messageId || !messageMenu.waiting)
                return
            messageMenu.waiting = false
            messageMenu.actions = actions
            messageMenu.popup()
        }
    }

    Connections {
        target: composerModel
        function onRemoteDraft(text, replyTo) { root.loadDraft(text, replyTo) }
    }

    // "Insert" on a suggested reply: into the input as a reply to that message, not sent.
    Connections {
        target: ai
        function onInsertReply(text, messageId) {
            root.replyToId = messageId
            composer.useText(text)
        }
    }

    Timer {  // "last seen 5 minutes ago" ages
        interval: 30000
        repeat: true
        running: root.visible
        onTriggered: messages.refreshStatus()
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
        function onViewerRequested(messageId) { viewer.open(messageId) }
        // A t.me link to a message: jump to it here, or open the other chat.
        function onLinkResolved(chatId, messageId) {
            if (chatId !== messages.chatId)
                root.openChatRequested(chatId, messageId)
            else if (messageId)
                root.showMessage(messageId)
        }
        function onJumpReady(row) {
            list.stickToBottom = false
            list.anchorId = 0
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
                    objectName: "chatStatus"
                    width: parent.width
                    text: messages.chatStatus !== "" ? messages.chatStatus
                          : messages.loading ? qsTr("Loading messages") : ""
                    visible: text !== ""
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    color: messages.chatStatusActive ? Theme.accent : Theme.textMuted
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
                // Or keep this message (the first unread) at the top, for the same reason.
                property var anchorId: 0

                function pinIfSticky() {
                    if (anchorId !== 0) {
                        const row = messages.rowOf(anchorId)
                        if (row >= 0)
                            positionViewAtIndex(row, ListView.End)
                    } else if (stickToBottom) {
                        positionViewAtBeginning()
                    }
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
                    onReactionToggled: (id, key) => messages.toggleReaction(id, key)
                    onMenuRequested: id => {
                        messageMenu.waiting = true   // pops up on actionsReady
                        messageMenu.messageId = id
                        messageMenu.fileId = fileState === "ready" ? fileId : 0
                        messageMenu.isVoice = mediaKind === "voice"
                        messageMenu.transcribed = transcriptState === "done"
                                                  || transcriptState === "pending"
                        messageMenu.senderKey = isOutgoing ? "" : senderKey
                        messageMenu.senderName = senderName
                        messageMenu.mediaKind = mediaKind
                        messageMenu.fileName = fileName
                        messageMenu.translated = translationState !== ""
                        messages.requestActions(id)
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
                    onLinkActivated: link => messages.openLink(link)
                }

                onContentYChanged: viewTimer.restart()
                onCountChanged: {
                    viewTimer.restart()
                    Qt.callLater(pinIfSticky)
                }
                onContentHeightChanged: Qt.callLater(pinIfSticky)
                onMovementStarted: anchorId = 0
                onMovementEnded: stickToBottom = atYEnd

                ScrollBar.vertical: ScrollBar {
                    onPressedChanged: {
                        list.anchorId = 0
                        if (!pressed)
                            list.stickToBottom = list.atYEnd
                    }
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
                onReactionRequested: (id, key) => messages.addReaction(id, key)
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
                list.anchorId = 0
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
            list.anchorId = 0
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

    // Drop files anywhere on the chat to send them (with a caption, from SendFilesDialog).
    DropArea {
        id: dropArea
        anchors.fill: parent
        enabled: messages.canWrite
        keys: ["text/uri-list"]
        onDropped: drop => {
            if (drop.hasUrls) {
                composerModel.stage(drop.urls)
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
            objectName: "askItem"
            text: qsTr("Ask about this chat…")
            iconName: "search"
            onTriggered: {
                ai.openPanel("ask")
                root.summaryOpen = true
                summaryPanel.focusQuestion()
            }
        }
        AppMenuItem {
            text: qsTr("Dates and meetings")
            iconName: "calendar"
            hint: qsTr("30 days")
            onTriggered: {
                ai.findEvents()
                root.summaryOpen = true
            }
        }
        AppMenuItem {
            text: qsTr("Show last summary")
            iconName: "open"
            onTriggered: {
                ai.showChatSummary()
                root.summaryOpen = true
            }
        }
        AppMenuSeparator {}
        AppMenuItem {
            objectName: "digestItem"
            text: qsTr("Include in the digest")
            iconName: "inbox"
            hint: ai.digestEnabled ? "\u2713" : ""
            onTriggered: ai.setDigest(!ai.digestEnabled)
        }
        AppMenuItem {
            objectName: "smartItem"
            text: qsTr("Smart notifications\u2026")
            iconName: "bell"
            hint: ai.smartNotify ? "\u2713" : ""
            onTriggered: ai.smartNotify ? ai.setSmartNotify(false) : smartConsent.open()
        }
        AppMenuItem {
            enabled: false
            text: qsTr("Spent here this month: %1").arg(ai.spentHere)
        }
    }

    Popup {
        id: smartConsent
        objectName: "smartConsent"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(440, root.width - 48)
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
                text: qsTr("Smart notifications for \u201c%1\u201d?").arg(messages.chatTitle)
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
                text: qsTr("You'll be notified only about messages that concern you: addressed to "
                           + "you, asking you something, or with news for you. To decide, every "
                           + "new message that would notify you is sent automatically, with a few "
                           + "earlier ones, to %1 via OpenRouter. This is the only AI feature "
                           + "that works without a click.").arg(ai.cheapModel)
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Item { Layout.fillWidth: true }
                PillButton {
                    text: qsTr("Cancel")
                    onClicked: smartConsent.close()
                }
                PillButton {
                    objectName: "smartConsentAccept"
                    text: qsTr("Turn on")
                    filled: true
                    onClicked: {
                        ai.setSmartNotify(true)
                        smartConsent.close()
                    }
                }
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
                wrapMode: Text.Wrap
                color: Theme.textMuted
                font.pixelSize: Theme.fontSmall
                text: qsTr("AI answers in %1. Change it in Settings.").arg(ai.languageLabel)
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
        property bool waiting: false
        property string mediaKind: ""
        property string fileName: ""
        property bool translated: false
        readonly property bool askableFile: mediaKind === "document"
            && /\.(pdf|txt|md|csv|tsv|json|xml|ya?ml|html?|log|ini|toml|py|js|ts|java|c|cpp|h|go|rs|sh|sql|rtf|srt)$/i
               .test(fileName)
        property var actions: ({})
        readonly property var reactionKeys: actions.reactions || []      // [{key, label}]
        readonly property var allReactions: actions.allReactions || []
        readonly property var chosenKeys: actions.chosen || []
        readonly property string contextHint: (actions.aiContext || 0) === 1
            ? qsTr("1 message") : qsTr("%1 messages").arg(actions.aiContext || 0)

        // The full grid opens where the menu was, kept inside the window.
        function openAllReactions() {
            const at = messageMenu.parent.mapToItem(Overlay.overlay, messageMenu.x, messageMenu.y)
            const overlay = Overlay.overlay
            reactionPicker.x = Math.max(8, Math.min(at.x, overlay.width - reactionPicker.width - 8))
            reactionPicker.y = Math.max(8, Math.min(at.y, overlay.height - reactionPicker.height - 8))
            reactionPicker.openAt(messageMenu.messageId, messageMenu.allReactions,
                                  messageMenu.chosenKeys)
            messageMenu.close()
        }

        // Wide enough for the whole row of quick reactions and the expand button.
        width: Math.max(implicitWidth, reactionBar.visible
                        ? reactionBar.implicitWidth + leftPadding + rightPadding : 0)

        Item {  // quick reactions
            id: reactionBar
            objectName: "reactionBar"
            visible: messageMenu.reactionKeys.length > 0
            implicitWidth: reactionRow.implicitWidth + 8
            implicitHeight: visible ? 44 : 0

            Row {
                id: reactionRow
                x: 4
                anchors.verticalCenter: parent.verticalCenter
                spacing: 2
                Repeater {
                    model: messageMenu.reactionKeys
                    delegate: AbstractButton {
                        id: reactionButton
                        required property var modelData
                        readonly property bool chosen:
                            messageMenu.chosenKeys.indexOf(modelData.key) >= 0
                        width: 34
                        height: 34
                        hoverEnabled: true
                        Accessible.name: modelData.label
                        onClicked: {
                            messages.toggleReaction(messageMenu.messageId, modelData.key)
                            messageMenu.close()
                        }
                        background: Rectangle {
                            radius: height / 2
                            color: reactionButton.chosen ? Theme.selection
                                 : reactionButton.hovered ? Theme.hover : "transparent"
                        }
                        contentItem: Text {
                            text: reactionButton.modelData.label
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                            font.pixelSize: reactionButton.hovered ? 22 : 19
                            Behavior on font.pixelSize { NumberAnimation { duration: 80 } }
                        }
                    }
                }
                AbstractButton {  // all the other reactions
                    id: moreReactions
                    objectName: "moreReactions"
                    visible: messageMenu.allReactions.length > messageMenu.reactionKeys.length
                    width: 34
                    height: 34
                    hoverEnabled: true
                    Accessible.name: qsTr("More reactions")
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: Accessible.name
                    onClicked: messageMenu.openAllReactions()
                    background: Rectangle {
                        radius: height / 2
                        color: moreReactions.hovered ? Theme.hover : Theme.pill
                    }
                    contentItem: Item {
                        Icon {
                            anchors.centerIn: parent
                            name: "chevron-down"
                            size: 16
                        }
                    }
                }
            }
        }
        AppMenuSeparator {
            visible: messageMenu.reactionKeys.length > 0
            height: visible ? implicitHeight : 0
        }
        AppMenuItem {
            text: qsTr("Reply")
            iconName: "reply"
            hint: qsTr("Double-click")
            visible: messageMenu.actions.canReply !== false
            onTriggered: {
                root.replyToId = messageMenu.messageId
                composer.focusInput()
            }
        }
        AppMenuItem {
            objectName: "editItem"
            text: qsTr("Edit")
            iconName: "edit"
            hint: qsTr("↑")
            visible: messageMenu.actions.canEdit === true
            onTriggered: messages.startEdit(messageMenu.messageId)
        }
        AppMenuItem {
            text: qsTr("Forward")
            iconName: "forward"
            visible: messageMenu.actions.canForward === true
            onTriggered: forwardDialog.pick(messageMenu.messageId)
        }
        AppMenuItem {
            text: messageMenu.isVoice ? qsTr("Copy transcript") : qsTr("Copy text")
            iconName: "copy"
            visible: messageMenu.isVoice ? messageMenu.transcribed
                                         : messageMenu.actions.canCopy === true
            onTriggered: messages.copyText(messageMenu.messageId)
        }
        AppMenuItem {
            objectName: "translateItem"
            text: qsTr("Translate")
            iconName: "translate"
            visible: ai.enabled && messageMenu.actions.canCopy === true && !messageMenu.translated
            onTriggered: ai.translate(messageMenu.messageId)
        }
        AppMenuItem {
            objectName: "explainItem"
            text: qsTr("Explain")
            iconName: "sparkle"
            // How much context leaves the computer, said before the click.
            hint: messageMenu.contextHint
            visible: ai.enabled && (messageMenu.actions.aiContext || 0) > 0
            onTriggered: {
                ai.explainMessage(messageMenu.messageId, messageMenu.senderName)
                root.summaryOpen = true
            }
        }
        AppMenuItem {
            objectName: "suggestReplyItem"
            text: qsTr("Suggest reply")
            iconName: "reply"
            hint: messageMenu.contextHint
            visible: ai.enabled && (messageMenu.actions.aiContext || 0) > 0
                     && messageMenu.actions.canReply !== false
            onTriggered: {
                ai.suggestReplies(messageMenu.messageId, messageMenu.senderName)
                root.summaryOpen = true
            }
        }
        AppMenuItem {
            text: qsTr("Collect answers")
            iconName: "summary"
            visible: ai.enabled && root.isGroupChat
            onTriggered: {
                ai.collectAnswers(messageMenu.messageId,
                                  messages.replyPreview(messageMenu.messageId).text || "")
                root.summaryOpen = true
            }
        }
        AppMenuItem {
            text: qsTr("Ask about this file\u2026")
            iconName: "file"
            visible: ai.enabled && messageMenu.askableFile
            onTriggered: {
                ai.openDocument(messageMenu.messageId, messageMenu.fileName)
                root.summaryOpen = true
                summaryPanel.focusQuestion()
            }
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
        AppMenuSeparator {
            visible: deleteItem.visible
            height: visible ? implicitHeight : 0
        }
        AppMenuItem {
            id: deleteItem
            text: qsTr("Delete")
            iconName: "trash"
            danger: true
            visible: messageMenu.actions.canDelete === true
            onTriggered: deleteDialog.ask(messageMenu.messageId,
                                          messageMenu.actions.canDeleteForAll === true)
        }
    }

    ReactionPicker {
        id: reactionPicker
        parent: Overlay.overlay
        onPicked: (messageId, key) => messages.toggleReaction(messageId, key)
    }

    ForwardDialog {
        id: forwardDialog
        onChatPicked: chatId => {
            messages.forward(forwardDialog.messageId, chatId)
            root.openChatRequested(chatId, 0)
        }
    }

    Popup {
        id: deleteDialog
        objectName: "deleteDialog"
        property var messageId: 0
        property bool canRevoke: false

        function ask(messageId, canRevoke) {
            deleteDialog.messageId = messageId
            deleteDialog.canRevoke = canRevoke
            revokeSwitch.checked = true
            open()
        }

        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(380, root.width - 48)
        modal: true
        padding: 20
        background: Rectangle {
            radius: 12
            color: Theme.sidebar
            border.width: 1
            border.color: Theme.separator
        }

        contentItem: ColumnLayout {
            spacing: 14

            Text {
                Layout.fillWidth: true
                text: qsTr("Delete this message?")
                color: Theme.text
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }
            RowLayout {
                Layout.fillWidth: true
                visible: deleteDialog.canRevoke
                spacing: 10
                ToggleSwitch { id: revokeSwitch }
                Text {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: messages.chatType === "private"
                          ? qsTr("Also delete for %1").arg(messages.chatTitle)
                          : qsTr("Delete for everyone")
                    color: Theme.text
                    font.pixelSize: Theme.fontBody
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Item { Layout.fillWidth: true }
                PillButton {
                    text: qsTr("Cancel")
                    onClicked: deleteDialog.close()
                }
                PillButton {
                    objectName: "deleteConfirm"
                    text: qsTr("Delete")
                    filled: true
                    danger: true
                    onClicked: {
                        messages.deleteMessage(deleteDialog.messageId,
                                               deleteDialog.canRevoke && revokeSwitch.checked)
                        deleteDialog.close()
                    }
                }
            }
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
