import QtQuick
import QtQuick.Window
import QtMultimedia
import TgClient.Native

// Media part of a message bubble: photo/video thumbnail, sticker, round video, file card, voice.
Item {
    id: root

    property string kind: ""
    property string source: ""
    property int mediaWidth: 0
    property int mediaHeight: 0
    property var fileId: 0
    property string fileName: ""
    property string fileInfo: ""
    property string fileState: ""
    property real fileProgress: 0
    property string duration: ""
    property var waveform: []
    property string stickerEmoji: ""
    property string stickerFormat: ""     // webp | tgs | webm
    property string playbackPath: ""      // animated sticker or GIF, once downloaded
    property var albumItems: []           // kind "album": [{messageId, kind, source, x, y, w, h, ...}]
    property real maxWidth: 300
    property var messageId: 0

    signal activated()

    readonly property bool isVisual: kind === "photo" || kind === "video" || kind === "animation"
                                     || kind === "album"
    // Animations play only while on screen and the app is in front.
    readonly property bool canPlay: visible && Qt.application.state === Qt.ApplicationActive
    readonly property bool isCard: kind === "document" || kind === "audio"
    readonly property bool busy: fileState === "downloading" || fileState === "uploading"
    readonly property real visualScale: isVisual && mediaWidth > maxWidth ? maxWidth / mediaWidth : 1
    readonly property int radiusPx: Math.round(9 * Screen.devicePixelRatio)
    readonly property bool isCurrentVoice: voice.fileId !== 0 && voice.fileId === root.fileId

    readonly property real naturalWidth: isVisual ? Math.round(mediaWidth * visualScale)
                                       : kind === "videoNote" ? 200
                                       : kind === "sticker" ? mediaWidth
                                       : isCard ? card.implicitWidth
                                       : kind === "voice" ? voiceRow.implicitWidth : 0

    implicitWidth: naturalWidth
    implicitHeight: isVisual ? Math.round(mediaHeight * visualScale)
                  : kind === "videoNote" ? 200
                  : kind === "sticker" ? mediaHeight
                  : isCard ? card.implicitHeight
                  : kind === "voice" ? voiceRow.implicitHeight : 0
    visible: kind !== ""

    function clock(seconds) {
        const m = Math.floor(seconds / 60), s = seconds % 60
        return m + ":" + (s < 10 ? "0" : "") + s
    }

    // --- album: a grid of photos and videos, each opens in the viewer ---------------------
    Item {
        anchors.fill: parent
        visible: root.kind === "album"

        Repeater {
            model: root.kind === "album" ? root.albumItems : []
            Item {
                id: cell
                required property var modelData
                x: Math.round(modelData.x * root.visualScale)
                y: Math.round(modelData.y * root.visualScale)
                width: Math.round(modelData.w * root.visualScale)
                height: Math.round(modelData.h * root.visualScale)

                Rectangle {
                    anchors.fill: parent
                    radius: 6
                    color: Theme.pill
                }
                Image {
                    anchors.fill: parent
                    source: cell.modelData.source === "" ? ""
                            : cell.modelData.source + "/" + Math.round(6 * Screen.devicePixelRatio)
                    sourceSize.width: Math.ceil(width * Screen.devicePixelRatio)
                    sourceSize.height: Math.ceil(height * Screen.devicePixelRatio)
                    asynchronous: true
                    smooth: true
                }
                Rectangle {
                    visible: cell.modelData.kind !== "photo"
                             || cell.modelData.fileState === "downloading"
                    anchors.centerIn: parent
                    width: 36
                    height: 36
                    radius: 18
                    color: "#99000000"
                    Text {
                        anchors.centerIn: parent
                        text: cell.modelData.fileState === "downloading"
                              ? Math.round(cell.modelData.progress * 100) + "%" : "\u25b6"
                        color: "#FFFFFF"
                        font.pixelSize: cell.modelData.fileState === "downloading" ? 10 : 14
                    }
                }
                TapHandler { onTapped: messages.activateMedia(cell.modelData.messageId) }
            }
        }
    }

    // --- photo, video, animation, round video ---------------------------------------------
    Item {
        anchors.fill: parent
        visible: (root.isVisual && root.kind !== "album") || root.kind === "videoNote"

        Rectangle {
            anchors.fill: parent
            radius: root.kind === "videoNote" ? width / 2 : 9
            color: Theme.pill
        }

        Image {
            anchors.fill: parent
            source: root.source === "" ? "" : root.source + "/" + root.radiusPx
            sourceSize.width: Math.ceil(width * Screen.devicePixelRatio)
            sourceSize.height: Math.ceil(height * Screen.devicePixelRatio)
            asynchronous: true
            smooth: true
        }

        // GIFs (MP4 without sound) loop inline once downloaded.
        Loader {
            anchors.fill: parent
            active: root.kind === "animation" && root.playbackPath !== "" && root.canPlay
            sourceComponent: Item {
                VideoOutput {
                    id: gifOutput
                    anchors.fill: parent
                    fillMode: VideoOutput.PreserveAspectCrop
                }
                MediaPlayer {
                    source: "file://" + root.playbackPath
                    loops: MediaPlayer.Infinite
                    videoOutput: gifOutput
                    Component.onCompleted: play()
                }
            }
        }

        Rectangle {
            visible: (root.kind !== "photo" || root.busy)
                     && !(root.kind === "animation" && root.playbackPath !== "")
            anchors.centerIn: parent
            width: 46
            height: 46
            radius: 23
            color: "#99000000"

            Text {
                anchors.centerIn: parent
                text: root.busy ? Math.round(root.fileProgress * 100) + "%" : "\u25b6"
                color: "#FFFFFF"
                font.pixelSize: root.busy ? 12 : 18
                font.weight: Font.DemiBold
            }
        }

        Rectangle {
            visible: root.duration !== "" && root.kind !== "photo"
            x: 6
            y: 6
            width: durationText.implicitWidth + 12
            height: 20
            radius: 10
            color: "#99000000"

            Text {
                id: durationText
                anchors.centerIn: parent
                text: root.duration
                color: "#FFFFFF"
                font.pixelSize: 11
            }
        }

        TapHandler { onTapped: root.activated() }
    }

    // --- sticker ----------------------------------------------------------------------------
    Item {
        anchors.fill: parent
        visible: root.kind === "sticker"

        Image {
            id: stickerImage
            anchors.fill: parent
            visible: !animatedSticker.ready
            source: root.kind === "sticker" ? root.source : ""
            sourceSize.width: Math.ceil(width * Screen.devicePixelRatio)
            sourceSize.height: Math.ceil(height * Screen.devicePixelRatio)
            fillMode: Image.PreserveAspectFit
            asynchronous: true
        }

        // TGS and WebM stickers play over their static first frame once the file is here.
        AnimatedImage {
            id: animatedSticker
            anchors.fill: parent
            visible: ready
            source: root.kind === "sticker" && root.stickerFormat !== "webp"
                    ? root.playbackPath : ""
            playing: root.canPlay
        }

        Text {
            anchors.centerIn: parent
            visible: stickerImage.status !== Image.Ready && !animatedSticker.ready
            text: root.stickerEmoji
            font.pixelSize: 64
        }
    }

    // --- document / audio card --------------------------------------------------------------
    Row {
        id: card
        visible: root.isCard
        spacing: 10

        Rectangle {
            width: 44
            height: 44
            radius: 22
            color: Theme.accent

            Text {  // a downloaded file: its type
                anchors.centerIn: parent
                visible: root.fileState === "ready" && !root.busy
                text: root.extension(root.fileName)
                color: Theme.textOnAccent
                font.pixelSize: 10
                font.weight: Font.Bold
            }
            Icon {
                anchors.centerIn: parent
                visible: root.busy || root.fileState !== "ready"
                name: root.busy ? "close" : "arrow-down"
                color: Theme.textOnAccent
                size: root.busy ? 16 : 20
            }

            TapHandler { onTapped: root.activated() }
        }

        Column {
            anchors.verticalCenter: parent.verticalCenter
            width: Math.min(220, Math.max(nameText.implicitWidth, infoText.implicitWidth),
                            root.maxWidth - 54)
            spacing: 2

            Text {
                id: nameText
                width: parent.width
                text: root.fileName
                textFormat: Text.PlainText
                elide: Text.ElideMiddle
                color: Theme.text
                font.pixelSize: Theme.fontBody
                font.weight: Font.DemiBold
            }
            Text {
                id: infoText
                width: parent.width
                text: root.fileInfo
                color: Theme.textMuted
                font.pixelSize: Theme.fontSmall
            }
            Rectangle {
                visible: root.busy
                width: parent.width
                height: 3
                radius: 1.5
                color: Theme.separator

                Rectangle {
                    width: parent.width * root.fileProgress
                    height: parent.height
                    radius: 1.5
                    color: Theme.accent
                }
            }
        }
    }

    function extension(name) {
        const dot = name.lastIndexOf(".")
        return dot > 0 ? name.slice(dot + 1, dot + 5).toUpperCase() : "FILE"
    }

    // --- voice message ----------------------------------------------------------------------
    Row {
        id: voiceRow
        visible: root.kind === "voice"
        spacing: 10

        Rectangle {
            width: 40
            height: 40
            radius: 20
            color: Theme.accent

            Icon {
                anchors.centerIn: parent
                anchors.horizontalCenterOffset: name === "play" ? 1 : 0
                name: !root.isCurrentVoice ? "play"
                    : voice.loading ? "arrow-down"
                    : voice.playing ? "pause" : "play"
                color: Theme.textOnAccent
                size: 20
            }

            TapHandler {
                onTapped: voice.toggle(root.fileId, messages.chatId, root.messageId)
            }
        }

        Column {
            anchors.verticalCenter: parent.verticalCenter
            spacing: 4

            Row {
                id: bars
                height: 22
                spacing: 2

                Repeater {
                    model: root.waveform
                    Rectangle {
                        required property real modelData
                        required property int index
                        anchors.verticalCenter: parent.verticalCenter
                        width: 2
                        height: Math.max(3, modelData * bars.height)
                        radius: 1
                        color: root.isCurrentVoice
                               && index / root.waveform.length < voice.progress
                               ? Theme.accent : Theme.textMuted
                        opacity: color === Theme.accent ? 1 : 0.6
                    }
                }

                TapHandler {
                    onTapped: point => {
                        if (root.isCurrentVoice)
                            voice.seek(point.position.x / bars.width)
                    }
                }
            }

            Text {
                text: root.isCurrentVoice && (voice.playing || voice.progress > 0)
                      ? root.clock(voice.positionSeconds) : root.duration
                color: Theme.textMuted
                font.pixelSize: 11
            }
        }
    }
}
