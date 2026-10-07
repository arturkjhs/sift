import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: root
    objectName: "messageView"
    property var replyToId: 0
    property bool summaryOpen: false
    property bool profileOpen: false
    onSummaryOpenChanged: if (summaryOpen) profileOpen = false
    onProfileOpenChanged: if (profileOpen) summaryOpen = false

    function showProfile(chatId, userId) {
        profile.open(chatId, userId)
        root.profileOpen = true
    }
    property var highlightId: 0          // message to flash after a jump (search, quote, summary)
    readonly property bool isGroupChat: messages.chatType === "group" || messages.chatType === "supergroup"

    signal openChatRequested(var chatId, var messageId)
    signal searchRequested(string query)

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
                root.profileOpen = false
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
            if (messageId === reactionPicker.waitingFor) {  // the hover button
                reactionPicker.waitingFor = 0
                const overlay = reactionPicker.parent  // Overlay.overlay (null in Connections)
                const reactions = actions.allReactions || []
                if (reactions.length === 0)
                    return
                reactionPicker.openAt(messageId, reactions, actions.chosen || [])
                reactionPicker.x = Math.max(8, Math.min(reactionPicker.anchorPoint.x - 8,
                                                        overlay.width - reactionPicker.width - 8))
                reactionPicker.y = Math.max(8, reactionPicker.anchorPoint.y - reactionPicker.height - 6)
                return
            }
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
    Binding { target: ai; property: "topicId"; value: messages.topicId }
    Binding { target: ai; property: "linkColor"; value: Theme.link.toString() }
    Binding { target: ai; property: "codeBackground"; value: Theme.codeBackground.toString() }

    Connections {
        target: messages
        function onViewerRequested(messageId) { viewer.open(messageId) }
        // A t.me link to a message: jump to it here, or open the other chat.
        function onSearchRequested(query) { root.searchRequested(query) }
        function onBotAnswer(text, alert) {
            if (alert)
                botAlert.ask(text, "", qsTr("OK"), "", null)
            else
                toast.show(text)
        }
        function onInviteReady(info) { inviteDialog.show(info) }
        function onJoinRequested() { joinNotice.visible = true }
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

    function openSearch() {
        chatSearch.visible = true
        chatSearchBox.focusInput()
    }

    function closeSearch() {
        chatSearch.visible = false
        messages.endChatSearch()
        composer.focusInput()
    }

    Connections {
        target: messages
        function onChatSearchJump(messageId) { root.showMessage(messageId) }
        function onChatChanged() {  // another chat or topic: the search is over
            const scope = messages.chatId + "/" + messages.topicId
            if (scope !== chatSearch.scope) {
                chatSearch.scope = scope
                chatSearch.visible = false
                chatSearchBox.text = ""
            }
        }
    }
    Binding { target: messages; property: "searchHighlight"
              value: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.3).toString() }

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

            IconButton {  // in a forum topic: back to the list of topics
                id: topicBack
                objectName: "topicBack"
                x: 8
                anchors.verticalCenter: parent.verticalCenter
                visible: messages.topicId !== 0 || messages.threadMode
                iconName: "back"
                glyphSize: 16
                Accessible.name: messages.threadMode ? qsTr("Back to the channel")
                                                     : qsTr("All topics")
                onClicked: messages.threadMode ? messages.closeComments() : messages.closeTopic()
            }

            Column {
                anchors.left: topicBack.visible ? topicBack.right : parent.left
                anchors.leftMargin: topicBack.visible ? 4 : 16
                anchors.right: aiTools.left
                anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter

                // The title opens the chat's profile.
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    objectName: "headerTap"
                    onTapped: root.profileOpen ? root.profileOpen = false
                                               : root.showProfile(messages.chatId, 0)
                }

                Text {
                    width: parent.width
                    text: messages.threadMode ? qsTr("Comments")
                          : messages.topicId !== 0 && messages.topicName !== ""
                            ? messages.topicName : messages.chatTitle
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    color: Theme.text
                    font.pixelSize: 15
                    font.weight: Font.DemiBold
                }
                Text {
                    objectName: "chatStatus"
                    width: parent.width
                    text: messages.threadMode ? messages.threadChannelTitle
                          : messages.topicId !== 0 ? messages.chatTitle
                          : messages.topicsMode ? (topicList.count === 1 ? qsTr("1 topic")
                                                   : qsTr("%1 topics").arg(topicList.count))
                          : messages.chatStatus !== "" ? messages.chatStatus
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

                IconButton {
                    objectName: "chatSearchButton"
                    anchors.verticalCenter: parent.verticalCenter
                    visible: !messages.topicsMode
                    iconName: "search"
                    glyphSize: 17
                    Accessible.name: qsTr("Search in this chat")
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: Qt.platform.os === "osx" ? qsTr("Search in this chat (\u2318F)")
                                                          : qsTr("Search in this chat (Ctrl+F)")
                    onClicked: chatSearch.visible ? root.closeSearch() : root.openSearch()
                }

                PillButton {
                    id: summaryButton
                    objectName: "summaryButton"
                    visible: ai.available && ai.enabled
                    iconName: "sparkle"
                    text: qsTr("Summarize")
                    onClicked: summaryMenu.popup(summaryButton, 0, summaryButton.height + 4)
                }

                // AI switch: filled when on. Turning it on asks first, turning it off doesn't.
                PillButton {
                    objectName: "aiSwitch"
                    visible: ai.available
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

        // Search in the open chat: Enter / ↑ older, Shift+Enter / ↓ newer, Esc closes.
        Rectangle {
            id: chatSearch
            objectName: "chatSearch"
            property string scope: ""
            Layout.fillWidth: true
            implicitHeight: visible ? 48 : 0
            visible: false
            color: Theme.sidebar

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                spacing: 6

                SearchBox {
                    id: chatSearchBox
                    objectName: "chatSearchBox"
                    Layout.fillWidth: true
                    placeholder: qsTr("Search in this chat")
                    onEdited: text => chatSearchTimer.restart()
                    onCleared: root.closeSearch()
                    onSubmitted: backwards => backwards ? messages.searchNewer()
                                                        : messages.searchOlder()
                }
                Timer {
                    id: chatSearchTimer
                    interval: 350
                    onTriggered: messages.searchInChat(chatSearchBox.text)
                }
                Text {
                    objectName: "chatSearchCounter"
                    text: messages.chatSearchBusy && messages.chatSearchCount === 0
                          ? qsTr("Searching\u2026")
                          : messages.chatSearchQuery === "" ? ""
                          : messages.chatSearchCount === 0 ? qsTr("Nothing found")
                          : qsTr("%1 of %2").arg(messages.chatSearchIndex)
                              .arg(messages.chatSearchCount + (messages.chatSearchMore ? "+" : ""))
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                }
                IconButton {
                    iconName: "chevron-down"
                    rotation: 180
                    glyphSize: 15
                    enabled: messages.chatSearchIndex < messages.chatSearchCount
                             || messages.chatSearchMore
                    Accessible.name: qsTr("Older result")
                    onClicked: messages.searchOlder()
                }
                IconButton {
                    iconName: "chevron-down"
                    glyphSize: 15
                    enabled: messages.chatSearchIndex > 1
                    Accessible.name: qsTr("Newer result")
                    onClicked: messages.searchNewer()
                }
                IconButton {
                    iconName: "close"
                    glyphSize: 11
                    Accessible.name: qsTr("Close search")
                    onClicked: root.closeSearch()
                }
            }
            Rectangle {
                anchors.bottom: parent.bottom
                width: parent.width
                height: 1
                color: Theme.separator
            }
        }

        // The pinned message (one of several: a click jumps to it and shows the next older).
        Rectangle {
            id: pinnedBar
            objectName: "pinnedBar"
            Layout.fillWidth: true
            implicitHeight: visible ? 46 : 0
            visible: messages.pinnedCount > 0
            color: pinnedHover.hovered ? Theme.hover : Theme.sidebar

            Column {  // one segment per pinned message (up to 4 shown), the current one lit
                id: pinnedSegments
                x: 16
                anchors.verticalCenter: parent.verticalCenter
                spacing: 2
                readonly property int shown: Math.min(messages.pinnedCount, 4)
                Repeater {
                    model: pinnedSegments.shown
                    Rectangle {
                        required property int index
                        width: 3
                        height: (32 - (pinnedSegments.shown - 1) * 2) / pinnedSegments.shown
                        radius: 1.5
                        color: Theme.accent
                        opacity: (messages.pinnedIndex - 1) % pinnedSegments.shown === index
                                 ? 1 : 0.3
                    }
                }
            }
            Column {
                anchors.left: pinnedSegments.right
                anchors.leftMargin: 10
                anchors.right: pinnedIcon.left
                anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                Text {
                    text: messages.pinnedIndex > 1 ? qsTr("Pinned message #%1").arg(messages.pinnedIndex)
                                                   : qsTr("Pinned message")
                    color: Theme.accent
                    font.pixelSize: Theme.fontSmall
                    font.weight: Font.DemiBold
                }
                Text {
                    width: parent.width
                    text: messages.pinnedText
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    color: Theme.text
                    font.pixelSize: Theme.fontBody
                }
            }
            Icon {
                id: pinnedIcon
                anchors.right: parent.right
                anchors.rightMargin: 16
                anchors.verticalCenter: parent.verticalCenter
                name: "pin"
                size: 18
            }
            HoverHandler {
                id: pinnedHover
                cursorShape: Qt.PointingHandCursor
            }
            TapHandler {
                onTapped: {
                    const id = messages.nextPinned()
                    if (id)
                        root.showMessage(id)
                }
            }
            TapHandler {
                acceptedButtons: Qt.RightButton
                onTapped: pinnedMenu.popup()
            }
            Rectangle {
                anchors.bottom: parent.bottom
                width: parent.width
                height: 1
                color: Theme.separator
            }
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
                    } else if (stickToBottom && messages.atLatest) {
                        positionViewAtBeginning()
                    }
                }

                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: !messages.topicsMode
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
                    selecting: messages.selectionCount > 0
                    onSelectToggled: (id, range) => range ? messages.selectRange(id)
                                                          : messages.toggleSelected(id)
                    onButtonPressed: (id, row, column) => messages.pressButton(id, row, column)
                    onCommentsRequested: id => messages.openComments(id)
                    onReactRequested: (id, button) => {
                        const at = button.mapToItem(Overlay.overlay, 0, 0)
                        reactionPicker.waitingFor = id
                        reactionPicker.anchorPoint = Qt.point(at.x, at.y)
                        messages.requestActions(id)
                    }
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
                // At the bottom of a window that stops short of the newest message, newer pages
                // load as the user scrolls: following the bottom would load them all.
                onMovementEnded: stickToBottom = atYEnd && messages.atLatest

                ScrollBar.vertical: ScrollBar {
                    onPressedChanged: {
                        list.anchorId = 0
                        if (!pressed)
                            list.stickToBottom = list.atYEnd && messages.atLatest
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

            TopicList {
                id: topicList
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: messages.topicsMode
                onTopicPicked: id => messages.openTopic(id)
            }

            Rectangle {
                Layout.fillHeight: true
                implicitWidth: 1
                visible: summaryPanel.visible
                color: Theme.separator
            }

            Rectangle {
                Layout.fillHeight: true
                implicitWidth: 1
                visible: profilePanel.visible
                color: Theme.separator
            }

            ProfilePanel {
                id: profilePanel
                Layout.fillHeight: true
                Layout.preferredWidth: Math.min(340, root.width * 0.4)
                visible: root.profileOpen
                onCloseRequested: root.profileOpen = false
                onMessageRequested: (chatId, messageId) => chatId === messages.chatId
                                    ? root.showMessage(messageId)
                                    : root.openChatRequested(chatId, messageId)
                onChatRequested: chatId => root.openChatRequested(chatId, 0)
                onPersonRequested: userId => messages.openLink("tg://user?id=" + userId)
                onSearchRequested: root.openSearch()
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

        // Instead of the composer while messages are selected.
        Rectangle {
            id: selectionBar
            objectName: "selectionBar"
            Layout.fillWidth: true
            implicitHeight: 56
            visible: messages.selectionCount > 0
            color: Theme.sidebar

            Rectangle {
                width: parent.width
                height: 1
                color: Theme.separator
            }
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                spacing: 8

                IconButton {
                    iconName: "close"
                    glyphSize: 12
                    Accessible.name: qsTr("Cancel selection")
                    onClicked: messages.clearSelection()
                }
                Text {
                    Layout.fillWidth: true
                    text: messages.selectionCount === 1 ? qsTr("1 message selected")
                          : qsTr("%1 messages selected").arg(messages.selectionCount)
                    color: Theme.text
                    font.pixelSize: Theme.fontBody
                    font.weight: Font.DemiBold
                }
                PillButton {
                    iconName: "copy"
                    text: qsTr("Copy")
                    onClicked: messages.copySelected()
                }
                PillButton {
                    iconName: "forward"
                    text: qsTr("Forward")
                    onClicked: forwardDialog.pick(0)
                }
                PillButton {
                    objectName: "deleteSelected"
                    iconName: "trash"
                    text: qsTr("Delete")
                    danger: true
                    onClicked: deleteDialog.ask(0, messages.selectionCanDeleteForAll)
                }
            }
        }

        Shortcut {
            sequence: "Esc"
            enabled: messages.selectionCount > 0
            onActivated: messages.clearSelection()
        }

        // A bot's own keyboard instead of typing (its buttons send their text).
        Rectangle {
            id: replyKeyboard
            objectName: "replyKeyboard"
            Layout.fillWidth: true
            readonly property var rows: messages.replyKeyboard.rows || []
            visible: rows.length > 0 && messages.canWrite && !selectionBar.visible
                     && !messages.topicsMode
            implicitHeight: visible ? keyboardColumn.implicitHeight + 16 : 0
            color: Theme.sidebar
            Rectangle {
                width: parent.width
                height: 1
                color: Theme.separator
            }
            Column {
                id: keyboardColumn
                x: 12
                y: 8
                width: parent.width - 24 - 30
                spacing: 6
                Repeater {
                    model: replyKeyboard.rows
                    Row {
                        id: keyRow
                        required property var modelData
                        spacing: 6
                        Repeater {
                            model: keyRow.modelData
                            PillButton {
                                required property string modelData
                                width: (keyboardColumn.width - (keyRow.modelData.length - 1) * 6)
                                       / keyRow.modelData.length
                                text: modelData
                                onClicked: messages.sendKeyboardButton(modelData)
                            }
                        }
                    }
                }
            }
            IconButton {
                anchors.right: parent.right
                anchors.rightMargin: 8
                anchors.top: parent.top
                anchors.topMargin: 8
                iconName: "close"
                glyphSize: 11
                Accessible.name: qsTr("Hide the bot's keyboard")
                onClicked: messages.hideReplyKeyboard()
            }
        }

        // Opened from a link without being a member: join instead of writing.
        Rectangle {
            objectName: "joinBar"
            Layout.fillWidth: true
            implicitHeight: 56
            visible: messages.canJoin && !messages.topicsMode
            color: Theme.sidebar
            Rectangle {
                width: parent.width
                height: 1
                color: Theme.separator
            }
            PillButton {
                id: joinButton
                objectName: "joinButton"
                anchors.centerIn: parent
                filled: true
                text: messages.chatType === "channel" ? qsTr("Join channel") : qsTr("Join group")
                onClicked: messages.joinChat()
            }
            Text {
                id: joinNotice
                anchors.left: joinButton.right
                anchors.leftMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                visible: false
                text: qsTr("Request sent to the admins")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSmall
            }
        }

        Composer {
            id: composer
            Layout.fillWidth: true
            visible: messages.canWrite && !selectionBar.visible && !messages.topicsMode
                     && !messages.canJoin
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

    // Short notices (a bot's answer to a button, "Copied").
    Rectangle {
        id: toast
        objectName: "toast"
        property alias text: toastText.text
        function show(message) {
            toastText.text = message
            toastTimer.restart()
        }
        x: (list.width - width) / 2  // the feed starts at the left edge
        anchors.bottom: parent.bottom
        anchors.bottomMargin: composer.height + 70
        width: Math.min(toastText.implicitWidth + 32, list.width - 48)
        height: toastText.implicitHeight + 16
        radius: 10
        color: Theme.popup
        border.width: 1
        border.color: Theme.popupBorder
        visible: opacity > 0
        opacity: toastTimer.running ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 160 } }
        Text {
            id: toastText
            anchors.centerIn: parent
            width: parent.width - 32
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            color: Theme.text
            font.pixelSize: Theme.fontBody
        }
        Timer {
            id: toastTimer
            interval: 3000
        }
    }

    ConfirmDialog {
        id: botAlert
        objectName: "botAlert"
        danger: false
        canCancel: false
    }

    // Jump buttons over the feed's bottom-right corner: unread reactions, mentions, newest.
    Column {
        id: jumpButtons
        x: list.width - width - 18
        anchors.bottom: parent.bottom
        anchors.bottomMargin: composer.height + 14
        spacing: 16  // room for the counters on top

        JumpButton {
            objectName: "reactionJump"
            visible: messages.reactionCount > 0
            iconName: "heart"
            count: messages.reactionCount
            accessibleName: qsTr("Next unread reaction")
            onClicked: messages.nextReaction()
            onMenuRequested: jumpMenu.openFor("reactions")
        }
        JumpButton {
            objectName: "mentionJump"
            visible: messages.mentionCount > 0
            glyph: "@"
            count: messages.mentionCount
            accessibleName: qsTr("Next mention")
            onClicked: messages.nextMention()
            onMenuRequested: jumpMenu.openFor("mentions")
        }
        JumpButton {  // back to the newest message
            objectName: "latestJump"
            visible: (!list.atYEnd || !messages.atLatest) && list.count > 0
            iconName: "arrow-down"
            count: messages.unreadCount
            accessibleName: qsTr("Scroll to latest")
            onClicked: {
                list.anchorId = 0
                list.stickToBottom = true
                if (messages.atLatest)
                    list.positionViewAtBeginning()
                else
                    messages.jumpToLatest()
            }
        }
    }

    AppMenu {
        id: jumpMenu
        objectName: "jumpMenu"
        property string kind: ""
        function openFor(what) {
            kind = what
            popup()
        }
        AppMenuItem {
            text: jumpMenu.kind === "mentions" ? qsTr("Mark all mentions as read")
                                               : qsTr("Mark all reactions as read")
            iconName: "check"
            onTriggered: jumpMenu.kind === "mentions" ? messages.readAllMentions()
                                                      : messages.readAllReactions()
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
            objectName: "pinItem"
            text: messageMenu.actions.isPinned ? qsTr("Unpin") : qsTr("Pin")
            iconName: "pin"
            visible: messageMenu.actions.canPin === true
            onTriggered: messageMenu.actions.isPinned
                         ? messages.unpinMessage(messageMenu.messageId)
                         : pinDialog.ask(messageMenu.messageId)
        }
        AppMenuItem {
            text: qsTr("Copy link")
            iconName: "link"
            visible: messageMenu.actions.canCopyLink === true
            onTriggered: messages.copyLink(messageMenu.messageId)
        }
        AppMenuItem {
            objectName: "selectItem"
            text: qsTr("Select")
            iconName: "check"
            hint: Qt.platform.os === "osx" ? qsTr("\u2318-click") : qsTr("Ctrl+click")
            onTriggered: messages.toggleSelected(messageMenu.messageId)
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

    AppMenu {
        id: pinnedMenu
        objectName: "pinnedMenu"
        AppMenuItem {
            text: qsTr("Go to message")
            iconName: "open"
            onTriggered: root.showMessage(messages.pinnedId)
        }
        AppMenuItem {
            text: qsTr("Unpin")
            iconName: "pin"
            onTriggered: messages.unpinMessage(messages.pinnedId)
        }
        AppMenuItem {
            visible: messages.pinnedCount > 1
            text: qsTr("Unpin all messages")
            iconName: "close"
            danger: true
            onTriggered: messages.unpinAll()
        }
    }

    // An invite link to a chat the user isn't in: what it is, and Join.
    Popup {
        id: inviteDialog
        objectName: "inviteDialog"
        property var info: ({})

        function show(info) {
            inviteDialog.info = info
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
            spacing: 10
            Avatar {
                Layout.alignment: Qt.AlignHCenter
                size: 64
                initials: (inviteDialog.info.title || "?").slice(0, 1).toUpperCase()
                colorIndex: (inviteDialog.info.title || "").length % 7
            }
            Text {
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
                text: inviteDialog.info.title || ""
                textFormat: Text.PlainText
                color: Theme.text
                font.pixelSize: 16
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
                readonly property int count: inviteDialog.info.members || 0
                text: inviteDialog.info.channel
                      ? (count === 1 ? qsTr("1 subscriber") : qsTr("%1 subscribers").arg(count))
                      : (count === 1 ? qsTr("1 member") : qsTr("%1 members").arg(count))
                color: Theme.textMuted
                font.pixelSize: Theme.fontSmall
            }
            Text {
                Layout.fillWidth: true
                visible: text !== ""
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
                maximumLineCount: 6
                elide: Text.ElideRight
                text: inviteDialog.info.description || ""
                textFormat: Text.PlainText
                color: Theme.text
                font.pixelSize: Theme.fontBody
            }
            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: 6
                spacing: 8
                Item { Layout.fillWidth: true }
                PillButton {
                    text: qsTr("Cancel")
                    onClicked: inviteDialog.close()
                }
                PillButton {
                    objectName: "inviteJoin"
                    filled: true
                    text: inviteDialog.info.request ? qsTr("Request to join")
                          : inviteDialog.info.channel ? qsTr("Join channel") : qsTr("Join group")
                    onClicked: {
                        messages.joinByInvite(inviteDialog.info.link)
                        inviteDialog.close()
                    }
                }
            }
        }
    }

    Popup {
        id: pinDialog
        objectName: "pinDialog"
        property var messageId: 0

        function ask(messageId) {
            pinDialog.messageId = messageId
            notifySwitch.checked = false
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
                text: qsTr("Pin this message?")
                color: Theme.text
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                ToggleSwitch { id: notifySwitch }
                Text {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: messages.chatType === "private"
                          ? qsTr("Also notify %1").arg(messages.chatTitle)
                          : qsTr("Notify all members")
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
                    onClicked: pinDialog.close()
                }
                PillButton {
                    objectName: "pinConfirm"
                    text: qsTr("Pin")
                    filled: true
                    onClicked: {
                        messages.pinMessage(pinDialog.messageId, notifySwitch.checked)
                        pinDialog.close()
                    }
                }
            }
        }
    }

    ReactionPicker {
        id: reactionPicker
        property var waitingFor: 0       // hover button clicked: opens when actions arrive
        property point anchorPoint: Qt.point(0, 0)
        parent: Overlay.overlay
        onPicked: (messageId, key) => messages.toggleReaction(messageId, key)
    }

    ForwardDialog {
        id: forwardDialog
        onChatPicked: chatId => {
            if (forwardDialog.messageId === 0)  // the selection
                messages.forwardSelected(chatId)
            else
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
                text: deleteDialog.messageId !== 0 || messages.selectionCount === 1
                      ? qsTr("Delete this message?")
                      : qsTr("Delete %1 messages?").arg(messages.selectionCount)
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
                        const revoke = deleteDialog.canRevoke && revokeSwitch.checked
                        if (deleteDialog.messageId === 0)  // the selection
                            messages.deleteSelected(revoke)
                        else
                            messages.deleteMessage(deleteDialog.messageId, revoke)
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
            objectName: "viewProfileItem"
            text: qsTr("View profile")
            iconName: "person"
            visible: personMenu.senderKey.startsWith("user:")
            onTriggered: root.showProfile(0, Number(personMenu.senderKey.slice(5)))
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
