import QtQuick
import QtQuick.Controls.Basic

// Topics of the open forum (instead of its messages until one is picked).
ListView {
    id: root
    objectName: "topicList"
    signal topicPicked(var topicId)

    clip: true
    model: topics
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar {}

    delegate: Rectangle {
        id: row
        required property var topicId
        required property string name
        required property string iconColor
        required property string preview
        required property string time
        required property int unreadCount
        required property int mentionCount
        required property bool pinned
        required property bool closed
        required property bool general

        width: ListView.view.width
        height: 62
        color: hover.hovered ? Theme.hover : "transparent"

        HoverHandler { id: hover }
        TapHandler { onTapped: root.topicPicked(row.topicId) }

        Rectangle {  // topic icon: a colored circle with the first letter ("#" for General)
            id: icon
            x: 16
            anchors.verticalCenter: parent.verticalCenter
            width: 34
            height: 34
            radius: 17
            color: row.general ? Theme.pill : row.iconColor
            Text {
                anchors.centerIn: parent
                text: row.general ? "#" : row.name.slice(0, 1).toUpperCase()
                color: row.general ? Theme.textMuted : "#FFFFFF"
                font.pixelSize: 15
                font.weight: Font.Bold
            }
        }
        Text {
            id: nameText
            anchors.left: icon.right
            anchors.leftMargin: 12
            anchors.right: timeText.left
            anchors.rightMargin: 8
            y: 11
            text: (row.closed ? "🔒 " : "") + row.name
            textFormat: Text.PlainText
            elide: Text.ElideRight
            color: Theme.text
            font.pixelSize: Theme.fontTitle
            font.weight: Font.DemiBold
        }
        Text {
            id: timeText
            anchors.right: parent.right
            anchors.rightMargin: 16
            anchors.baseline: nameText.baseline
            text: row.time
            color: row.unreadCount > 0 ? Theme.accent : Theme.textMuted
            font.pixelSize: Theme.fontSmall
        }
        Text {
            anchors.left: nameText.left
            anchors.right: badges.left
            anchors.rightMargin: 8
            anchors.top: nameText.bottom
            anchors.topMargin: 3
            text: row.preview
            textFormat: Text.PlainText
            elide: Text.ElideRight
            color: Theme.textMuted
            font.pixelSize: Theme.fontBody
        }
        Row {
            id: badges
            anchors.right: parent.right
            anchors.rightMargin: 16
            y: 34
            spacing: 4
            Rectangle {
                visible: row.mentionCount > 0
                width: 20
                height: 20
                radius: 10
                color: Theme.accent
                Text {
                    anchors.centerIn: parent
                    text: "@"
                    color: Theme.textOnAccent
                    font.pixelSize: 11
                    font.weight: Font.Bold
                }
            }
            Rectangle {
                visible: row.unreadCount > 0
                height: 20
                width: Math.max(20, unreadText.implicitWidth + 12)
                radius: 10
                color: Theme.accent
                Text {
                    id: unreadText
                    anchors.centerIn: parent
                    text: row.unreadCount
                    color: Theme.textOnAccent
                    font.pixelSize: 11
                    font.weight: Font.Bold
                }
            }
            Icon {
                visible: row.pinned && row.unreadCount === 0
                name: "pin"
                size: 16
            }
        }
        Rectangle {
            anchors.bottom: parent.bottom
            anchors.left: nameText.left
            anchors.right: parent.right
            height: 1
            color: Theme.separator
            opacity: 0.6
        }
    }

    Text {
        anchors.centerIn: parent
        visible: root.count === 0
        text: qsTr("Loading topics")
        color: Theme.textMuted
        font.pixelSize: Theme.fontBody
    }
}
