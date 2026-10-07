import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// One person's messages in the open chat (or, later, in all chats in common): find and check
// what exactly they wrote. A click jumps to the message.
Rectangle {
    id: root
    objectName: "personMessagesPanel"
    color: Theme.sidebar

    signal closeRequested()
    signal messageRequested(var chatId, var messageId)

    function focusSearch() { searchBox.focusInput() }

    Binding {
        target: personMessages
        property: "highlightColor"
        value: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.3).toString()
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            Text {
                Layout.fillWidth: true
                text: personMessages.onBehalf
                      ? qsTr("Messages on behalf of %1").arg(personMessages.senderName)
                      : qsTr("Messages from %1").arg(personMessages.senderName)
                textFormat: Text.PlainText
                elide: Text.ElideRight
                color: Theme.text
                font.pixelSize: Theme.fontTitle
                font.weight: Font.DemiBold
            }
            IconButton {
                iconName: "close"
                glyphSize: 12
                Accessible.name: qsTr("Close")
                onClicked: root.closeRequested()
            }
        }

        SearchBox {
            id: searchBox
            objectName: "personSearchBox"
            Layout.fillWidth: true
            placeholder: qsTr("Search their messages")
            onEdited: text => personMessages.query = text
        }

        Text {
            Layout.fillWidth: true
            visible: text !== ""
            text: personMessages.busy && list.count === 0 ? qsTr("Searching…")
                  : list.count === 0 ? qsTr("Nothing found")
                  : ""
            color: Theme.textMuted
            font.pixelSize: Theme.fontSmall
        }

        ListView {
            id: list
            objectName: "personMessageList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: personMessages
            spacing: 6
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}

            delegate: Rectangle {
                id: row
                required property string kind
                required property var chatId
                required property var messageId
                required property string chatTitle
                required property string topic
                required property string time
                required property string text
                required property string media

                width: ListView.view.width
                height: column.implicitHeight + 16
                radius: 8
                color: hover.hovered ? Theme.hover : Theme.window

                HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: root.messageRequested(row.chatId, row.messageId) }

                Column {
                    id: column
                    x: 10
                    y: 8
                    width: parent.width - 20
                    spacing: 3
                    Row {
                        width: parent.width
                        spacing: 6
                        Text {
                            text: row.time
                            color: Theme.accent
                            font.pixelSize: Theme.fontSmall
                            font.weight: Font.DemiBold
                        }
                        Text {
                            width: parent.width - x
                            visible: text !== ""
                            text: row.topic !== "" ? "# " + row.topic : ""
                            textFormat: Text.PlainText
                            elide: Text.ElideRight
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSmall
                        }
                    }
                    Text {
                        visible: row.media !== ""
                        text: row.media
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSmall
                        font.italic: true
                    }
                    Text {
                        width: parent.width
                        visible: row.text !== ""
                        text: row.text
                        textFormat: Text.RichText
                        wrapMode: Text.Wrap
                        maximumLineCount: 8
                        elide: Text.ElideRight
                        color: Theme.text
                        font.pixelSize: Theme.fontBody
                    }
                }
            }

            footer: Item {
                width: ListView.view ? ListView.view.width : 0
                height: personMessages.busy && list.count > 0 ? 30 : 0
                Text {
                    anchors.centerIn: parent
                    visible: parent.height > 0
                    text: qsTr("Loading…")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                }
            }
        }
    }
}
