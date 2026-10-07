import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// A question before something that can't be undone; optionally with one switch
// ("also for everyone"). ask() fills it in, accepted(checked) answers.
Popup {
    id: root
    property string title: ""
    property string text: ""
    property string option: ""        // the switch's label; "" = no switch
    property string confirmText: qsTr("OK")
    property bool danger: true
    property var payload: null        // whatever the caller needs back

    signal accepted(bool checked, var payload)

    function ask(title, text, confirmText, option, payload) {
        root.title = title
        root.text = text || ""
        root.confirmText = confirmText
        root.option = option || ""
        root.payload = payload
        optionSwitch.checked = false
        open()
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(380, parent ? parent.width - 48 : 380)
    modal: true
    padding: 20
    background: Rectangle {
        radius: 12
        color: Theme.sidebar
        border.width: 1
        border.color: Theme.separator
    }

    contentItem: ColumnLayout {
        spacing: 14
        Text {
            Layout.fillWidth: true
            text: root.title
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
            color: Theme.text
            font.pixelSize: 15
            font.weight: Font.DemiBold
        }
        Text {
            Layout.fillWidth: true
            visible: root.text !== ""
            text: root.text
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
            color: Theme.textMuted
            font.pixelSize: Theme.fontBody
        }
        RowLayout {
            Layout.fillWidth: true
            visible: root.option !== ""
            spacing: 10
            ToggleSwitch { id: optionSwitch }
            Text {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: root.option
                color: Theme.text
                font.pixelSize: Theme.fontBody
            }
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
                objectName: "confirmButton"
                text: root.confirmText
                filled: true
                danger: root.danger
                onClicked: {
                    root.close()
                    root.accepted(optionSwitch.checked, root.payload)
                }
            }
        }
    }
}
