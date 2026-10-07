import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts
import QtMultimedia

// Full-window viewer for photos and videos of the open chat (viewer: ViewerModel).
// ←/→ walk through the chat's media, Space plays/pauses, Esc closes; wheel or double-click zooms.
Popup {
    id: root
    objectName: "mediaViewer"

    readonly property bool isPhoto: viewer.kind === "photo"
    readonly property bool isVideo: !isPhoto && viewer.kind !== ""
    property real zoom: 1

    parent: Overlay.overlay
    x: 0
    y: 0
    width: parent ? parent.width : 800
    height: parent ? parent.height : 600
    modal: true
    padding: 0
    closePolicy: Popup.CloseOnEscape
    onClosed: viewer.close()
    onOpened: keys.forceActiveFocus()

    Connections {
        target: viewer
        function onChanged() {
            if (viewer.active && !root.opened)
                root.open()
            else if (!viewer.active && root.opened)
                root.close()
            root.zoom = 1
        }
    }

    background: Rectangle { color: "#F2101317" }

    contentItem: Item {
        id: keys
        focus: true
        Keys.onPressed: event => {
            if (event.key === Qt.Key_Right) { viewer.next(); event.accepted = true }
            else if (event.key === Qt.Key_Left) { viewer.prev(); event.accepted = true }
            else if (event.key === Qt.Key_Space && videoLoader.item) {
                videoLoader.item.toggle()
                event.accepted = true
            }
        }

        // --- photo (or the preview of a video that is still downloading) -------------------
        Flickable {
            id: flick
            anchors.fill: parent
            anchors.topMargin: 56
            anchors.bottomMargin: root.isVideo ? 64 : 24
            visible: root.isPhoto || !viewer.ready
            clip: true
            interactive: root.zoom > 1
            boundsBehavior: Flickable.StopAtBounds
            readonly property real naturalWidth: photo.implicitWidth > 0 ? photo.implicitWidth
                                                                        : viewer.mediaWidth
            readonly property real naturalHeight: photo.implicitHeight > 0 ? photo.implicitHeight
                                                                          : viewer.mediaHeight
            readonly property real fit: naturalWidth > 0 && naturalHeight > 0
                ? Math.min(width / naturalWidth, height / naturalHeight, 1) : 1
            contentWidth: Math.max(width, naturalWidth * fit * root.zoom)
            contentHeight: Math.max(height, naturalHeight * fit * root.zoom)

            Image {
                id: photo
                objectName: "viewerImage"
                width: flick.naturalWidth * flick.fit * root.zoom
                height: flick.naturalHeight * flick.fit * root.zoom
                x: (flick.contentWidth - width) / 2
                y: (flick.contentHeight - height) / 2
                source: root.isPhoto && viewer.ready ? viewer.source : viewer.preview
                asynchronous: true
                autoTransform: true
                smooth: true
                mipmap: true
            }

            WheelHandler {
                acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
                enabled: root.isPhoto
                onWheel: event => {
                    const factor = event.angleDelta.y > 0 ? 1.15 : 1 / 1.15
                    root.zoom = Math.max(1, Math.min(6, root.zoom * factor))
                }
            }
            TapHandler {
                onDoubleTapped: if (root.isPhoto) root.zoom = root.zoom > 1 ? 1 : 2.5
                onTapped: point => {  // a click on the dark area around the picture closes
                    const p = point.position
                    if (p.x < photo.x - flick.contentX || p.x > photo.x + photo.width - flick.contentX
                            || p.y < photo.y - flick.contentY
                            || p.y > photo.y + photo.height - flick.contentY)
                        root.close()
                }
            }
        }

        // --- video, GIF, round video --------------------------------------------------------
        Loader {
            id: videoLoader
            anchors.fill: parent
            anchors.topMargin: 56
            active: root.isVideo && viewer.ready && root.opened
            sourceComponent: Item {
                function toggle() {
                    player.playbackState === MediaPlayer.PlayingState ? player.pause() : player.play()
                }

                VideoOutput {
                    id: output
                    anchors.fill: parent
                    anchors.bottomMargin: 64
                    TapHandler { onTapped: parent.parent.toggle() }
                }
                AudioOutput {
                    id: audio
                    muted: viewer.kind === "animation"
                }
                MediaPlayer {
                    id: player
                    source: viewer.source
                    videoOutput: output
                    audioOutput: audio
                    loops: viewer.kind === "animation" ? MediaPlayer.Infinite : 1
                    Component.onCompleted: play()
                }

                RowLayout {  // controls
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.margins: 16
                    height: 32
                    spacing: 12

                    Icon {
                        name: player.playbackState === MediaPlayer.PlayingState ? "pause" : "play"
                        color: "#FFFFFF"
                        size: 20
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: parent.parent.parent.toggle() }
                    }
                    Slider {
                        id: seek
                        Layout.fillWidth: true
                        from: 0
                        to: Math.max(1, player.duration)
                        value: player.position
                        enabled: player.seekable
                        onMoved: player.position = value
                    }
                    Text {
                        text: root.clock(player.position) + " / " + root.clock(player.duration)
                        color: "#C8CDD3"
                        font.pixelSize: 12
                    }
                    Text {
                        visible: viewer.kind !== "animation"
                        text: audio.muted ? qsTr("Unmute") : qsTr("Mute")
                        color: "#C8CDD3"
                        font.pixelSize: 12
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: audio.muted = !audio.muted }
                    }
                }
            }
        }

        // Download progress of the full file.
        Rectangle {
            anchors.centerIn: parent
            visible: !viewer.ready && viewer.active
            width: 64
            height: 64
            radius: 32
            color: "#99000000"
            Text {
                anchors.centerIn: parent
                text: Math.round(viewer.progress * 100) + "%"
                color: "#FFFFFF"
                font.pixelSize: 14
                font.weight: Font.DemiBold
            }
        }

        // --- top bar ------------------------------------------------------------------------
        RowLayout {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 12
            height: 32
            spacing: 6

            Column {
                Layout.fillWidth: true
                Text {
                    text: viewer.sender
                    textFormat: Text.PlainText
                    color: "#FFFFFF"
                    font.pixelSize: Theme.fontTitle
                    font.weight: Font.DemiBold
                }
                Text {
                    text: viewer.date + (viewer.count > 1
                          ? "  ·  " + qsTr("%1 of %2").arg(viewer.index + 1).arg(viewer.count)
                          : "")
                    color: "#A9B0B8"
                    font.pixelSize: Theme.fontSmall
                }
            }
            ViewerButton {
                iconName: "arrow-down"
                Accessible.name: qsTr("Save as…")
                enabled: viewer.ready
                onClicked: {
                    saveDialog.currentFile = saveDialog.currentFolder + "/" + viewer.suggestedName
                    saveDialog.open()
                }
            }
            ViewerButton {
                iconName: "folder"
                Accessible.name: qsTr("Show in folder")
                enabled: viewer.ready
                onClicked: viewer.showInFolder()
            }
            ViewerButton {
                iconName: "open"
                Accessible.name: qsTr("Open in another app")
                enabled: viewer.ready
                onClicked: viewer.openExternally()
            }
            ViewerButton {
                iconName: "close"
                Accessible.name: qsTr("Close")
                onClicked: root.close()
            }
        }

        // --- previous / next --------------------------------------------------------------
        ViewerButton {
            anchors.left: parent.left
            anchors.leftMargin: 12
            anchors.verticalCenter: parent.verticalCenter
            visible: viewer.index > 0
            iconName: "reply"
            size: 44
            Accessible.name: qsTr("Previous")
            onClicked: viewer.prev()
        }
        ViewerButton {
            anchors.right: parent.right
            anchors.rightMargin: 12
            anchors.verticalCenter: parent.verticalCenter
            visible: viewer.index < viewer.count - 1
            iconName: "forward"
            size: 44
            Accessible.name: qsTr("Next")
            onClicked: viewer.next()
        }

        // --- caption ----------------------------------------------------------------------
        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            anchors.bottomMargin: root.isVideo && viewer.ready ? 64 : 16
            visible: viewer.caption !== ""
            width: Math.min(parent.width - 120, captionText.implicitWidth + 28)
            height: captionText.height + 16
            radius: 10
            color: "#B3000000"
            Text {
                id: captionText
                anchors.centerIn: parent
                width: parent.width - 28
                text: viewer.caption
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                maximumLineCount: 4
                elide: Text.ElideRight
                horizontalAlignment: Text.AlignHCenter
                color: "#FFFFFF"
                font.pixelSize: Theme.fontBody
            }
        }
    }

    function clock(ms) {
        const total = Math.floor(ms / 1000), m = Math.floor(total / 60), s = total % 60
        return m + ":" + (s < 10 ? "0" : "") + s
    }

    component ViewerButton: AbstractButton {
        id: button
        property string iconName: ""
        property int size: 34
        implicitWidth: size
        implicitHeight: size
        hoverEnabled: true
        opacity: enabled ? 1 : 0.4
        AppToolTip {
            visible: parent.hovered && parent.Accessible.name !== ""
            text: parent.Accessible.name
            delay: 600
        }
        background: Rectangle {
            radius: width / 2
            color: button.hovered ? "#33FFFFFF" : "#1AFFFFFF"
        }
        contentItem: Item {
            Icon {
                anchors.centerIn: parent
                name: button.iconName
                color: "#FFFFFF"
                size: Math.round(button.size * 0.5)
            }
        }
    }

    FileDialog {
        id: saveDialog
        title: qsTr("Save as")
        fileMode: FileDialog.SaveFile
        onAccepted: viewer.saveAs(selectedFile)
    }
}
