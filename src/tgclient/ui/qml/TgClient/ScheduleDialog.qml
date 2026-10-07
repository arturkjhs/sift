import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// When to send: quick choices or a date and time. picked(unix time).
Popup {
    id: root
    objectName: "scheduleDialog"
    property var messageId: 0   // rescheduling an existing message; 0 = the composer's text
    signal picked(int sendDate, var messageId)

    function ask(messageId) {
        root.messageId = messageId || 0
        const soon = new Date(Date.now() + 3600 * 1000)
        dateField.text = Qt.formatDate(soon, "yyyy-MM-dd")
        timeField.text = Qt.formatTime(soon, "HH:mm")
        error.text = ""
        open()
    }

    function at(days, hours, minutes) {
        const d = new Date()
        d.setDate(d.getDate() + days)
        d.setHours(hours, minutes, 0, 0)
        return Math.floor(d.getTime() / 1000)
    }

    function choose(unix) {
        if (unix <= Date.now() / 1000 + 30) {
            error.text = qsTr("Pick a time in the future")
            return
        }
        root.close()
        root.picked(unix, root.messageId)
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(360, parent ? parent.width - 48 : 360)
    modal: true
    padding: 20
    background: Rectangle {
        radius: 12
        color: Theme.sidebar
        border.width: 1
        border.color: Theme.separator
    }

    component Field: TextField {
        Layout.fillWidth: true
        color: Theme.text
        font.pixelSize: Theme.fontBody
        padding: 8
        background: Rectangle {
            radius: 8
            color: Theme.field
            border.width: 1
            border.color: parent.activeFocus ? Theme.accent : Theme.fieldBorder
        }
    }

    contentItem: ColumnLayout {
        spacing: 12
        Text {
            text: root.messageId ? qsTr("Reschedule") : qsTr("Schedule message")
            color: Theme.text
            font.pixelSize: 15
            font.weight: Font.DemiBold
        }
        Flow {
            Layout.fillWidth: true
            spacing: 6
            PillButton {
                text: qsTr("In 1 hour")
                onClicked: root.choose(Math.floor(Date.now() / 1000) + 3600)
            }
            PillButton {
                text: qsTr("This evening")
                visible: new Date().getHours() < 18
                onClicked: root.choose(root.at(0, 19, 0))
            }
            PillButton {
                text: qsTr("Tomorrow 9:00")
                onClicked: root.choose(root.at(1, 9, 0))
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Field {
                id: dateField
                objectName: "scheduleDate"
                placeholderText: "2026-10-08"
            }
            Field {
                id: timeField
                objectName: "scheduleTime"
                Layout.preferredWidth: 90
                Layout.fillWidth: false
                placeholderText: "18:30"
            }
        }
        Text {
            id: error
            visible: text !== ""
            color: Theme.danger
            font.pixelSize: Theme.fontSmall
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Item { Layout.fillWidth: true }
            PillButton {
                text: qsTr("Cancel")
                onClicked: root.close()
            }
            PillButton {
                objectName: "scheduleConfirm"
                text: qsTr("Schedule")
                filled: true
                onClicked: {
                    const when = new Date(dateField.text + "T" + timeField.text + ":00")
                    if (isNaN(when.getTime())) {
                        error.text = qsTr("Use a date like 2026-10-08 and a time like 18:30")
                        return
                    }
                    root.choose(Math.floor(when.getTime() / 1000))
                }
            }
        }
    }
}
