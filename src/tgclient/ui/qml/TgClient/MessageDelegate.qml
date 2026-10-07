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
    required property string senderKey
    required property int senderColor
    required property string senderInitials
    required property string senderAvatar
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
    required property string transcript
    required property string transcriptState
    required property string translation
    required property string translationState
    required property var reactions        // [{key, label, count, chosen}]
    required property string forwardedFrom
    required property bool unreadSeparator
    required property bool albumHidden
    required property var albumItems
    required property string stickerFormat
    required property string playbackPath
    required property bool selected
    required property var linkPreview      // {url, site, title, text, image, ...} or {}

    property bool isGroupChat: false
    property bool flashed: false   // just jumped to: briefly tinted
    property bool selecting: false // some messages are selected: a click toggles this one

    signal replyRequested(var messageId)
    signal jumpRequested(var messageId)
    signal menuRequested(var messageId)
    signal linkActivated(string link)
    signal senderClicked(string senderKey, string senderName, var messageId)
    signal reactionToggled(var messageId, string key)
    signal selectToggled(var messageId, bool range)   // range: Shift-click
    signal reactRequested(var messageId, var button)   // the hover button: pick a reaction

    readonly property real sidePadding: 16
    readonly property real avatarSpace: isGroupChat && !isOutgoing ? 40 : 0
    readonly property real maxBubbleWidth: Math.max(160, Math.min(560, (width - avatarSpace) * 0.78))
    // Stickers and round videos sit on the background without a bubble; photos and videos
    // without text fill the bubble almost edge to edge, with the time drawn over the image.
    readonly property bool visualMedia: ["photo", "video", "animation", "sticker", "videoNote",
                                         "album"]
                                        .indexOf(mediaKind) >= 0
    readonly property bool hasReactions: reactions !== undefined && reactions.length > 0
    readonly property bool timeOnMedia: visualMedia && html === "" && !hasReactions
    // Voice and file cards leave room on their right for the time instead of an extra line
    // (a voice transcript below the player takes that line, so the time moves under it).
    readonly property bool showTranscript: mediaKind === "voice"
                                           && (transcriptState !== "" || ai.enabled)
    readonly property bool longTranscript: transcriptState === "done" || transcriptState === "error"
    readonly property bool timeBesideMedia: ["voice", "document", "audio"].indexOf(mediaKind) >= 0
                                           && html === "" && !(showTranscript && longTranscript)
                                           && !hasReactions
    readonly property bool bare: (mediaKind === "sticker" || mediaKind === "videoNote")
                                 && html === "" && replyToId === 0 && !showSender
                                 && forwardedFrom === "" && !hasReactions
    readonly property real bubblePadding: bare ? 0 : timeOnMedia && !showSender && replyToId === 0
                                                     && forwardedFrom === "" ? 4 : 10
    readonly property real maxContentWidth: maxBubbleWidth - 2 * bubblePadding

    width: ListView.view ? ListView.view.width : 400
    // Other members of an album are drawn in the grid of its newest message.
    height: albumHidden ? 0 : column.height
    visible: !albumHidden

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

        Item {  // "Unread messages": where the user stopped reading when the chat was opened
            objectName: "unreadSeparator"
            width: parent.width
            height: visible ? 36 : 0
            visible: root.unreadSeparator

            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                height: 26
                color: Theme.sidebar
                opacity: 0.85
            }
            Text {
                anchors.centerIn: parent
                text: qsTr("Unread messages")
                color: Theme.accent
                font.pixelSize: Theme.fontSmall
                font.weight: Font.DemiBold
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

            Rectangle {  // in the multi-selection
                objectName: "selectionTint"
                anchors.fill: parent
                anchors.topMargin: -2
                anchors.bottomMargin: -2
                visible: root.selected
                color: Theme.selection
                opacity: 0.6
            }

            Avatar {
                visible: root.showAvatar
                x: root.sidePadding
                anchors.bottom: bubble.bottom
                size: 32
                avatarSource: root.senderAvatar
                initials: root.senderInitials
                colorIndex: root.senderColor

                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    onTapped: root.senderClicked(root.senderKey, root.senderName, root.messageId)
                }
            }

            HoverHandler { id: rowHover }

            // Shown while the pointer is over the message: pick a reaction without the menu.
            AbstractButton {
                id: reactButton
                objectName: "reactButton"
                visible: (rowHover.hovered || hovered) && !root.selecting && !root.albumHidden
                width: 28
                height: 28
                x: root.isOutgoing ? bubble.x - width - 6 : bubble.x + bubble.width + 6
                anchors.bottom: bubble.bottom
                hoverEnabled: true
                Accessible.name: qsTr("React")
                onClicked: root.reactRequested(root.messageId, reactButton)
                background: Rectangle {
                    radius: width / 2
                    color: reactButton.hovered ? Theme.hover : Theme.popup
                    border.width: 1
                    border.color: Theme.popupBorder
                }
                contentItem: Item {
                    Icon {
                        anchors.centerIn: parent
                        name: "emoji"
                        size: 16
                    }
                }
            }

            Rectangle {
                id: bubble

                readonly property real naturalWidth: Math.max(
                    senderLabel.visible ? senderLabel.implicitWidth : 0,
                    forwardLabel.visible ? forwardLabel.implicitWidth : 0,
                    reactionFlow.visible ? reactionFlow.naturalWidth + timeRow.implicitWidth + 12
                                         : 0,
                    replyBlock.visible ? replyBlock.naturalWidth : 0,
                    mediaText.visible ? mediaText.implicitWidth + timeRow.implicitWidth + 12 : 0,
                    media.visible ? media.naturalWidth
                                    + (root.timeBesideMedia ? timeRow.implicitWidth + 12 : 0) : 0,
                    body.visible ? body.implicitWidth : 0,
                    linkCard.visible ? linkCard.naturalWidth : 0,
                    translationBlock.visible ? translationBlock.naturalWidth : 0,
                    transcriptText.visible ? transcriptText.implicitWidth
                        + (root.timeBesideMedia ? timeRow.implicitWidth + 12 : 0) : 0,
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
                    acceptedModifiers: Qt.NoModifier
                    onTapped: if (root.selecting) root.selectToggled(root.messageId, false)
                    onDoubleTapped: if (!root.selecting) root.replyRequested(root.messageId)
                }
                TapHandler {  // Cmd-click (Ctrl elsewhere): add to / remove from the selection
                    acceptedButtons: Qt.LeftButton
                    acceptedModifiers: Qt.ControlModifier
                    onTapped: root.selectToggled(root.messageId, false)
                }
                TapHandler {
                    acceptedButtons: Qt.LeftButton
                    acceptedModifiers: Qt.ShiftModifier
                    onTapped: root.selectToggled(root.messageId, true)
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

                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                        TapHandler {
                            onTapped: root.senderClicked(root.senderKey, root.senderName,
                                                         root.messageId)
                        }
                    }

                    Text {
                        id: forwardLabel
                        visible: root.forwardedFrom !== ""
                        width: parent.width
                        text: qsTr("Forwarded from %1").arg(root.forwardedFrom)
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        color: Theme.accent
                        font.pixelSize: Theme.fontSmall
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
                        stickerFormat: root.stickerFormat
                        playbackPath: root.playbackPath
                        albumItems: root.albumItems
                        maxWidth: root.maxContentWidth
                        messageId: root.messageId
                        onActivated: messages.activateMedia(root.messageId)
                    }

                    // Voice transcript: "Transcribe" link, progress, the text, or an error to retry.
                    Text {
                        id: transcriptText
                        readonly property bool actionable: root.transcriptState === ""
                                                           || root.transcriptState === "error"
                        visible: root.showTranscript
                        width: parent.width
                        text: root.transcriptState === "done" ? root.transcript
                            : root.transcriptState === "pending" ? qsTr("Transcribing\u2026")
                            : root.transcriptState === "error"
                              ? root.transcript + " \u00b7 " + qsTr("Retry")
                            : qsTr("Transcribe")
                        textFormat: Text.PlainText
                        wrapMode: Text.Wrap
                        color: root.transcriptState === "done" ? Theme.text
                             : root.transcriptState === "error" ? Theme.danger : Theme.accent
                        font.pixelSize: root.transcriptState === "done" ? Theme.fontBody
                                                                        : Theme.fontSmall
                        font.weight: actionable && root.transcriptState === ""
                                     ? Font.DemiBold : Font.Normal

                        HoverHandler {
                            cursorShape: transcriptText.actionable ? Qt.PointingHandCursor
                                                                   : Qt.ArrowCursor
                        }
                        TapHandler {
                            enabled: transcriptText.actionable && ai.enabled
                            onTapped: ai.transcribe(root.messageId)
                        }
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

                    // Link preview: site, title, description, picture (on the side or below).
                    Rectangle {
                        id: linkCard
                        objectName: "linkCard"
                        readonly property var lp: root.linkPreview || ({})
                        readonly property bool hasImage: (lp.image || "") !== ""
                        readonly property bool sideImage: hasImage && !lp.large
                        readonly property real naturalWidth: Math.min(340, Math.max(
                            siteText.implicitWidth, titleText.implicitWidth,
                            descriptionText.implicitWidth, lp.large ? lp.width || 0 : 0)
                            + 18 + (sideImage ? 66 : 0))
                        visible: (lp.url || "") !== ""
                        width: parent.width
                        height: Math.max(cardColumn.height, sideImage ? 62 : 0) + 12
                        radius: 6
                        color: Theme.replyBackground

                        Rectangle {
                            width: 3
                            height: parent.height
                            radius: 1.5
                            color: Theme.replyBar
                        }
                        Image {
                            visible: linkCard.sideImage
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: 6
                            width: 56
                            height: 56
                            source: linkCard.sideImage ? linkCard.lp.image : ""
                            sourceSize.width: 112
                            sourceSize.height: 112
                            fillMode: Image.PreserveAspectCrop
                            asynchronous: true
                        }
                        Column {
                            id: cardColumn
                            x: 10
                            y: 6
                            width: parent.width - 16 - (linkCard.sideImage ? 64 : 0)
                            spacing: 2

                            Text {
                                id: siteText
                                width: parent.width
                                visible: text !== ""
                                text: linkCard.lp.site || ""
                                textFormat: Text.PlainText
                                elide: Text.ElideRight
                                color: Theme.accent
                                font.pixelSize: Theme.fontSmall
                                font.weight: Font.DemiBold
                            }
                            Text {
                                id: titleText
                                width: parent.width
                                visible: text !== ""
                                text: linkCard.lp.title || ""
                                textFormat: Text.PlainText
                                wrapMode: Text.Wrap
                                maximumLineCount: 2
                                elide: Text.ElideRight
                                color: Theme.text
                                font.pixelSize: Theme.fontBody
                                font.weight: Font.DemiBold
                            }
                            Text {
                                id: descriptionText
                                width: parent.width
                                visible: text !== ""
                                text: linkCard.lp.text || ""
                                textFormat: Text.PlainText
                                wrapMode: Text.Wrap
                                maximumLineCount: 4
                                elide: Text.ElideRight
                                color: Theme.text
                                font.pixelSize: Theme.fontSmall
                            }
                            Image {  // large picture under the text
                                visible: linkCard.hasImage && !linkCard.sideImage
                                width: Math.min(parent.width, linkCard.lp.width || parent.width)
                                height: visible ? width * (linkCard.lp.height || 3)
                                                  / (linkCard.lp.width || 4) : 0
                                source: visible ? linkCard.lp.image : ""
                                fillMode: Image.PreserveAspectCrop
                                asynchronous: true
                            }
                            Text {
                                visible: text !== ""
                                text: linkCard.lp.label || ""
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSmall
                            }
                        }

                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: root.linkActivated(linkCard.lp.url) }
                    }

                    // Translation into the user's language (Translate in the message menu).
                    Column {
                        id: translationBlock
                        objectName: "translationBlock"
                        readonly property real naturalWidth: Math.max(
                            translationLabel.implicitWidth, translationText.implicitWidth)
                        visible: root.translationState !== ""
                        width: parent.width
                        topPadding: 2
                        spacing: 1

                        Text {
                            id: translationLabel
                            text: root.translationState === "pending" ? qsTr("Translating\u2026")
                                : root.translationState === "error" ? qsTr("Translation failed")
                                : qsTr("Translation")
                            color: root.translationState === "error" ? Theme.danger : Theme.accent
                            font.pixelSize: Theme.fontSmall
                            font.weight: Font.DemiBold
                        }
                        Text {
                            id: translationText
                            visible: text !== ""
                            width: parent.width
                            text: root.translation
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                            color: root.translationState === "error" ? Theme.danger : Theme.text
                            font.pixelSize: root.translationState === "error" ? Theme.fontSmall
                                                                               : Theme.fontTitle
                        }
                    }

                    Flow {
                        id: reactionFlow
                        readonly property real naturalWidth: {
                            let total = 0
                            for (let i = 0; i < reactionRepeater.count; ++i) {
                                const item = reactionRepeater.itemAt(i)
                                if (item)
                                    total += item.implicitWidth + (i > 0 ? spacing : 0)
                            }
                            return total
                        }
                        visible: root.hasReactions
                        // The last line leaves room for the time on the right.
                        width: parent.width - timeRow.implicitWidth - 8
                        topPadding: 3
                        spacing: 4

                        Repeater {
                            id: reactionRepeater
                            model: root.reactions
                            delegate: Rectangle {
                                id: pill
                                required property var modelData
                                implicitWidth: pillRow.implicitWidth + 14
                                width: implicitWidth
                                height: 26
                                radius: 13
                                color: modelData.chosen ? Theme.accent
                                     : Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b,
                                               pillHover.hovered ? 0.22 : 0.13)

                                Row {
                                    id: pillRow
                                    anchors.centerIn: parent
                                    spacing: 4
                                    Text {
                                        anchors.verticalCenter: parent.verticalCenter
                                        visible: pill.modelData.image === ""
                                        text: pill.modelData.label
                                        textFormat: Text.PlainText
                                        font.pixelSize: 14
                                    }
                                    Image {  // custom emoji reaction
                                        anchors.verticalCenter: parent.verticalCenter
                                        visible: pill.modelData.image !== ""
                                        width: 18
                                        height: 18
                                        source: pill.modelData.image
                                        sourceSize.width: 36
                                        sourceSize.height: 36
                                        fillMode: Image.PreserveAspectFit
                                    }
                                    Text {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: pill.modelData.count
                                        color: pill.modelData.chosen ? Theme.textOnAccent
                                                                     : Theme.accent
                                        font.pixelSize: Theme.fontSmall
                                        font.weight: Font.DemiBold
                                    }
                                }

                                HoverHandler {
                                    id: pillHover
                                    cursorShape: Qt.PointingHandCursor
                                }
                                // Who reacted (names arrive later for bigger lists).
                                readonly property string reactors: pillHover.hovered
                                    ? (messages.reactorsVersion,
                                       messages.reactorsText(root.messageId, modelData.key))
                                    : ""
                                ToolTip.visible: pillHover.hovered && reactors !== ""
                                ToolTip.delay: 400
                                ToolTip.text: reactors
                                TapHandler {
                                    onTapped: root.reactionToggled(root.messageId,
                                                                   pill.modelData.key)
                                }
                            }
                        }
                    }

                    Item {  // room for the time row when there's no text to tuck it into
                        visible: ((translationBlock.visible || linkCard.visible)
                                  && !reactionFlow.visible)
                                 || (!body.visible && !root.timeOnMedia && !root.timeBesideMedia
                                     && !reactionFlow.visible)
                        width: 1
                        height: mediaText.visible ? 0 : timeRow.height
                    }
                }

                Rectangle {  // flash after a jump (search result, quote, summary link)
                    anchors.fill: parent
                    radius: parent.radius
                    color: Theme.flash
                    opacity: root.flashed ? 1 : 0
                    visible: opacity > 0
                    Behavior on opacity { NumberAnimation { duration: 400 } }
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
                    Icon {
                        anchors.verticalCenter: parent.verticalCenter
                        visible: root.isOutgoing && root.status !== "failed"
                        name: root.status === "read" ? "checks"
                              : root.status === "pending" ? "clock" : "check"
                        color: root.timeOnMedia ? "#FFFFFF"
                             : root.status === "read" ? Theme.accent : Theme.textMuted
                        size: 14
                    }
                    Text {
                        visible: root.isOutgoing && root.status === "failed"
                        text: "!"
                        color: Theme.danger
                        font.pixelSize: 11
                        font.weight: Font.DemiBold
                    }
                }
            }
        }
    }
}
