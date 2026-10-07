import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Covers everything until the passcode is entered (at launch, after auto-lock or "Lock now").
Rectangle {
    id: root
    objectName: "lockScreen"
    color: Theme.window

    function focusInput() { passcodeField.forceActiveFocus() }

    function tryUnlock() {
        if (lock.unlock(passcodeField.text)) {
            passcodeField.text = ""
            errorText.text = ""
        } else {
            errorText.text = qsTr("Wrong passcode")
            passcodeField.selectAll()
            shake.restart()
        }
    }

    onVisibleChanged: if (visible) focusInput()
    Component.onCompleted: focusInput()

    MouseArea { anchors.fill: parent }   // nothing underneath is clickable

    ColumnLayout {
        id: content
        anchors.centerIn: parent
        width: Math.min(300, root.width - 48)
        spacing: 12

        Rectangle {
            Layout.alignment: Qt.AlignHCenter
            width: 64
            height: 64
            radius: 32
            color: Theme.accent
            Text {
                anchors.centerIn: parent
                text: "🔒"
                font.pixelSize: 28
            }
        }
        Text {
            Layout.alignment: Qt.AlignHCenter
            text: qsTr("Enter your passcode")
            color: Theme.text
            font.pixelSize: 18
            font.weight: Font.DemiBold
        }
        TextField {
            id: passcodeField
            objectName: "passcodeField"
            Layout.fillWidth: true
            echoMode: TextInput.Password
            placeholderText: qsTr("Passcode")
            color: Theme.text
            placeholderTextColor: Theme.textMuted
            font.pixelSize: Theme.fontTitle
            padding: 10
            background: Rectangle {
                radius: 10
                color: Theme.field
                border.width: 1
                border.color: passcodeField.activeFocus ? Theme.accent : Theme.fieldBorder
            }
            onAccepted: root.tryUnlock()
            transform: Translate { id: nudge }
            SequentialAnimation {
                id: shake
                NumberAnimation { target: nudge; property: "x"; to: -8; duration: 50 }
                NumberAnimation { target: nudge; property: "x"; to: 8; duration: 70 }
                NumberAnimation { target: nudge; property: "x"; to: 0; duration: 50 }
            }
        }
        Text {
            id: errorText
            objectName: "lockError"
            Layout.alignment: Qt.AlignHCenter
            visible: text !== ""
            color: Theme.danger
            font.pixelSize: Theme.fontSmall
        }
        PillButton {
            Layout.alignment: Qt.AlignHCenter
            objectName: "unlockButton"
            text: qsTr("Unlock")
            filled: true
            onClicked: root.tryUnlock()
        }
    }
}
