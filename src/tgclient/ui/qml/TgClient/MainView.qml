import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

SplitView {
    id: root
    objectName: "mainView"

    property var selectedChatId: 0       // int53, keep as var

    readonly property bool searching: search.query.trim() !== ""

    signal settingsRequested()

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

        Shortcut {
            sequences: [StandardKey.Find]
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

                SearchBox {
                    id: searchField
                    Layout.fillWidth: true
                    placeholder: qsTr("Search chats and messages")
                    onEdited: text => search.query = text
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
            id: messageView
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
