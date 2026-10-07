import QtQuick
import QtQuick.Controls.Basic

Item {
    id: root
    property string currentKey: "main"
    signal selected(string key)
    signal editRequested(string key)   // "" for a new folder

    AppMenu {
        id: tabMenu
        objectName: "folderMenu"
        property string key: ""
        AppMenuItem {
            visible: tabMenu.key.startsWith("folder:")
            text: qsTr("Edit folder")
            iconName: "edit"
            onTriggered: root.editRequested(tabMenu.key)
        }
        AppMenuItem {
            text: qsTr("Move left")
            iconName: "back"
            onTriggered: folderEditor.move(tabMenu.key, -1)
        }
        AppMenuItem {
            text: qsTr("Move right")
            iconName: "forward"
            onTriggered: folderEditor.move(tabMenu.key, 1)
        }
        AppMenuItem {
            text: qsTr("New folder")
            iconName: "folder"
            onTriggered: root.editRequested("")
        }
        AppMenuSeparator { visible: tabMenu.key.startsWith("folder:") }
        AppMenuItem {
            visible: tabMenu.key.startsWith("folder:")
            text: qsTr("Delete folder")
            iconName: "trash"
            danger: true
            onTriggered: folderEditor.remove(tabMenu.key)
        }
    }

    implicitHeight: 44

    ListView {
        anchors.left: parent.left
        anchors.right: addFolder.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        leftMargin: 8
        rightMargin: 8
        orientation: ListView.Horizontal
        spacing: 2
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        model: folders

        delegate: Item {
            id: tab
            required property string key
            required property string title
            required property int unreadCount
            readonly property bool current: key === root.currentKey

            height: ListView.view.height
            width: content.implicitWidth + 20

            Row {
                id: content
                anchors.centerIn: parent
                spacing: 6

                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: tab.title
                    color: tab.current ? Theme.accent : Theme.textMuted
                    font.pixelSize: Theme.fontBody
                    font.weight: tab.current ? Font.DemiBold : Font.Normal
                }

                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    visible: tab.unreadCount > 0
                    height: 16
                    width: Math.max(16, count.implicitWidth + 8)
                    radius: 8
                    color: tab.current ? Theme.accent : Theme.badgeMuted

                    Text {
                        id: count
                        anchors.centerIn: parent
                        text: tab.unreadCount > 999 ? "999+" : tab.unreadCount
                        color: Theme.textOnAccent
                        font.pixelSize: 10
                        font.weight: Font.Bold
                    }
                }
            }

            Rectangle {
                anchors.bottom: parent.bottom
                anchors.horizontalCenter: parent.horizontalCenter
                visible: tab.current
                width: content.width
                height: 2
                radius: 1
                color: Theme.accent
            }

            TapHandler {
                onTapped: {
                    root.currentKey = tab.key
                    root.selected(tab.key)
                }
            }
            TapHandler {
                acceptedButtons: Qt.RightButton
                onTapped: {
                    tabMenu.key = tab.key
                    tabMenu.popup()
                }
            }
        }
    }

    IconButton {
        id: addFolder
        objectName: "addFolder"
        anchors.right: parent.right
        anchors.rightMargin: 6
        anchors.verticalCenter: parent.verticalCenter
        iconName: "folder"
        glyphSize: 15
        Accessible.name: qsTr("New folder")
        ToolTip.visible: hovered
        ToolTip.delay: 600
        ToolTip.text: Accessible.name
        onClicked: root.editRequested("")
    }
}
