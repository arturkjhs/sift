import QtQuick
import QtQuick.Controls.Basic

// One row of the message list: optional day separator, then a service line or a bubble.
Item {
    id: root

    required property var messageId       // int53, keep as var
    required property bool isOutgoing
    required property bool isService
    required property string serviceText
    required property string senderName
    required property int senderColor
    required property string senderInitials
    required property bool showSender
    required property bool groupBottom
    required property bool showAvatar
    required property string html
    required property string time
    required property bool edited
    required property string status
    required property var replyToId
    required property string replySender
    required property string replyText
    required property string mediaLabel
    required property string dayLabel
    required property string mediaKind
    required property string mediaSource
    required property int mediaWidth
    required property int mediaHeight
    required property var fileId
    required property string fileName
    required property string fileInfo
    required property string fileState
    required property real fileProgress
    required property string duration
    required property var waveform
    required property string stickerEmoji

    property bool isGroupChat: false

    signal replyRequested(var messageId)
    signal jumpRequested(var messageId)
    signal menuRequested(var messageId)
    signal linkActivated(string link)

    readonly property real sidePadding: 16
    readonly property real avatarSpace: isGroupChat && !isOutgoing ? 40 : 0
    readonly property real maxBubbleWidth: Math.max(160, Math.min(560, (width - avatarSpace) * 0.78))
    // Stickers and round videos sit on the background without a bubble; photos and videos
    // without text fill the bubble almost edge to edge, with the time drawn over the image.
    readonly property bool visualMedia: ["photo", "video", "animation", "sticker", "videoNote"]
                                        .indexOf(mediaKind) >= 0
    readonly property bool timeOnMedia: visualMedia && html === ""
    // Voice and file cards leave room on their right for the time instead of an extra line.
    readonly property bool timeBesideMedia: ["voice", "document", "audio"].indexOf(mediaKind) >= 0
                                           && html === ""
    readonly property bool bare: (mediaKind === "sticker" || mediaKind === "videoNote")
                                 && html === "" && replyToId === 0 && !showSender
    readonly property real bubblePadding: bare ? 0 : timeOnMedia && !showSender && replyToId === 0 ? 4 : 10
    readonly property real maxContentWidth: maxBubbleWidth - 2 * bubblePadding
    readonly property var statusGlyph: ({ pending: "\u25f7", sent: "\u2713", read: "\u2713\u2713", failed: "!" })

    width: ListView.view ? ListView.view.width : 400
    height: column.height

    Column {
        id: column
        width: parent.width
        topPadding: root.dayLabel !== "" ? 4 : (root.showSender || root.isService ? 8 : 2)

        Item {
            width: parent.width
            height: visible ? 40 : 0
            visible: root.dayLabel !== ""

            Rectangle {
                anchors.centerIn: parent
                height: 24
                width: dayText.implicitWidth + 20
                radius: 12
                color: Theme.pill

                Text {
                    id: dayText
                    anchors.centerIn: parent
                    text: root.dayLabel
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                    font.weight: Font.DemiBold
                }
            }
        }

        Item {
            width: parent.width
            height: visible ? serviceBox.height + 4 : 0
            visible: root.isService

            Rectangle {
                id: serviceBox
                anchors.horizontalCenter: parent.horizontalCenter
                width: Math.min(serviceLabel.implicitWidth + 24, parent.width - 64)
                height: serviceLabel.height + 8
                radius: 12
                color: Theme.pill

                Text {
                    id: serviceLabel
                    anchors.centerIn: parent
                    width: parent.width - 24
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    text: root.serviceText
                    textFormat: Text.PlainText
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                }
            }
        }

        Item {
            width: parent.width
            height: visible ? bubble.height : 0
            visible: !root.isService

            Avatar {
                visible: root.showAvatar
                x: root.sidePadding
                anchors.bottom: bubble.bottom
                size: 32
                initials: root.senderInitials
                colorIndex: root.senderColor
            }

            Rectangle {
                id: bubble

                readonly property real naturalWidth: Math.max(
                    senderLabel.visible ? senderLabel.implicitWidth : 0,
                    replyBlock.visible ? replyBlock.naturalWidth : 0,
                    mediaText.visible ? mediaText.implicitWidth + timeRow.implicitWidth + 12 : 0,
                    media.visible ? media.naturalWidth
                                    + (root.timeBesideMedia ? timeRow.implicitWidth + 12 : 0) : 0,
                    body.visible ? body.implicitWidth : 0,
                    timeRow.implicitWidth)

                x: root.isOutgoing ? parent.width - width - root.sidePadding
                                   : root.sidePadding + root.avatarSpace
                width: Math.min(root.maxContentWidth, naturalWidth) + 2 * root.bubblePadding
                height: content.y + content.height + root.bubblePadding
                radius: 12
                bottomLeftRadius: !root.isOutgoing && root.groupBottom ? 4 : 12
                bottomRightRadius: root.isOutgoing && root.groupBottom ? 4 : 12
                color: root.bare ? "transparent" : root.isOutgoing ? Theme.bubbleOut : Theme.bubbleIn

                TapHandler {
                    acceptedButtons: Qt.RightButton
                    onTapped: root.menuRequested(root.messageId)
                }
                TapHandler {
                    acceptedButtons: Qt.LeftButton
                    onDoubleTapped: root.replyRequested(root.messageId)
                }

                Column {
                    id: content
                    x: root.bubblePadding
                    y: root.bubblePadding > 4 ? root.bubblePadding - 2 : root.bubblePadding
                    width: bubble.width - 2 * root.bubblePadding
                    spacing: 3

                    Text {
                        id: senderLabel
                        visible: root.showSender && root.senderName !== ""
                        width: parent.width
                        text: root.senderName
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        color: Theme.avatarColors[root.senderColor % Theme.avatarColors.length]
                        font.pixelSize: Theme.fontBody
                        font.weight: Font.DemiBold
                    }

                    Rectangle {
                        id: replyBlock
                        readonly property real naturalWidth: Math.max(replySenderText.implicitWidth,
                                                                      replyBodyText.implicitWidth) + 18
                        visible: root.replyToId !== 0
                        width: parent.width
                        height: 40
                        radius: 6
                        color: Theme.replyBackground

                        Rectangle {
                            width: 3
                            height: parent.height
                            radius: 1.5
                            color: Theme.replyBar
                        }

                        Column {
                            x: 10
                            anchors.verticalCenter: parent.verticalCenter
                            width: parent.width - 16

                            Text {
                                id: replySenderText
                                width: parent.width
                                text: root.replySender !== "" ? root.replySender : qsTr("Loading")
                                textFormat: Text.PlainText
                                elide: Text.ElideRight
                                color: Theme.accent
                                font.pixelSize: Theme.fontSmall
                                font.weight: Font.DemiBold
                            }
                            Text {
                                id: replyBodyText
                                width: parent.width
                                text: root.replyText
                                textFormat: Text.PlainText
                                elide: Text.ElideRight
                                maximumLineCount: 1
                                color: Theme.text
                                font.pixelSize: Theme.fontSmall
                            }
                        }

                        TapHandler { onTapped: root.jumpRequested(root.replyToId) }
                    }

                    Text {
                        id: mediaText
                        visible: root.mediaLabel !== ""
                        text: root.mediaLabel
                        textFormat: Text.PlainText
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontBody
                        font.italic: true
                    }

                    MediaContent {
                        id: media
                        kind: root.mediaKind
                        source: root.mediaSource
                        mediaWidth: root.mediaWidth
                        mediaHeight: root.mediaHeight
                        fileId: root.fileId
                        fileName: root.fileName
                        fileInfo: root.fileInfo
                        fileState: root.fileState
                        fileProgress: root.fileProgress
                        duration: root.duration
                        waveform: root.waveform
                        stickerEmoji: root.stickerEmoji
                        maxWidth: root.maxContentWidth
                        messageId: root.messageId
                        onActivated: messages.activateMedia(root.messageId)
                    }

                    TextEdit {
                        id: body
                        visible: root.html !== ""
                        width: parent.width
                        text: root.html
                        textFormat: TextEdit.RichText
                        wrapMode: TextEdit.Wrap
                        readOnly: true
                        selectByMouse: true
                        persistentSelection: false
                        color: Theme.text
                        selectionColor: Theme.accent
                        selectedTextColor: Theme.textOnAccent
                        font.pixelSize: Theme.fontTitle
                        onLinkActivated: link => root.linkActivated(link)

                        HoverHandler {
                            cursorShape: body.linkAt(point.position.x, point.position.y) !== ""
                                         ? Qt.PointingHandCursor : Qt.IBeamCursor
                        }
                    }

                    Item {  // room for the time row when there's no text to tuck it into
                        visible: !body.visible && !root.timeOnMedia && !root.timeBesideMedia
                        width: 1
                        height: mediaText.visible ? 0 : timeRow.height
                    }
                }

                Rectangle {  // backdrop for the time drawn over a photo or sticker
                    visible: root.timeOnMedia
                    anchors.fill: timeRow
                    anchors.margins: -4
                    anchors.leftMargin: -6
                    anchors.rightMargin: -6
                    radius: height / 2
                    color: "#80000000"
                }

                Row {
                    id: timeRow
                    readonly property color textColor: root.timeOnMedia ? "#FFFFFF" : Theme.textMuted
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.rightMargin: root.timeOnMedia ? root.bubblePadding + 8 : root.bubblePadding
                    anchors.bottomMargin: root.timeOnMedia ? root.bubblePadding + 8 : 5
                    spacing: 3

                    Text {
                        visible: root.edited
                        text: qsTr("edited")
                        color: timeRow.textColor
                        font.pixelSize: 11
                    }
                    Text {
                        text: root.time
                        color: timeRow.textColor
                        font.pixelSize: 11
                    }
                    Text {
                        visible: root.isOutgoing
                        text: root.statusGlyph[root.status] || ""
                        color: root.status === "failed" ? Theme.danger
                             : root.timeOnMedia ? "#FFFFFF"
                             : root.status === "read" ? Theme.accent : Theme.textMuted
                        font.pixelSize: 11
                        font.weight: Font.DemiBold
                    }
                }
            }
        }
    }
}
