import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

SplitView {
    id: root
    objectName: "mainView"

    property var selectedChatId: 0       // int53, keep as var

    handle: Rectangle {
        implicitWidth: 1
        color: Theme.separator
    }

    Rectangle {
        SplitView.preferredWidth: 340
        SplitView.minimumWidth: 260
        SplitView.maximumWidth: 520
        color: Theme.sidebar

        ColumnLayout {
            anchors.fill: parent
            spacing: 0

            FolderTabs {
                Layout.fillWidth: true
                currentKey: chatList.listKey
                onSelected: key => {
                    chatList.setList(key)
                    list.positionViewAtBeginning()
                }
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

    Rectangle {
        SplitView.fillWidth: true
        color: Theme.window

        MessageView {
            anchors.fill: parent
            visible: root.selectedChatId !== 0
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
