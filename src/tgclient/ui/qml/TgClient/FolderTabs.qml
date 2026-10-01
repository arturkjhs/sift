import QtQuick

Item {
    id: root
    property string currentKey: "main"
    signal selected(string key)

    implicitHeight: 44

    ListView {
        anchors.fill: parent
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
        }
    }
}
