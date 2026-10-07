import QtQuick

Rectangle {
    id: row

    required property var chatId          // int53: exceeds QML int range, keep as var
    required property string title
    required property string preview
    required property string time
    required property int unreadCount
    required property int mentionCount
    required property bool muted
    required property bool pinned
    required property string avatarSource
    required property string initials
    required property int colorIndex
    required property string chatType
    required property string draft
    required property string typing
    required property bool online
    required property bool markedUnread

    property bool selected: false
    signal clicked()
    signal menuRequested()

    implicitHeight: 64
    color: selected ? Theme.selection : hover.hovered ? Theme.hover : "transparent"

    HoverHandler { id: hover }
    TapHandler { onTapped: row.clicked() }
    TapHandler {
        acceptedButtons: Qt.RightButton
        onTapped: row.menuRequested()
    }

    component Badge: Rectangle {
        property alias label: badgeText.text
        height: 20
        width: Math.max(20, badgeText.implicitWidth + 12)
        radius: 10
        Text {
            id: badgeText
            anchors.centerIn: parent
            color: Theme.textOnAccent
            font.pixelSize: 11
            font.weight: Font.Bold
        }
    }

    Avatar {
        id: avatar
        x: 12
        anchors.verticalCenter: parent.verticalCenter
        size: 44
        avatarSource: row.avatarSource
        initials: row.initials
        colorIndex: row.colorIndex
    }

    Rectangle {  // online dot
        visible: row.online
        x: avatar.x + avatar.width - width + 1
        y: avatar.y + avatar.height - height + 1
        width: 13
        height: 13
        radius: width / 2
        color: Theme.accent
        border.width: 2
        border.color: row.selected ? Theme.selection : Theme.sidebar
    }

    Text {
        id: titleText
        anchors.left: avatar.right
        anchors.leftMargin: 10
        anchors.right: timeText.left
        anchors.rightMargin: 8
        y: 12
        text: row.title
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: Theme.text
        font.pixelSize: Theme.fontTitle
        font.weight: Font.DemiBold
    }

    Text {
        id: timeText
        anchors.right: parent.right
        anchors.rightMargin: 12
        anchors.baseline: titleText.baseline
        text: row.time
        color: row.unreadCount > 0 && !row.muted ? Theme.accent : Theme.textMuted
        font.pixelSize: Theme.fontSmall
    }

    // "typing…" (accent) or "Draft: …" (danger) instead of the last message.
    Text {
        id: prefixText
        anchors.left: titleText.left
        anchors.baseline: previewText.baseline
        visible: row.typing === "" && row.draft !== ""
        text: qsTr("Draft:") + " "
        color: Theme.danger
        font.pixelSize: Theme.fontBody
    }

    Text {
        id: previewText
        anchors.left: prefixText.visible ? prefixText.right : titleText.left
        anchors.right: badges.left
        anchors.rightMargin: 8
        anchors.top: titleText.bottom
        anchors.topMargin: 3
        text: row.typing !== "" ? row.typing : row.draft !== "" ? row.draft : row.preview
        textFormat: Text.PlainText
        elide: Text.ElideRight
        maximumLineCount: 1
        color: row.typing !== "" ? Theme.accent : Theme.textMuted
        font.pixelSize: Theme.fontBody
    }

    Row {
        id: badges
        anchors.right: parent.right
        anchors.rightMargin: 12
        anchors.verticalCenter: previewText.verticalCenter
        spacing: 4

        Badge {
            visible: row.mentionCount > 0
            label: "@"
            color: Theme.accent
        }

        Badge {  // marked as unread by hand
            objectName: "markedUnread"
            visible: row.markedUnread
            width: 20
            label: ""
            color: row.muted ? Theme.badgeMuted : Theme.accent
        }

        Badge {
            visible: row.unreadCount > 0
            label: row.unreadCount >= 1000 ? (row.unreadCount / 1000).toFixed(1) + "K" : row.unreadCount
            color: row.muted ? Theme.badgeMuted : Theme.accent
        }
    }
}
