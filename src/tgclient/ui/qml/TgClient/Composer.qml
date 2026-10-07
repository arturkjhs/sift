import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts

Rectangle {
    id: root
    property var replyToId: 0
    // Depends on messages.loading so a draft's quote fills in once its message is loaded.
    readonly property var replyPreview: (messages.loading, replyToId ? messages.replyPreview(replyToId) : ({}))

    // Editing a sent message: the input temporarily holds its text; the draft is kept aside.
    property var editingId: 0
    property string editingPreview: ""
    property string stashedText: ""
    property bool draftSuspended: false   // true while a chat's draft is being loaded
    // Translation of the input, shown for confirmation before anything is sent.
    property string translatedText: ""
    property string translatedLang: ""

    signal cancelReply()
    signal sent()

    function send() {
        if (root.editingId !== 0) {
            messages.saveEdit(root.editingId, input.text)
            root.finishEdit()
            return
        }
        if (input.text.trim().length === 0)
            return
        messages.sendMessage(input.text, root.replyToId, composerModel.sendOptions())
        input.clear()
        composerModel.sent()
        root.sent()
    }

    function focusInput() { input.forceActiveFocus() }

    // A suggested reply: replaces the input, the user edits and sends it themselves.
    function useText(text) {
        if (root.editingId !== 0)
            root.finishEdit()
        input.text = text
        input.cursorPosition = input.length
        input.forceActiveFocus()
    }

    function insertText(text) {
        input.insert(input.cursorPosition, text)
    }

    // "@…" right before the cursor: suggest members; picking one replaces the "@…".
    readonly property var mentionMatch: {
        const before = input.text.slice(0, input.cursorPosition)
        return /(^|\s)@([\w.]*)$/.exec(before)
    }
    onMentionMatchChanged: {
        if (mentionMatch && root.editingId === 0)
            composerModel.findMentions(mentionMatch[2])
        else
            composerModel.stopMentions()
        mentionList.currentIndex = 0
    }

    function insertMention(row) {
        const match = root.mentionMatch
        if (!match)
            return
        const start = input.cursorPosition - match[2].length - 1
        input.remove(start, input.cursorPosition)
        input.insert(start, row.insert)
        composerModel.stopMentions()
    }

    // A chat was opened, or its draft changed on another device. Not a user edit: don't save
    // it back or tell the chat we're typing.
    function loadDraft(text) {
        root.editingId = 0
        root.stashedText = ""
        root.translatedText = ""
        ai.clearAssistError()
        if (input.text !== text) {
            input.text = text
            input.cursorPosition = input.length
        }
    }

    function beginEdit(messageId, text) {
        if (root.editingId === 0)
            root.stashedText = input.text
        root.editingPreview = messages.replyPreview(messageId).text || ""
        root.editingId = messageId
        input.text = text
        input.cursorPosition = input.length
        input.forceActiveFocus()
    }

    function finishEdit() {
        if (root.editingId === 0)
            return
        root.editingId = 0
        input.text = root.stashedText
        input.cursorPosition = input.length
        root.stashedText = ""
    }

    function saveDraft() {
        if (root.editingId === 0 && !root.draftSuspended)
            composerModel.setDraft(input.text, root.replyToId)
    }

    onReplyToIdChanged: saveDraft()

    color: Theme.sidebar
    implicitHeight: layout.implicitHeight + 16

    Connections {
        target: messages
        function onEditReady(messageId, text) { root.beginEdit(messageId, text) }
    }

    Connections {
        target: ai
        function onReplySuggested(text) {
            input.text = text
            input.cursorPosition = input.length
            input.forceActiveFocus()
        }
        function onDraftTranslated(text, lang) {
            root.translatedText = text
            root.translatedLang = lang
        }
    }

    function sendTranslation() {
        messages.sendMessage(root.translatedText, root.replyToId, composerModel.sendOptions())
        root.translatedText = ""
        input.clear()
        composerModel.sent()
        root.sent()
    }

    function languageName(code) {
        const match = ai.languages.filter(l => l.code === code)
        return match.length ? match[0].label : code
    }

    AppMenu {
        id: assistMenu
        objectName: "assistMenu"
        AppMenuItem {
            text: qsTr("Suggest a reply")
            iconName: "sparkle"
            onTriggered: ai.suggestReply("neutral", root.replyToId)
        }
        AppMenuItem {
            text: qsTr("Suggest: friendly")
            iconName: "sparkle"
            onTriggered: ai.suggestReply("friendly", root.replyToId)
        }
        AppMenuItem {
            text: qsTr("Suggest: formal")
            iconName: "sparkle"
            onTriggered: ai.suggestReply("formal", root.replyToId)
        }
        AppMenuItem {
            text: qsTr("Suggest: brief")
            iconName: "sparkle"
            onTriggered: ai.suggestReply("brief", root.replyToId)
        }
        AppMenuSeparator {}
        AppMenuItem {
            text: qsTr("Translate to English")
            iconName: "translate"
            enabled: input.text.trim() !== ""
            onTriggered: ai.translateDraft(input.text, "en")
        }
        AppMenuItem {
            text: qsTr("Translate to Russian")
            iconName: "translate"
            enabled: input.text.trim() !== ""
            onTriggered: ai.translateDraft(input.text, "ru")
        }
        AppMenuItem {
            text: qsTr("Translate to Ukrainian")
            iconName: "translate"
            enabled: input.text.trim() !== ""
            onTriggered: ai.translateDraft(input.text, "uk")
        }
        AppMenuItem {
            text: qsTr("Translate to Czech")
            iconName: "translate"
            enabled: input.text.trim() !== ""
            onTriggered: ai.translateDraft(input.text, "cs")
        }
    }

    FileDialog {
        id: fileDialog
        title: qsTr("Send files")
        fileMode: FileDialog.OpenFiles
        onAccepted: composerModel.stage(selectedFiles)
    }

    ColumnLayout {
        id: layout
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: 12
        anchors.rightMargin: 12
        spacing: 8

        // Recording a voice message: replaces the input row.
        RowLayout {
            objectName: "recordingBar"
            Layout.fillWidth: true
            visible: recorder.busy
            spacing: 10

            Rectangle {
                width: 10
                height: 10
                radius: 5
                color: Theme.danger
                SequentialAnimation on opacity {
                    running: recorder.recording
                    loops: Animation.Infinite
                    NumberAnimation { to: 0.3; duration: 600 }
                    NumberAnimation { to: 1; duration: 600 }
                }
            }
            Text {
                text: recorder.recording
                      ? Math.floor(recorder.seconds / 60) + ":"
                        + (recorder.seconds % 60 < 10 ? "0" : "") + recorder.seconds % 60
                      : qsTr("Sending\u2026")
                color: Theme.text
                font.pixelSize: Theme.fontTitle
                font.features: { "tnum": 1 }
            }
            Rectangle {  // input level
                Layout.fillWidth: true
                height: 4
                radius: 2
                color: Theme.separator
                Rectangle {
                    width: parent.width * Math.min(1, recorder.level * 1.6)
                    height: parent.height
                    radius: 2
                    color: Theme.accent
                    Behavior on width { NumberAnimation { duration: 90 } }
                }
            }
            PillButton {
                text: qsTr("Cancel")
                enabled: recorder.recording
                onClicked: recorder.cancel()
            }
            PillButton {
                objectName: "sendVoice"
                text: qsTr("Send")
                filled: true
                enabled: recorder.recording
                onClicked: {
                    recorder.finish()
                    root.sent()
                }
            }
        }

        Text {
            id: recorderError
            Layout.fillWidth: true
            visible: text !== ""
            wrapMode: Text.Wrap
            color: Theme.danger
            font.pixelSize: Theme.fontSmall
            Connections {
                target: recorder
                function onError(message) { recorderError.text = message }
            }
            Timer {
                running: recorderError.text !== ""
                interval: 6000
                onTriggered: recorderError.text = ""
            }
        }

        // AI: working, an error, or a translation waiting for confirmation.
        RowLayout {
            Layout.fillWidth: true
            visible: ai.assistBusy || ai.assistError !== ""
            spacing: 8
            Text {
                Layout.fillWidth: true
                text: ai.assistBusy ? qsTr("Thinking\u2026") : ai.assistError
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                color: ai.assistBusy ? Theme.accent : Theme.danger
                font.pixelSize: Theme.fontSmall
            }
            IconButton {
                visible: !ai.assistBusy
                iconName: "close"
                glyphSize: 11
                Accessible.name: qsTr("Dismiss")
                onClicked: ai.clearAssistError()
            }
        }

        RowLayout {
            objectName: "translationPreview"
            Layout.fillWidth: true
            visible: root.translatedText !== ""
            spacing: 10

            Icon {
                Layout.alignment: Qt.AlignTop
                name: "translate"
                color: Theme.accent
                size: 18
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text {
                    text: qsTr("Translation to %1").arg(root.languageName(root.translatedLang))
                    color: Theme.accent
                    font.pixelSize: Theme.fontSmall
                    font.weight: Font.DemiBold
                }
                TextEdit {
                    Layout.fillWidth: true
                    Layout.maximumHeight: 120
                    text: root.translatedText
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextEdit.Wrap
                    textFormat: TextEdit.PlainText
                    clip: true
                    color: Theme.text
                    selectionColor: Theme.accent
                    selectedTextColor: Theme.textOnAccent
                    font.pixelSize: Theme.fontBody
                }
            }
            PillButton {
                Layout.alignment: Qt.AlignTop
                text: qsTr("Edit")
                onClicked: {
                    input.text = root.translatedText
                    input.cursorPosition = input.length
                    root.translatedText = ""
                    input.forceActiveFocus()
                }
            }
            PillButton {
                objectName: "sendTranslation"
                Layout.alignment: Qt.AlignTop
                text: qsTr("Send")
                filled: true
                onClicked: root.sendTranslation()
            }
            IconButton {
                Layout.alignment: Qt.AlignTop
                iconName: "close"
                glyphSize: 11
                Accessible.name: qsTr("Discard the translation")
                onClicked: root.translatedText = ""
            }
        }

        // Preview of the link in the input; removing it sends the message without one.
        RowLayout {
            objectName: "composerLinkPreview"
            Layout.fillWidth: true
            visible: root.editingId === 0 && (composerModel.linkPreview.url || "") !== ""
            spacing: 10

            Icon {
                Layout.alignment: Qt.AlignVCenter
                name: "link"
                color: Theme.accent
                size: 18
            }
            Rectangle {
                Layout.preferredWidth: 3
                Layout.fillHeight: true
                radius: 1.5
                color: Theme.replyBar
            }
            Image {
                Layout.preferredWidth: 36
                Layout.preferredHeight: 36
                visible: (composerModel.linkPreview.image || "") !== ""
                source: composerModel.linkPreview.image || ""
                sourceSize.width: 72
                sourceSize.height: 72
                fillMode: Image.PreserveAspectCrop
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1
                Text {
                    Layout.fillWidth: true
                    text: composerModel.linkPreview.site || composerModel.linkPreview.title || ""
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    color: Theme.accent
                    font.pixelSize: Theme.fontSmall
                    font.weight: Font.DemiBold
                }
                Text {
                    Layout.fillWidth: true
                    text: composerModel.linkPreview.site ? (composerModel.linkPreview.title
                                                             || composerModel.linkPreview.text || "")
                                                          : (composerModel.linkPreview.text || "")
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                }
            }
            IconButton {
                objectName: "removeLinkPreview"
                iconName: "close"
                glyphSize: 11
                Accessible.name: qsTr("Send without the link preview")
                onClicked: composerModel.removeLinkPreview()
            }
        }

        // Reply or edit context above the input.
        RowLayout {
            Layout.fillWidth: true
            visible: root.editingId !== 0 || root.replyToId !== 0
            spacing: 10

            Icon {
                Layout.alignment: Qt.AlignVCenter
                name: root.editingId !== 0 ? "edit" : "reply"
                color: Theme.accent
                size: 18
            }

            Rectangle {
                Layout.preferredWidth: 3
                Layout.fillHeight: true
                radius: 1.5
                color: Theme.replyBar
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1

                Text {
                    Layout.fillWidth: true
                    text: root.editingId !== 0 ? qsTr("Edit message")
                                               : qsTr("Reply to %1").arg(root.replyPreview.sender || "")
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    color: Theme.accent
                    font.pixelSize: Theme.fontSmall
                    font.weight: Font.DemiBold
                }
                Text {
                    Layout.fillWidth: true
                    text: root.editingId !== 0 ? root.editingPreview : (root.replyPreview.text || "")
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                }
            }

            IconButton {
                iconName: "close"
                glyphSize: 11
                Accessible.name: root.editingId !== 0 ? qsTr("Cancel editing") : qsTr("Cancel reply")
                onClicked: root.editingId !== 0 ? root.finishEdit() : root.cancelReply()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            visible: !recorder.busy
            spacing: 8

            IconButton {
                id: attachButton
                objectName: "attachButton"
                Layout.alignment: Qt.AlignBottom
                Layout.bottomMargin: 5
                visible: root.editingId === 0
                iconName: "attach"
                glyphSize: 19
                Accessible.name: qsTr("Attach files")
                onClicked: fileDialog.open()
            }

            ScrollView {
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(input.implicitHeight, 160)

                TextArea {
                    id: input
                    objectName: "composerInput"
                    placeholderText: messages.replyKeyboard.placeholder || qsTr("Write a message")
                    wrapMode: TextArea.Wrap
                    color: Theme.text
                    placeholderTextColor: Theme.textMuted
                    font.pixelSize: Theme.fontTitle
                    padding: 10
                    background: Rectangle {
                        radius: 10
                        color: Theme.field
                        border.width: 1
                        border.color: input.activeFocus ? Theme.accent : Theme.fieldBorder
                    }
                    onTextChanged: root.saveDraft()
                    // Enter sends, Shift+Enter inserts a new line; Cmd/Ctrl+V with an image or
                    // copied files attaches them; Up in an empty input edits the last message.
                    Keys.onPressed: event => {
                        if (mentionPopup.visible && (event.key === Qt.Key_Down
                                                     || event.key === Qt.Key_Up)) {
                            mentionList.currentIndex = (mentionList.currentIndex
                                + (event.key === Qt.Key_Down ? 1 : -1) + mentionList.count)
                                % mentionList.count
                            event.accepted = true
                        } else if (mentionPopup.visible && (event.key === Qt.Key_Return
                                       || event.key === Qt.Key_Enter || event.key === Qt.Key_Tab)) {
                            root.insertMention(composerModel.mentions[mentionList.currentIndex])
                            event.accepted = true
                        } else if (mentionPopup.visible && event.key === Qt.Key_Escape) {
                            composerModel.stopMentions()
                            event.accepted = true
                        } else if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                                && !(event.modifiers & Qt.ShiftModifier)) {
                            root.send()
                            event.accepted = true
                        } else if (event.matches(StandardKey.Paste)) {
                            event.accepted = composerModel.pasteClipboard()
                        } else if (event.key === Qt.Key_Escape && root.editingId !== 0) {
                            root.finishEdit()
                            event.accepted = true
                        } else if (event.key === Qt.Key_Escape && root.replyToId !== 0) {
                            root.cancelReply()
                            event.accepted = true
                        } else if (event.key === Qt.Key_Up && input.length === 0
                                   && root.editingId === 0) {
                            const id = messages.lastEditableId()
                            if (id) {
                                messages.startEdit(id)
                                event.accepted = true
                            }
                        }
                    }
                }
            }

            IconButton {
                id: assistButton
                objectName: "assistButton"
                Layout.alignment: Qt.AlignBottom
                Layout.bottomMargin: 5
                visible: ai.enabled && root.editingId === 0
                enabled: !ai.assistBusy
                iconName: "sparkle"
                glyphSize: 18
                Accessible.name: qsTr("AI: suggest a reply, translate")
                onClicked: assistMenu.popup(assistButton, 0, -assistMenu.implicitHeight - 6)
            }

            IconButton {
                id: emojiButton
                objectName: "emojiButton"
                Layout.alignment: Qt.AlignBottom
                Layout.bottomMargin: 5
                iconName: "emoji"
                glyphSize: 19
                Accessible.name: qsTr("Emoji and stickers")
                onClicked: picker.opened ? picker.close() : picker.open()

                EmojiStickerPicker {
                    id: picker
                    x: emojiButton.width - width + 8
                    y: -height - 14
                    onEmojiPicked: emoji => root.insertText(emoji)
                    onStickerPicked: sticker => {
                        messages.sendSticker(sticker, root.replyToId)
                        picker.close()
                        root.sent()
                    }
                    onClosed: input.forceActiveFocus()
                }
            }

            // Empty input: the microphone instead of Send.
            IconButton {
                id: micButton
                objectName: "micButton"
                Layout.alignment: Qt.AlignBottom
                Layout.bottomMargin: 5
                visible: input.text.trim().length === 0 && root.editingId === 0
                iconName: "mic"
                glyphSize: 19
                Accessible.name: qsTr("Record a voice message")
                onClicked: recorder.start(messages.chatId, root.replyToId)
            }

            Button {
                id: sendButton
                Layout.alignment: Qt.AlignBottom
                visible: !micButton.visible
                enabled: input.text.trim().length > 0 || root.editingId !== 0
                text: root.editingId !== 0 ? qsTr("Save") : qsTr("Send")
                padding: 10
                contentItem: Text {
                    text: sendButton.text
                    color: Theme.textOnAccent
                    font.pixelSize: Theme.fontTitle
                    font.weight: Font.DemiBold
                }
                background: Rectangle {
                    radius: 10
                    color: Theme.accent
                    opacity: sendButton.enabled ? (sendButton.down ? 0.85 : 1) : 0.4
                }
                onClicked: root.send()
            }
        }
    }

    SendFilesDialog {
        replyToId: root.replyToId
        onSent: root.sent()
    }

    // Members for the "@…" being typed, just above the input.
    Rectangle {
        id: mentionPopup
        objectName: "mentionPopup"
        visible: composerModel.mentions.length > 0 && root.mentionMatch !== null
        x: 52
        y: -height - 6
        z: 10
        width: Math.min(320, root.width - 64)
        height: Math.min(mentionList.contentHeight, 5 * 44) + 8
        radius: 10
        color: Theme.popup
        border.width: 1
        border.color: Theme.popupBorder

        ListView {
            id: mentionList
            anchors.fill: parent
            anchors.margins: 4
            clip: true
            model: composerModel.mentions
            currentIndex: 0
            boundsBehavior: Flickable.StopAtBounds
            delegate: Rectangle {
                id: mentionRow
                required property var modelData
                required property int index
                width: ListView.view.width
                height: 44
                radius: 6
                color: index === mentionList.currentIndex || mentionHover.hovered ? Theme.hover
                                                                                  : "transparent"
                HoverHandler { id: mentionHover }
                TapHandler { onTapped: root.insertMention(mentionRow.modelData) }
                Avatar {
                    x: 8
                    anchors.verticalCenter: parent.verticalCenter
                    size: 30
                    avatarSource: mentionRow.modelData.avatar
                    initials: mentionRow.modelData.initials
                    colorIndex: mentionRow.modelData.colorIndex
                }
                Column {
                    x: 48
                    width: parent.width - 56
                    anchors.verticalCenter: parent.verticalCenter
                    Text {
                        width: parent.width
                        text: mentionRow.modelData.name
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        color: Theme.text
                        font.pixelSize: Theme.fontBody
                        font.weight: Font.DemiBold
                    }
                    Text {
                        width: parent.width
                        visible: text !== ""
                        text: mentionRow.modelData.username ? "@" + mentionRow.modelData.username
                                                            : ""
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSmall
                    }
                }
            }
        }
    }
}
