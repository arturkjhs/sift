import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: root

    readonly property var titles: ({
        loading: qsTr("Connecting"),
        phone: qsTr("Sign in"),
        code: qsTr("Enter the code"),
        password: qsTr("Enter your password"),
        email: qsTr("Add an email"),
        emailCode: qsTr("Check your email"),
        link: qsTr("Confirm on another device"),
        failed: qsTr("Sign-in stopped")
    })
    readonly property var placeholders: ({
        phone: "+420 123 456 789",
        code: qsTr("Code"),
        password: qsTr("Password"),
        email: "you@example.com",
        emailCode: qsTr("Code")
    })
    readonly property bool asksInput: placeholders[auth.step] !== undefined
    property string shownStep: ""

    function submit() {
        if (submitButton.enabled)
            auth.submit(input.text)
    }

    Connections {
        target: auth
        function onChanged() {
            if (auth.step !== root.shownStep) {
                root.shownStep = auth.step
                input.text = ""
                input.forceActiveFocus()
            }
        }
    }

    ColumnLayout {
        anchors.centerIn: parent
        width: Math.min(root.width - 48, 340)
        spacing: 12

        Label {
            Layout.fillWidth: true
            text: root.titles[auth.step] || ""
            color: Theme.text
            font.pixelSize: 22
            font.weight: Font.DemiBold
            wrapMode: Text.Wrap
        }

        Label {
            Layout.fillWidth: true
            visible: text.length > 0
            text: auth.hint
            color: Theme.textMuted
            font.pixelSize: Theme.fontTitle
            wrapMode: Text.Wrap
        }

        TextEdit {
            Layout.fillWidth: true
            visible: auth.step === "link"
            text: auth.link
            readOnly: true
            selectByMouse: true
            wrapMode: TextEdit.WrapAnywhere
            color: Theme.text
            font.pixelSize: Theme.fontBody
        }

        TextField {
            id: input
            Layout.fillWidth: true
            visible: root.asksInput
            enabled: !auth.busy
            focus: true
            placeholderText: root.placeholders[auth.step] || ""
            echoMode: auth.step === "password" ? TextInput.Password : TextInput.Normal
            inputMethodHints: auth.step === "phone" ? Qt.ImhDialableCharactersOnly
                            : auth.step === "code" || auth.step === "emailCode" ? Qt.ImhDigitsOnly
                            : auth.step === "email" ? Qt.ImhEmailCharactersOnly
                            : Qt.ImhNone
            color: Theme.text
            placeholderTextColor: Theme.textMuted
            font.pixelSize: 15
            padding: 10
            background: Rectangle {
                radius: 8
                color: Theme.field
                border.width: input.activeFocus ? 2 : 1
                border.color: input.activeFocus ? Theme.accent : Theme.fieldBorder
            }
            onAccepted: root.submit()
        }

        Label {
            Layout.fillWidth: true
            visible: text.length > 0
            text: auth.error
            color: Theme.danger
            font.pixelSize: Theme.fontBody
            wrapMode: Text.Wrap
        }

        Button {
            id: submitButton
            Layout.fillWidth: true
            visible: root.asksInput
            enabled: !auth.busy && input.text.trim().length > 0
            text: auth.busy ? qsTr("Checking") : qsTr("Continue")
            padding: 10
            contentItem: Text {
                text: submitButton.text
                color: Theme.textOnAccent
                font.pixelSize: Theme.fontTitle
                font.weight: Font.DemiBold
                horizontalAlignment: Text.AlignHCenter
            }
            background: Rectangle {
                radius: 8
                color: Theme.accent
                opacity: submitButton.enabled ? (submitButton.down ? 0.85 : 1) : 0.4
            }
            onClicked: root.submit()
        }

        BusyIndicator {
            Layout.alignment: Qt.AlignHCenter
            visible: running
            running: auth.step === "loading" || auth.busy
        }
    }
}
