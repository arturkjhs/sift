import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// The open chat's scheduled messages: send now, reschedule, delete.
Popup {
    id: root
    objectName: "scheduledList"
    signal rescheduleRequested(var messageId)

    function show() {
        messages.loadScheduled()
        open()
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(440, parent ? parent.width - 48 : 440)
    height: Math.min(list.contentHeight + 90, parent ? parent.height - 80 : 500)
    modal: true
    padding: 16
    background: Rectangle {
        radius: 12
        color: Theme.sidebar
        border.width: 1
        border.color: Theme.separator
    }

    contentItem: ColumnLayout {
        spacing: 8
        RowLayout {
            Layout.fillWidth: true
            Text {
                Layout.fillWidth: true
                text: qsTr("Scheduled messages")
                color: Theme.text
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }
            IconButton {
                iconName: "close"
                glyphSize: 12
                Accessible.name: qsTr("Close")
                onClicked: root.close()
            }
        }
        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: messages.scheduled
            spacing: 4
            delegate: Rectangle {
                id: row
                required property var modelData
                width: ListView.view.width
                height: 64
                radius: 8
                color: Theme.bubbleOut
                Column {
                    x: 10
                    y: 8
                    width: parent.width - actions.width - 24
                    Text {
                        text: row.modelData.when
                        color: Theme.accent
                        font.pixelSize: Theme.fontSmall
                        font.weight: Font.DemiBold
                    }
                    Text {
                        width: parent.width
                        text: row.modelData.text
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        maximumLineCount: 2
                        wrapMode: Text.Wrap
                        color: Theme.text
                        font.pixelSize: Theme.fontBody
                    }
                }
                Row {
                    id: actions
                    anchors.right: parent.right
                    anchors.rightMargin: 6
                    anchors.verticalCenter: parent.verticalCenter
                    IconButton {
                        iconName: "forward"
                        Accessible.name: qsTr("Send now")
                        ToolTip.visible: hovered
                        ToolTip.text: Accessible.name
                        onClicked: messages.sendScheduledNow(row.modelData.messageId)
                    }
                    IconButton {
                        iconName: "clock"
                        Accessible.name: qsTr("Reschedule")
                        ToolTip.visible: hovered
                        ToolTip.text: Accessible.name
                        onClicked: root.rescheduleRequested(row.modelData.messageId)
                    }
                    IconButton {
                        iconName: "trash"
                        Accessible.name: qsTr("Delete")
                        ToolTip.visible: hovered
                        ToolTip.text: Accessible.name
                        onClicked: messages.deleteScheduled(row.modelData.messageId)
                    }
                }
            }
            Text {
                anchors.centerIn: parent
                visible: list.count === 0
                text: qsTr("Nothing scheduled")
                color: Theme.textMuted
                font.pixelSize: Theme.fontBody
            }
        }
    }
}
