import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts

// Files about to be sent (file dialog, drag-and-drop, paste): review, add a caption, send.
Popup {
    id: root
    objectName: "sendFilesDialog"
    property var replyToId: 0
    readonly property var files: composerModel.staged

    signal sent()

    function send() {
        composerModel.sendStaged(caption.text, root.replyToId)
        caption.clear()
        root.close()
        root.sent()
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(440, parent.width - 48)
    modal: true
    padding: 18
    closePolicy: Popup.CloseOnEscape
    onOpened: caption.forceActiveFocus()
    onClosed: {
        composerModel.clearStaged()
        caption.clear()
    }

    // Opens when something is staged, closes when the last file is removed.
    Connections {
        target: composerModel
        function onStagedChanged() {
            if (root.files.length > 0 && !root.opened)
                root.open()
            else if (root.files.length === 0 && root.opened)
                root.close()
        }
    }

    background: Rectangle {
        radius: 12
        color: Theme.sidebar
        border.width: 1
        border.color: Theme.separator
    }

    contentItem: ColumnLayout {
        spacing: 12

        Text {
            Layout.fillWidth: true
            text: root.files.length === 1 ? qsTr("Send 1 file")
                                          : qsTr("Send %1 files").arg(root.files.length)
            color: Theme.text
            font.pixelSize: 16
            font.weight: Font.DemiBold
        }

        ListView {
            id: fileList
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(contentHeight, 300)
            clip: true
            spacing: 6
            boundsBehavior: Flickable.StopAtBounds
            model: root.files
            ScrollBar.vertical: ScrollBar {}

            delegate: RowLayout {
                id: fileRow
                required property var modelData
                required property int index
                width: ListView.view.width
                spacing: 10

                Rectangle {
                    Layout.preferredWidth: 48
                    Layout.preferredHeight: 48
                    radius: 8
                    color: Theme.pill
                    clip: true

                    Image {
                        anchors.fill: parent
                        visible: fileRow.modelData.isImage
                        source: fileRow.modelData.source
                        sourceSize.width: 96
                        sourceSize.height: 96
                        fillMode: Image.PreserveAspectCrop
                        asynchronous: true
                    }
                    Icon {
                        anchors.centerIn: parent
                        visible: !fileRow.modelData.isImage
                        name: "file"
                        color: Theme.accent
                        size: 22
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Text {
                        Layout.fillWidth: true
                        text: fileRow.modelData.name
                        textFormat: Text.PlainText
                        elide: Text.ElideMiddle
                        color: Theme.text
                        font.pixelSize: Theme.fontBody
                    }
                    Text {
                        text: (fileRow.modelData.isImage ? qsTr("Photo") : qsTr("File"))
                              + " · " + fileRow.modelData.size
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSmall
                    }
                }
                IconButton {
                    iconName: "close"
                    glyphSize: 11
                    Accessible.name: qsTr("Remove")
                    onClicked: composerModel.unstage(fileRow.index)
                }
            }
        }

        TextArea {
            id: caption
            objectName: "captionInput"
            Layout.fillWidth: true
            placeholderText: qsTr("Add a caption")
            wrapMode: TextArea.Wrap
            color: Theme.text
            placeholderTextColor: Theme.textMuted
            font.pixelSize: Theme.fontTitle
            padding: 10
            background: Rectangle {
                radius: 10
                color: Theme.field
                border.width: 1
                border.color: caption.activeFocus ? Theme.accent : Theme.fieldBorder
            }
            Keys.onPressed: event => {
                if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                        && !(event.modifiers & Qt.ShiftModifier)) {
                    root.send()
                    event.accepted = true
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            PillButton {
                text: qsTr("Add files")
                iconName: "attach"
                onClicked: addDialog.open()
            }
            Item { Layout.fillWidth: true }
            PillButton {
                text: qsTr("Cancel")
                onClicked: root.close()
            }
            PillButton {
                objectName: "sendFilesButton"
                text: qsTr("Send")
                filled: true
                onClicked: root.send()
            }
        }
    }

    FileDialog {
        id: addDialog
        title: qsTr("Add files")
        fileMode: FileDialog.OpenFiles
        onAccepted: composerModel.stage(selectedFiles)
    }
}
