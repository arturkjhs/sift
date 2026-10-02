import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts

Rectangle {
    id: root
    property var replyToId: 0
    readonly property var replyPreview: replyToId ? messages.replyPreview(replyToId) : ({})

    signal cancelReply()
    signal sent()

    function send() {
        if (input.text.trim().length === 0)
            return
        messages.send(input.text, root.replyToId)
        input.clear()
        root.sent()
    }

    function focusInput() { input.forceActiveFocus() }

    function insertText(text) {
        input.insert(input.cursorPosition, text)
    }

    color: Theme.sidebar
    implicitHeight: layout.implicitHeight + 16

    FileDialog {
        id: fileDialog
        title: qsTr("Send files")
        fileMode: FileDialog.OpenFiles
        onAccepted: messages.sendFiles(selectedFiles)
    }

    ColumnLayout {
        id: layout
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: 12
        anchors.rightMargin: 12
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            visible: root.replyToId !== 0
            spacing: 10

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
                    text: qsTr("Reply to %1").arg(root.replyPreview.sender || "")
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    color: Theme.accent
                    font.pixelSize: Theme.fontSmall
                    font.weight: Font.DemiBold
                }
                Text {
                    Layout.fillWidth: true
                    text: root.replyPreview.text || ""
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                }
            }

            ToolButton {
                text: "\u2715"
                onClicked: root.cancelReply()
                Accessible.name: qsTr("Cancel reply")
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            ToolButton {
                id: attachButton
                Layout.alignment: Qt.AlignBottom
                text: "+"
                font.pixelSize: 22
                Accessible.name: qsTr("Attach files")
                onClicked: fileDialog.open()
                contentItem: Text {
                    text: attachButton.text
                    color: attachButton.hovered ? Theme.accent : Theme.textMuted
                    font: attachButton.font
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Item { implicitWidth: 36; implicitHeight: 40 }
            }

            ScrollView {
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(input.implicitHeight, 160)

                TextArea {
                    id: input
                    objectName: "composerInput"
                    placeholderText: qsTr("Write a message")
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
                    // Enter sends, Shift+Enter inserts a new line.
                    Keys.onPressed: event => {
                        if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                                && !(event.modifiers & Qt.ShiftModifier)) {
                            root.send()
                            event.accepted = true
                        }
                    }
                }
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

            Button {
                id: sendButton
                Layout.alignment: Qt.AlignBottom
                enabled: input.text.trim().length > 0
                text: qsTr("Send")
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
}
