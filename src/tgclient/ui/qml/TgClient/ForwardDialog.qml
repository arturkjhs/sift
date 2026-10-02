import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Pick a chat to forward a message to.
Popup {
    id: root
    objectName: "forwardDialog"
    property var messageId: 0

    signal chatPicked(var chatId)

    function pick(messageId) {
        root.messageId = messageId
        chatPicker.filter = ""
        searchBox.clear()
        chatPicker.refresh()
        open()
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(400, parent.width - 48)
    height: Math.min(520, parent.height - 48)
    modal: true
    padding: 0
    onOpened: searchBox.focusInput()

    background: Rectangle {
        radius: 12
        color: Theme.sidebar
        border.width: 1
        border.color: Theme.separator
    }

    contentItem: ColumnLayout {
        spacing: 0

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: 16
            Layout.bottomMargin: 8
            Text {
                Layout.fillWidth: true
                text: qsTr("Forward to…")
                color: Theme.text
                font.pixelSize: 16
                font.weight: Font.DemiBold
            }
            IconButton {
                iconName: "close"
                glyphSize: 12
                Accessible.name: qsTr("Close")
                onClicked: root.close()
            }
        }

        SearchBox {
            id: searchBox
            Layout.fillWidth: true
            Layout.leftMargin: 16
            Layout.rightMargin: 16
            Layout.bottomMargin: 8
            placeholder: qsTr("Search chats")
            onEdited: text => chatPicker.filter = text
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 1
            color: Theme.separator
        }

        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            model: chatPicker
            ScrollBar.vertical: ScrollBar {}

            delegate: AbstractButton {
                id: row
                required property var chatId
                required property string title
                required property string avatarSource
                required property string initials
                required property int colorIndex

                width: ListView.view.width
                height: 52
                hoverEnabled: true
                onClicked: {
                    root.chatPicked(row.chatId)
                    root.close()
                }
                background: Rectangle { color: row.hovered ? Theme.hover : "transparent" }
                contentItem: Item {
                    Avatar {
                        id: avatar
                        x: 16
                        anchors.verticalCenter: parent.verticalCenter
                        size: 36
                        avatarSource: row.avatarSource
                        initials: row.initials
                        colorIndex: row.colorIndex
                    }
                    Text {
                        anchors.left: avatar.right
                        anchors.leftMargin: 12
                        anchors.right: parent.right
                        anchors.rightMargin: 16
                        anchors.verticalCenter: parent.verticalCenter
                        text: row.title
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        color: Theme.text
                        font.pixelSize: Theme.fontTitle
                    }
                }
            }

            Text {
                anchors.centerIn: parent
                visible: list.count === 0
                text: qsTr("No chats found")
                color: Theme.textMuted
                font.pixelSize: Theme.fontBody
            }
        }
    }
}
