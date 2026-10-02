import QtQuick
import QtQuick.Layouts

// Row of the search results: a section header, a matching chat, or a matching message.
Item {
    id: root

    required property string kind
    required property var chatId          // int53, keep as var
    required property var messageId
    required property string title
    required property string subtitle
    required property string snippet
    required property string time
    required property string avatarSource
    required property string initials
    required property int colorIndex
    required property bool byMeaning

    signal activated()

    width: ListView.view ? ListView.view.width : 300
    implicitHeight: kind === "section" ? 30 : kind === "chat" ? 52 : 68

    Text {
        visible: root.kind === "section"
        anchors.left: parent.left
        anchors.leftMargin: 16
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 6
        text: root.title === "chats" ? qsTr("Chats") : qsTr("Messages")
        color: Theme.textMuted
        font.pixelSize: 11
        font.weight: Font.DemiBold
        font.capitalization: Font.AllUppercase
        font.letterSpacing: 0.6
    }

    Rectangle {
        visible: root.kind !== "section"
        anchors.fill: parent
        color: hover.hovered ? Theme.hover : "transparent"

        HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: root.activated() }

        Avatar {
            id: avatar
            x: 12
            anchors.verticalCenter: parent.verticalCenter
            size: root.kind === "chat" ? 36 : 40
            avatarSource: root.avatarSource
            initials: root.initials
            colorIndex: root.colorIndex
        }

        ColumnLayout {
            anchors.left: avatar.right
            anchors.leftMargin: 10
            anchors.right: parent.right
            anchors.rightMargin: 12
            anchors.verticalCenter: parent.verticalCenter
            spacing: 2

            RowLayout {
                Layout.fillWidth: true
                spacing: 6

                Text {
                    Layout.fillWidth: true
                    text: root.subtitle !== "" ? root.title + " · " + root.subtitle : root.title
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    color: Theme.text
                    font.pixelSize: Theme.fontBody
                    font.weight: Font.DemiBold
                }
                Icon {
                    visible: root.byMeaning
                    name: "sparkle"
                    color: Theme.accent
                    size: 13
                }
                Text {
                    visible: root.time !== ""
                    text: root.time
                    color: Theme.textMuted
                    font.pixelSize: 11
                }
            }

            Text {
                Layout.fillWidth: true
                visible: root.kind === "message"
                text: root.snippet
                textFormat: Text.StyledText
                wrapMode: Text.Wrap
                maximumLineCount: 2
                elide: Text.ElideRight
                color: Theme.textMuted
                font.pixelSize: Theme.fontSmall
                lineHeight: 1.05
            }
        }
    }
}
